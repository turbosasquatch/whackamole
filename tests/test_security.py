from fastapi.testclient import TestClient

from app.config import SecretStore
from app.main import app
from app.path_security import safe_join_media
from app.security import AuthSettings, get_bound_secret, route_policy, set_bound_secret, validate_service_url


API_TOKEN = "security-test-api-token-with-at-least-32-characters"
PASSWORD = "correct horse battery staple"


def _raw_client(tmp_path, monkeypatch, **environment):
    monkeypatch.setenv("WHACKAMOLE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("WHACKAMOLE_API_TOKEN", API_TOKEN)
    monkeypatch.delenv("WHACKAMOLE_UI_BYPASS_CIDRS", raising=False)
    monkeypatch.delenv("WHACKAMOLE_ALLOWED_ORIGINS", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    return TestClient(app)


def _setup(client):
    return client.post(
        "/setup",
        data={
            "api_token": API_TOKEN,
            "username": "admin",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )


def _login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": PASSWORD, "next": "/"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )


def test_existing_api_token_bootstraps_admin_and_setup_closes(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
        response = _setup(client)
        assert response.status_code == 303
        assert client.app.state.db.get_admin_account()["username"] == "admin"
        assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_password_session_protects_ui_and_bearer_api_remains_separate(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        _setup(client)
        assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
        login = _login(client)
        assert login.status_code == 303
        assert client.get("/").status_code == 200
        assert client.get("/api/items").status_code == 401
        assert client.get("/api/items", headers={"Authorization": f"Bearer {API_TOKEN}"}).status_code == 200


def test_csrf_is_required_for_cookie_authenticated_changes(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        _setup(client)
        _login(client)
        rejected = client.post("/maintenance/pause", data={"return_to": "/"}, headers={"Origin": "http://testserver"})
        assert rejected.status_code == 403
        csrf = client.cookies.get("whackamole_csrf")
        accepted = client.post(
            "/maintenance/pause",
            data={"return_to": "/"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert accepted.status_code == 303


def test_public_status_is_minimal(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        payload = client.get("/api/status").json()
        assert set(payload) == {"status", "service_running"}


def test_host_validation_and_forwarded_headers_do_not_grant_bypass(tmp_path, monkeypatch):
    with _raw_client(
        tmp_path,
        monkeypatch,
        WHACKAMOLE_UI_BYPASS_CIDRS="192.168.1.0/24",
        WHACKAMOLE_ALLOWED_ORIGINS="http://testserver",
    ) as client:
        _setup(client)
        assert client.get("/", headers={"X-Forwarded-For": "192.168.1.50"}, follow_redirects=False).status_code == 303
        assert client.get("/", headers={"Host": "evil.example"}, follow_redirects=False).status_code == 400


def test_bypass_cidr_accepts_private_subnet_and_rejects_wildcard(monkeypatch):
    monkeypatch.setenv("WHACKAMOLE_UI_BYPASS_CIDRS", "192.168.1.0/24")
    monkeypatch.setenv("WHACKAMOLE_ALLOWED_ORIGINS", "http://192.168.1.16:9393")
    settings = AuthSettings.from_environment()
    assert settings.bypasses("192.168.1.50")
    assert not settings.bypasses("192.168.2.50")
    monkeypatch.setenv("WHACKAMOLE_UI_BYPASS_CIDRS", "0.0.0.0/0")
    try:
        AuthSettings.from_environment()
    except ValueError as exc:
        assert "Wildcard" in str(exc)
    else:
        raise AssertionError("Wildcard bypass network should be rejected")


def test_setup_persists_local_bypass_for_existing_container(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/setup",
            data={
                "api_token": API_TOKEN,
                "username": "admin",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
                "ui_bypass_cidrs": "192.168.1.0/24",
                "allowed_origins": "http://192.168.1.16:9393",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.app.state.auth.settings.bypasses("192.168.1.50")
        assert client.app.state.db.get_kv("security_allowed_origins") == "http://192.168.1.16:9393"


def test_service_credentials_are_bound_to_origin(tmp_path):
    store = SecretStore(str(tmp_path))
    set_bound_secret(store, "sonarr_api_key", "secret", "http://192.168.1.16:8989")
    assert get_bound_secret(store, "sonarr_api_key", "http://192.168.1.16:8989") == "secret"
    assert get_bound_secret(store, "sonarr_api_key", "http://attacker.example:8989") is None


def test_config_rejects_service_secret_without_url_before_mutating(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        _setup(client)
        _login(client)
        response = client.post(
            "/config",
            data={"sonarr_api_key": "must-not-be-saved"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("whackamole_csrf")},
        )
        assert response.status_code == 400
        assert client.app.state.secrets.get("sonarr_api_key") is None


def test_service_url_and_media_path_guards(monkeypatch, tmp_path):
    try:
        validate_service_url("http://169.254.169.254/latest/meta-data")
    except ValueError:
        pass
    else:
        raise AssertionError("Link-local metadata destination should be rejected")
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", str(media_root))
    assert safe_join_media(str(media_root), "show/episode.mkv").is_relative_to(media_root)
    try:
        safe_join_media(str(media_root), "../../config/secrets.yaml")
    except ValueError:
        pass
    else:
        raise AssertionError("Traversal should be rejected")


def test_every_registered_route_has_a_deny_by_default_policy():
    allowed = {"public", "ui", "browser_api", "bearer_api"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path:
            assert route_policy(path) in allowed
    assert route_policy("/new-unclassified-route") == "ui"

import time

from fastapi.testclient import TestClient

from app.config import SecretStore
from app.main import app
from app.path_security import allowed_media_roots, safe_join_media, validate_media_path
from app.security import AuthSettings, SESSION_TTL_SECONDS, get_bound_secret, route_policy, set_bound_secret, validate_service_url


API_TOKEN = "security-test-api-token-with-at-least-32-characters"
PASSWORD = "correct horse battery staple"
THIRTY_DAYS = 30 * 24 * 60 * 60


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


def test_password_session_has_fixed_thirty_day_expiry(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        _setup(client)
        login = _login(client)
        assert login.status_code == 303
        assert SESSION_TTL_SECONDS == THIRTY_DAYS
        assert any("Max-Age=2592000" in value for value in login.headers.get_list("set-cookie"))
        with client.app.state.db.connect() as conn:
            before = conn.execute("SELECT created_at, expires_at FROM auth_sessions").fetchone()
        assert int(before["expires_at"]) - int(before["created_at"]) == THIRTY_DAYS

        assert client.get("/").status_code == 200
        with client.app.state.db.connect() as conn:
            after = conn.execute("SELECT created_at, expires_at FROM auth_sessions").fetchone()
        assert int(after["expires_at"]) == int(before["expires_at"])


def test_active_legacy_sessions_extend_without_reviving_expired_sessions(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        now = int(time.time())
        db = client.app.state.db
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions(token_hash, username, auth_version, auth_method, client_ip, csrf_hash, created_at, expires_at)
                VALUES(?, 'admin', 1, 'password', '', 'csrf', ?, ?)
                """,
                ("active-legacy", now - 60, now + 60),
            )
            conn.execute(
                """
                INSERT INTO auth_sessions(token_hash, username, auth_version, auth_method, client_ip, csrf_hash, created_at, expires_at)
                VALUES(?, 'admin', 1, 'password', '', 'csrf', ?, ?)
                """,
                ("expired-legacy", now - 120, now - 60),
            )

        updated = db.extend_active_auth_sessions(THIRTY_DAYS, now=now)

        with db.connect() as conn:
            active = conn.execute("SELECT created_at, expires_at FROM auth_sessions WHERE token_hash = 'active-legacy'").fetchone()
            expired = conn.execute("SELECT created_at, expires_at FROM auth_sessions WHERE token_hash = 'expired-legacy'").fetchone()
        assert updated == 1
        assert int(active["expires_at"]) == int(active["created_at"]) + THIRTY_DAYS
        assert int(expired["expires_at"]) == now - 60


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


def test_logout_does_not_reissue_deleted_session_cookies(tmp_path, monkeypatch):
    with _raw_client(tmp_path, monkeypatch) as client:
        _setup(client)
        _login(client)
        response = client.post(
            "/logout",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": client.cookies.get("whackamole_csrf"),
            },
            follow_redirects=False,
        )

        cookies = response.headers.get_list("set-cookie")
        assert response.status_code == 303
        assert any("whackamole_session=" in value and "Max-Age=0" in value for value in cookies)
        assert not any("whackamole_session=" in value and "Max-Age=2592000" in value for value in cookies)
        with client.app.state.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


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


def test_default_media_roots_include_legacy_read_only_mount(monkeypatch):
    for value in (None, ""):
        if value is None:
            monkeypatch.delenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", raising=False)
        else:
            monkeypatch.setenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", value)
        assert "/media/torrents" in {str(root) for root in allowed_media_roots()}
        assert str(validate_media_path("/media/torrents/show/episode.mkv")).startswith("/media/torrents/")


def test_every_registered_route_has_a_deny_by_default_policy():
    allowed = {"public", "ui", "browser_api", "bearer_api"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path:
            assert route_policy(path) in allowed
    assert route_policy("/new-unclassified-route") == "ui"

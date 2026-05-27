from app.config import SecretStore


def test_secret_store_encrypts_and_reads_values(tmp_path):
    store = SecretStore(str(tmp_path))

    store.set("token", "super-secret")

    assert store.has("token")
    assert store.get("token") == "super-secret"
    assert "super-secret" not in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")


def test_secret_store_can_clear_values(tmp_path):
    store = SecretStore(str(tmp_path))
    store.set("token", "super-secret")

    store.clear("token")

    assert not store.has("token")
    assert store.get("token") is None

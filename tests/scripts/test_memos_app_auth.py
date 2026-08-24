from __future__ import annotations

import importlib.util
import sys

from pathlib import Path

from fastapi.testclient import TestClient


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_password_hash_and_session_are_compatible_with_previous_frontend_format(monkeypatch):
    auth = _load_script("memos_app_auth")
    password = "correct horse battery staple"
    password_hash = auth.create_password_hash(password, salt=bytes([7]) * 16)
    monkeypatch.setenv("MEMOS_ACCESS_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("MEMOS_SESSION_SECRET", "test-session-secret-that-is-long-enough")

    assert password_hash.startswith("pbkdf2_sha256:210000:")
    assert auth.verify_access_password(password) is True
    assert auth.verify_access_password("wrong password") is False

    now_ms = 1_777_777_000_000
    token = auth.create_session_token(now_ms=now_ms, ttl_ms=60_000, nonce=bytes([9]) * 16)
    assert auth.verify_session_token(token, now_ms=now_ms + 30_000) is True
    assert auth.verify_session_token(token, now_ms=now_ms + 60_001) is False
    assert auth.verify_session_token(f"{token}tampered", now_ms=now_ms + 30_000) is False


def test_auth_configuration_is_written_to_memos_env_without_duplicate_keys(tmp_path: Path):
    auth = _load_script("memos_app_auth")
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=value\nMEMOS_SESSION_SECRET=old\n", encoding="utf-8")

    auth.write_auth_configuration(
        env_path,
        password="correct horse battery staple",
        session_secret="new-session-secret-that-is-long-enough",
    )

    configured = env_path.read_text(encoding="utf-8")
    assert "EXISTING=value" in configured
    assert configured.count("MEMOS_ACCESS_PASSWORD_HASH=") == 1
    assert configured.count("MEMOS_SESSION_SECRET=") == 1
    assert "MEMOS_SESSION_SECRET=new-session-secret-that-is-long-enough" in configured
    assert "correct horse battery staple" not in configured


def test_v1_login_cookie_protects_application_api(monkeypatch, tmp_path: Path):
    auth = _load_script("memos_app_auth")
    api = _load_script("memos_frontend_api")
    password = "correct horse battery staple"
    monkeypatch.setenv(
        "MEMOS_ACCESS_PASSWORD_HASH",
        auth.create_password_hash(password, salt=bytes([5]) * 16),
    )
    monkeypatch.setenv("MEMOS_SESSION_SECRET", "test-session-secret-that-is-long-enough")
    monkeypatch.setenv("MEMOS_AUTH_COOKIE_SECURE", "false")

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def health(self):
            return {"status": "healthy"}

    app = api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
        auth_required=True,
    )
    client = TestClient(app)

    assert client.get("/api/v1/topics").status_code == 401
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401

    login = client.post("/api/v1/auth/login", json={"password": password})
    assert login.status_code == 200
    assert login.json() == {"ok": True}
    assert "memos_session=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert client.get("/api/v1/auth/session").json() == {"authenticated": True}
    assert client.get("/api/v1/topics").status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/v1/topics").status_code == 401


def test_mobile_login_returns_bearer_token(monkeypatch, tmp_path: Path):
    auth = _load_script("memos_app_auth")
    api = _load_script("memos_frontend_api")
    password = "correct horse battery staple"
    monkeypatch.setenv(
        "MEMOS_ACCESS_PASSWORD_HASH",
        auth.create_password_hash(password, salt=bytes([3]) * 16),
    )
    monkeypatch.setenv("MEMOS_SESSION_SECRET", "test-session-secret-that-is-long-enough")

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    app = api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: None,
        upload_dir=tmp_path / "uploads",
        auth_required=True,
    )
    client = TestClient(app)

    login = client.post("/api/v1/auth/mobile/login", json={"password": password})
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 7 * 24 * 60 * 60

    response = client.get(
        "/api/v1/topics",
        headers={"Authorization": f"Bearer {body['session_token']}"},
    )
    assert response.status_code == 200

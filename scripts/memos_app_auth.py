#!/usr/bin/env python3
"""Password and signed-session helpers for the MemOS application API."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import os
import secrets
import time

from pathlib import Path

from memos.log import get_logger


logger = get_logger(__name__)


PASSWORD_PREFIX = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000
SESSION_VERSION = "v1"
SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000
LOCAL_COOKIE_NAME = "memos_session"
SECURE_COOKIE_NAME = "__Host-memos_session"


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_password_hash(password: str, *, salt: bytes | None = None) -> str:
    """Create a hash compatible with the former TypeScript frontend auth."""
    if len(password) < 12:
        raise ValueError("访问密码至少需要 12 个字符。")
    resolved_salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt,
        PASSWORD_ITERATIONS,
        dklen=32,
    )
    return ":".join(
        (
            PASSWORD_PREFIX,
            str(PASSWORD_ITERATIONS),
            _base64url_encode(resolved_salt),
            _base64url_encode(derived),
        )
    )


def auth_configuration_error() -> str | None:
    if not os.getenv("MEMOS_ACCESS_PASSWORD_HASH", "").strip():
        return "服务器尚未配置访问密码。"
    if len(os.getenv("MEMOS_SESSION_SECRET", "")) < 32:
        return "服务器的登录会话密钥未配置或长度不足。"
    return None


def verify_access_password(candidate: str) -> bool:
    configured = os.getenv("MEMOS_ACCESS_PASSWORD_HASH", "").strip()
    parts = configured.split(":")
    if len(parts) != 4 or parts[0] != PASSWORD_PREFIX:
        return False
    try:
        iterations = int(parts[1])
        if iterations < 100_000:
            return False
        salt = _base64url_decode(parts[2])
        expected = _base64url_decode(parts[3])
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            candidate.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _sign_session(payload: str) -> str:
    secret = os.getenv("MEMOS_SESSION_SECRET", "").encode("utf-8")
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return _base64url_encode(signature)


def create_session_token(
    *,
    now_ms: int | None = None,
    ttl_ms: int = SESSION_TTL_MS,
    nonce: bytes | None = None,
) -> str:
    configuration_error = auth_configuration_error()
    if configuration_error:
        raise ValueError(configuration_error)
    issued_at = int(time.time() * 1000) if now_ms is None else now_ms
    expires_at = issued_at + ttl_ms
    payload = ".".join(
        (
            SESSION_VERSION,
            str(expires_at),
            _base64url_encode(nonce or secrets.token_bytes(16)),
        )
    )
    return f"{payload}.{_sign_session(payload)}"


def verify_session_token(token: str | None, *, now_ms: int | None = None) -> bool:
    if not token or auth_configuration_error():
        return False
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != SESSION_VERSION:
        return False
    try:
        expires_at = int(parts[1])
    except ValueError:
        return False
    current_time = int(time.time() * 1000) if now_ms is None else now_ms
    if expires_at <= current_time:
        return False
    payload = ".".join(parts[:3])
    return hmac.compare_digest(parts[3], _sign_session(payload))


def bearer_session_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.strip().partition(" ")
    if separator and scheme.lower() == "bearer" and token and " " not in token:
        return token
    return None


def secure_cookie_enabled() -> bool:
    return os.getenv("MEMOS_AUTH_COOKIE_SECURE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def auth_cookie_name() -> str:
    return SECURE_COOKIE_NAME if secure_cookie_enabled() else LOCAL_COOKIE_NAME


def request_session_token(*, cookies: dict[str, str], authorization: str | None) -> str | None:
    return (
        cookies.get(SECURE_COOKIE_NAME)
        or cookies.get(LOCAL_COOKIE_NAME)
        or bearer_session_token(authorization)
    )


def write_auth_configuration(
    env_path: Path,
    *,
    password: str,
    session_secret: str | None = None,
) -> None:
    """Update only application-auth keys in the MemOS environment file."""
    if len(password) < 12:
        raise ValueError("访问密码至少需要 12 个字符。")
    resolved_secret = session_secret or secrets.token_urlsafe(48)
    if len(resolved_secret) < 32:
        raise ValueError("登录会话密钥至少需要 32 个字符。")
    values = {
        "MEMOS_AUTH_REQUIRED": "true",
        "MEMOS_ACCESS_PASSWORD_HASH": create_password_hash(password),
        "MEMOS_SESSION_SECRET": resolved_secret,
    }
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    lines = existing.splitlines()
    found: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key.strip() in values:
            normalized_key = key.strip()
            if normalized_key not in found:
                updated.append(f"{normalized_key}={values[normalized_key]}")
                found.add(normalized_key)
            continue
        updated.append(line)
    if updated and updated[-1].strip():
        updated.append("")
    for key, value in values.items():
        if key not in found:
            updated.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为 MemOS 应用后端设置访问密码")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="要更新的 MemOS .env 文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = getpass.getpass("请输入新的访问密码（至少 12 个字符）: ")
    confirmation = getpass.getpass("请再次输入访问密码: ")
    if password != confirmation:
        raise ValueError("两次输入的密码不一致。")
    write_auth_configuration(args.env_file.resolve(), password=password)
    logger.info("应用后端访问密码已写入 %s", args.env_file.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

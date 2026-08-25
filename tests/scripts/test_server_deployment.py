import json
import os
import subprocess

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_root_start_script_runs_the_complete_local_stack() -> None:
    script = (REPO_ROOT / "start.ps1").read_text(encoding="utf-8")

    assert "docker\\docker-compose.yml" in script
    assert '"up", "-d", "--wait"' in script
    assert "if ($Build)" in script
    assert '"--build"' in script
    assert "compose -f $composeFile ps" in script


def test_local_environment_example_documents_application_features() -> None:
    example = (REPO_ROOT / "docker/.env.example").read_text(encoding="utf-8")

    for name in (
        "VIDEO_PARSER_MODEL",
        "VIDEO_API_KEY",
        "VIDEO_API_BASE",
        "OSS_REGION",
        "OSS_BUCKET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
        "MEMOS_TOPIC_DAILY_LIMIT",
        "MEMOS_WEB_UPLOAD_MAX_BYTES",
    ):
        assert f"{name}=" in example


def test_local_compose_starts_core_and_application_backend_together() -> None:
    compose = (REPO_ROOT / "docker/docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "docker/Dockerfile").read_text(encoding="utf-8")
    frontend_nginx = (REPO_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "COPY scripts/ ./scripts/" in dockerfile
    assert "PYTHONPATH=/app/src:/app/scripts" in dockerfile
    assert "\n  app-backend:" in compose
    assert "\n  frontend:" in compose
    assert "context: ../frontend" in compose
    assert "/app/scripts/memos_frontend_api.py" in compose
    assert "http://memos:8000" in compose
    assert '"127.0.0.1:3000:80"' in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:8011:8011"' in compose
    assert '"127.0.0.1:7474:7474"' in compose
    assert '"127.0.0.1:7687:7687"' in compose
    assert '"127.0.0.1:6333:6333"' in compose
    assert '"127.0.0.1:6334:6334"' in compose
    assert "condition: service_healthy" in compose
    assert "/api/v1/health" in compose
    assert "location /api/v1/" in frontend_nginx
    assert "proxy_pass http://app-backend:8011;" in frontend_nginx


def test_public_server_serves_frontend_and_application_api_from_one_origin() -> None:
    compose = (REPO_ROOT / "deploy/server/docker-compose.yml").read_text(encoding="utf-8")
    caddyfile = (REPO_ROOT / "deploy/server/Caddyfile").read_text(encoding="utf-8")
    frontend_dockerfile = (REPO_ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    frontend_nginx = (REPO_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "image: caddy:2.11.4-alpine" in compose
    assert '"${MEMOS_HTTP_PORT:-80}:80"' in compose
    assert '"${MEMOS_HTTPS_PORT:-443}:443"' in compose
    assert "PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}" in compose
    assert "default_sni {$PUBLIC_HOST}" in caddyfile
    for private_port in ("3000", "8000", "8011", "6333", "6334", "7474", "7687"):
        assert f'"{private_port}:{private_port}"' not in compose

    assert "\n  frontend:" in compose
    assert "context: ../../frontend" in compose
    assert "frontend:\n        condition: service_healthy" in compose
    assert "app-backend:\n        condition: service_healthy" in compose
    assert "memos:\n        condition: service_healthy" in compose

    assert "profile shortlived" in caddyfile
    assert "handle /api/v1/*" in caddyfile
    assert "reverse_proxy app-backend:8011" in caddyfile
    assert "reverse_proxy frontend:80" in caddyfile
    assert 'respond "Not Found" 404' not in caddyfile

    assert "FROM node:22-alpine AS build" in frontend_dockerfile
    assert "RUN npm ci" in frontend_dockerfile
    assert "RUN npm run build" in frontend_dockerfile
    assert "FROM nginx:1.27-alpine" in frontend_dockerfile
    assert "try_files $uri $uri/ /index.html;" in frontend_nginx

    assert "MEMOS_CORS_ALLOWED_ORIGINS" in compose
    assert "\n  companion:" not in compose
    assert "\n  app-backend:" in compose


def test_public_server_compose_renders_machine_specific_ports_and_index(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    server_dir = repo_root / "deploy" / "server"
    server_dir.mkdir(parents=True)
    (repo_root / "frontend").mkdir()
    compose_path = server_dir / "docker-compose.yml"
    compose_path.write_text(
        (REPO_ROOT / "deploy/server/docker-compose.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo_root / ".env").write_text("", encoding="utf-8")
    env_path = server_dir / ".server.env"
    env_path.write_text(
        """PUBLIC_HOST=127.0.0.1
ACME_EMAIL=test@example.com
NEO4J_PASSWORD=test-only-password
MEMOS_ACCESS_PASSWORD_HASH=test-only-hash
MEMOS_SESSION_SECRET=test-only-session-secret-over-32-characters
MEMOS_CORS_ALLOWED_ORIGINS=
MEMOS_HTTP_PORT=18080
MEMOS_HTTPS_PORT=18443
PIP_INDEX_URL=https://mirror.example/simple/
""",
        encoding="utf-8",
    )

    process_environment = os.environ.copy()
    for name in (
        "PUBLIC_HOST",
        "ACME_EMAIL",
        "NEO4J_PASSWORD",
        "MEMOS_ACCESS_PASSWORD_HASH",
        "MEMOS_SESSION_SECRET",
        "MEMOS_CORS_ALLOWED_ORIGINS",
        "MEMOS_HTTP_PORT",
        "MEMOS_HTTPS_PORT",
        "PIP_INDEX_URL",
    ):
        process_environment.pop(name, None)

    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_path),
            "-f",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        env=process_environment,
        text=True,
    )
    rendered = json.loads(completed.stdout)

    caddy_ports = {
        (str(item["published"]), item["target"]) for item in rendered["services"]["caddy"]["ports"]
    }
    assert caddy_ports == {("18080", 80), ("18443", 443)}
    assert (
        rendered["services"]["memos"]["build"]["args"]["PIP_INDEX_URL"]
        == "https://mirror.example/simple/"
    )
    assert (
        rendered["services"]["app-backend"]["build"]["args"]["PIP_INDEX_URL"]
        == "https://mirror.example/simple/"
    )
    app_environment = rendered["services"]["app-backend"]["environment"]
    assert app_environment["MEMOS_ACCESS_PASSWORD_HASH"] == "test-only-hash"
    assert app_environment["MEMOS_SESSION_SECRET"] == "test-only-session-secret-over-32-characters"
    assert "frontend" in rendered["services"]
    assert "ports" not in rendered["services"]["frontend"]

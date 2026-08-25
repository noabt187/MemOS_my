import json
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

    assert "COPY scripts/ ./scripts/" in dockerfile
    assert "PYTHONPATH=/app/src:/app/scripts" in dockerfile
    assert "\n  app-backend:" in compose
    assert "/app/scripts/memos_frontend_api.py" in compose
    assert "http://memos:8000" in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"127.0.0.1:8011:8011"' in compose
    assert '"127.0.0.1:7474:7474"' in compose
    assert '"127.0.0.1:7687:7687"' in compose
    assert '"127.0.0.1:6333:6333"' in compose
    assert '"127.0.0.1:6334:6334"' in compose
    assert "condition: service_healthy" in compose
    assert "/api/v1/health" in compose


def test_public_server_exposes_only_the_application_api() -> None:
    compose = (REPO_ROOT / "deploy/server/docker-compose.yml").read_text(encoding="utf-8")
    caddyfile = (REPO_ROOT / "deploy/server/Caddyfile").read_text(encoding="utf-8")

    assert "image: caddy:2.11.4-alpine" in compose
    assert '"${MEMOS_HTTP_PORT:-80}:80"' in compose
    assert '"${MEMOS_HTTPS_PORT:-443}:443"' in compose
    assert "PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}" in compose
    assert "default_sni {$PUBLIC_HOST}" in caddyfile
    assert "profile shortlived" in caddyfile
    assert "handle /api/v1/*" in caddyfile
    assert "reverse_proxy app-backend:8011" in caddyfile
    assert 'respond "Not Found" 404' in caddyfile
    assert "root * /srv/frontend" not in caddyfile
    assert "try_files" not in caddyfile
    assert "MEMOS_FRONTEND_DIST" not in compose
    assert "/srv/frontend" not in compose
    assert "MEMOS_CORS_ALLOWED_ORIGINS" in compose
    assert "\n  frontend:" not in compose
    assert "\n  companion:" not in compose
    assert "\n  app-backend:" in compose
    assert "app-backend:\n        condition: service_healthy" in compose
    assert "memos:\n        condition: service_healthy" in compose


def test_public_server_compose_renders_machine_specific_ports_and_index(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    server_dir = repo_root / "deploy" / "server"
    server_dir.mkdir(parents=True)
    compose_path = server_dir / "docker-compose.yml"
    compose_path.write_text(
        (REPO_ROOT / "deploy/server/docker-compose.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo_root / ".env").write_text("", encoding="utf-8")
    env_path = server_dir / ".server.env"
    env_path.write_text(
        "\n".join(
            (
                "PUBLIC_HOST=127.0.0.1",
                "ACME_EMAIL=test@example.com",
                "NEO4J_PASSWORD=test-only-password",
                "MEMOS_CORS_ALLOWED_ORIGINS=",
                "MEMOS_HTTP_PORT=18080",
                "MEMOS_HTTPS_PORT=18443",
                "PIP_INDEX_URL=https://mirror.example/simple/",
            )
        ),
        encoding="utf-8",
    )

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
        text=True,
    )
    rendered = json.loads(completed.stdout)

    caddy_ports = {
        (str(item["published"]), item["target"])
        for item in rendered["services"]["caddy"]["ports"]
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
    assert "frontend" not in rendered["services"]

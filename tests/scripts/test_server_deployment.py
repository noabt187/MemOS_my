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
    assert '"80:80"' in compose
    assert '"443:443"' in compose
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

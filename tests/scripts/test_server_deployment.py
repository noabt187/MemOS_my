from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_caddy_uses_ip_certificate_capable_release() -> None:
    compose = (REPO_ROOT / "deploy/server/docker-compose.yml").read_text(encoding="utf-8")
    caddyfile = (REPO_ROOT / "deploy/server/Caddyfile").read_text(encoding="utf-8")

    assert "image: caddy:2.11.4-alpine" in compose
    assert '"80:80"' in compose
    assert '"443:443"' in compose
    assert "profile shortlived" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile

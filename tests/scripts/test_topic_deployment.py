from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _service_block(compose: str, service_name: str) -> str:
    lines = compose.splitlines(keepends=True)
    marker = f"  {service_name}:"
    start = next(index for index, line in enumerate(lines) if line.rstrip() == marker)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith("    "):
            end = index
            break
    return "".join(lines[start:end])


def test_topic_scheduler_has_one_writer_in_each_compose() -> None:
    compose_paths = (
        REPO_ROOT / "docker/docker-compose.yml",
        REPO_ROOT / "deploy/server/docker-compose.yml",
    )

    for compose_path in compose_paths:
        compose = compose_path.read_text(encoding="utf-8")
        app_backend = _service_block(compose, "app-backend")

        assert "MEMOS_TOPIC_DAILY_LIMIT" not in compose
        assert (
            "MEMOS_TOPIC_SCHEDULER_ENABLED: ${MEMOS_TOPIC_SCHEDULER_ENABLED:-true}"
        ) in app_backend
        assert compose.count("MEMOS_TOPIC_STATE:") == 1
        assert "MEMOS_TOPIC_STATE: /data/topic/topics.json" in app_backend
        assert compose.count(":/data/topic") == 1
        assert ":/data/topic" in app_backend
        assert "replicas:" not in app_backend
        assert "--workers" not in app_backend
        assert "topic-worker:" not in compose
        assert "topic-scheduler:" not in compose
        assert "topic-sidecar:" not in compose


def test_server_topic_scheduler_configuration_and_writer_rule_are_documented() -> None:
    environment_example = (REPO_ROOT / "deploy/server/.server.env.example").read_text(
        encoding="utf-8"
    )
    readme = (REPO_ROOT / "deploy/server/README_ZH.md").read_text(encoding="utf-8")

    assert "MEMOS_TOPIC_DAILY_LIMIT" not in environment_example
    assert "MEMOS_TOPIC_SCHEDULER_ENABLED=true" in environment_example
    assert "MEMOS_TOPIC_SCHEDULER_ENABLED=true" in readme
    assert "唯一 Topic writer" in readme
    assert "不能扩容 `app-backend`" in readme
    assert "不能新增 Topic scheduler sidecar" in readme

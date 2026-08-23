#!/usr/bin/env python3
"""Remove persisted inline image data URLs without deleting memory records.

The script keeps the extracted memory text and replaces Base64 image payloads in
``sources`` with a local path (or another durable media reference). It updates
both Neo4j and the matching Qdrant payload. The default mode is a dry run;
pass ``--apply`` to write changes.
"""

from __future__ import annotations

import argparse
import json
import os

from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from qdrant_client import QdrantClient


INLINE_IMAGE_PLACEHOLDER = "[inline image data omitted]"


def _load_project_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _is_inline_image_data(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().lower().startswith("data:image/")


def _find_reference(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("source_path", "image_path", "media_uri", "source_url"):
            candidate = value.get(key)
            if (
                isinstance(candidate, str)
                and candidate.strip()
                and not _is_inline_image_data(candidate)
            ):
                return candidate.strip()
        for nested in value.values():
            candidate = _find_reference(nested)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for nested in value:
            candidate = _find_reference(nested)
            if candidate:
                return candidate
    return None


def _sanitize_value(value: Any, reference: str) -> Any:
    if _is_inline_image_data(value):
        return reference
    if isinstance(value, dict):
        cleaned = {key: _sanitize_value(item, reference) for key, item in value.items()}
        if cleaned.get("type") in {"image", "image_url"}:
            cleaned["inline_data_persisted"] = False
            image_info = cleaned.get("image_info")
            if isinstance(image_info, dict):
                image_info["inline_data_persisted"] = False
        return cleaned
    if isinstance(value, list):
        return [_sanitize_value(item, reference) for item in value]
    return value


def _sanitize_sources(raw_sources: Any, node_reference: str | None) -> list[Any]:
    sources = raw_sources if isinstance(raw_sources, list) else [raw_sources]
    clean_sources: list[Any] = []
    for raw_source in sources:
        decoded: Any = raw_source
        was_json_string = False
        if isinstance(raw_source, str):
            try:
                decoded = json.loads(raw_source)
                was_json_string = True
            except json.JSONDecodeError:
                decoded = raw_source
        reference = _find_reference(decoded) or node_reference or INLINE_IMAGE_PLACEHOLDER
        cleaned = _sanitize_value(decoded, reference)
        clean_sources.append(
            json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
            if was_json_string
            else cleaned
        )
    return clean_sources


def _contains_base64(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    lowered = text.lower()
    return "data:image/" in lowered or "base64," in lowered


def main() -> int:
    _load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the sanitised sources")
    parser.add_argument("--cube-id", default="default_cube")
    parser.add_argument("--collection", default="neo4j_vec_db")
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    args = parser.parse_args()

    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "12345678")
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(user, password))
    qdrant = QdrantClient(url=args.qdrant_url)

    query = """
    MATCH (n:Memory)
    WHERE n.user_name = $cube_id
      AND any(source IN coalesce(n.sources, [])
              WHERE toLower(source) CONTAINS 'data:image/'
                 OR toLower(source) CONTAINS 'base64,')
    RETURN n.id AS id, n.sources AS sources, n.source_path AS source_path
    ORDER BY n.id
    """
    with driver.session() as session:
        rows = list(session.run(query, cube_id=args.cube_id))

        updates: list[tuple[str, list[Any], str]] = []
        for row in rows:
            memory_id = str(row["id"])
            clean_sources = _sanitize_sources(row["sources"], row["source_path"])
            if _contains_base64(clean_sources):
                raise RuntimeError(f"Base64 remained after sanitising memory {memory_id}")
            reference = (
                _find_reference(clean_sources) or row["source_path"] or INLINE_IMAGE_PLACEHOLDER
            )
            updates.append((memory_id, clean_sources, str(reference)))

        print(f"Found {len(updates)} memory records with inline image data.")
        for memory_id, _sources, reference in updates:
            print(f"- {memory_id}: {reference}")

        if not args.apply:
            print("Dry run only. Re-run with --apply to update Neo4j and Qdrant.")
            driver.close()
            return 0

        for memory_id, clean_sources, _reference in updates:
            session.run(
                "MATCH (n:Memory {id: $id}) SET n.sources = $sources",
                id=memory_id,
                sources=clean_sources,
            ).consume()
            qdrant.set_payload(
                collection_name=args.collection,
                payload={"sources": clean_sources},
                points=[memory_id],
                wait=True,
            )

    driver.close()
    print(f"Updated {len(updates)} records in Neo4j and Qdrant; memory text was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate contact history references against stored event memories.

Run this script from the project environment every ten days. It initializes the
same components as the API service, checks one memory cube, removes missing
event references, and refreshes affected relationship summaries when possible.
"""

import argparse

from memos.api.handlers.component_init import init_server
from memos.memories.textual.relationship import RelationshipUpdater


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean stale relationship event references")
    parser.add_argument(
        "--cube-id",
        required=True,
        help="Memory cube/user_name whose relationship summaries should be checked",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Check even when the last successful check was less than ten days ago",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    components = init_server()
    updater = RelationshipUpdater(
        text_mem=components["naive_mem_cube"].text_mem,
        llm=components.get("llm"),
    )
    result = updater.cleanup_stale_history(user_name=args.cube_id, force=args.force)
    print(
        f"checked_relationships={result.checked_relationships} "
        f"updated_relationships={result.updated_relationships} "
        f"removed_references={result.removed_references}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

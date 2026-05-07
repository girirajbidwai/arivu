"""
Export `corrections` rows from Supabase into JSONL for fine-tuning prep.

Prototype scope:
  * Pull append-only corrections from the production table.
  * Write a compact JSONL file that downstream jobs can consume.
  * Stay dependency-light and safe to run on a laptop.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from supabase import create_client


def export_corrections(output_path: Path, limit: int = 1000) -> int:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    sb = create_client(url, key)

    rows = (
        sb.table("corrections")
        .select("*")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="learning_loop/exports/corrections.jsonl",
        help="Path to the JSONL file to write",
    )
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    count = export_corrections(Path(args.output), limit=args.limit)
    print(f"exported {count} corrections → {args.output}")


if __name__ == "__main__":
    main()

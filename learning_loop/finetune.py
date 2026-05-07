"""
Prototype fine-tune driver.

This does not launch a heavyweight LoRA job by itself. Instead it:
  1. Validates that the exported correction dataset exists.
  2. Computes a tiny summary of the training set.
  3. Writes a run manifest so the team can attach the exact dataset and
     hyperparameters used during the hackathon demo.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def summarise_dataset(dataset_path: Path) -> dict:
    counts = Counter()
    rows = 0
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            counts[row.get("field", "unknown")] += 1
            rows += 1
    return {"rows": rows, "field_breakdown": dict(counts)}


def write_manifest(dataset_path: Path, output_path: Path) -> dict:
    summary = summarise_dataset(dataset_path)
    manifest = {
        "dataset": str(dataset_path),
        "summary": summary,
        "status": "ready_for_lora_job",
        "notes": [
            "Prototype-phase placeholder manifest.",
            "Swap this step for the real LoRA runner once the base model and GPU target are locked.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="learning_loop/exports/corrections.jsonl")
    parser.add_argument("--output", default="learning_loop/exports/finetune_manifest.json")
    args = parser.parse_args()

    manifest = write_manifest(Path(args.dataset), Path(args.output))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

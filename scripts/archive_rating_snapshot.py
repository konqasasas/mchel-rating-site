#!/usr/bin/env python3
"""Archive compact daily rating outputs for later rating-history features.

Raw rankings are deliberately not committed every day: they are several MB and
would make the Git repository grow quickly. The compact overall_ranking.csv is
sufficient for later player-rating trend charts and is stored once per JST day.

The active course-difficulty table and metadata are archived too. This keeps
historical ratings auditable when the difficulty table is deliberately refreshed.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overall-csv", type=Path, required=True)
    parser.add_argument("--metadata-json", type=Path, required=True)
    parser.add_argument("--course-summary-csv", type=Path, required=True)
    parser.add_argument("--course-difficulty-csv", type=Path, required=True)
    parser.add_argument("--run-summary-json", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    return parser.parse_args()


def snapshot_date(summary_path: Path) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    timestamp = summary.get("fetched_at_utc") or summary.get("source_run_fetched_at_utc")
    if not timestamp:
        raise ValueError("run summary does not have fetched_at_utc or source_run_fetched_at_utc")
    parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return parsed.astimezone(JST).date().isoformat()


def copy(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> int:
    args = parse_args()
    day = snapshot_date(args.run_summary_json)
    current = args.data_dir / "current"
    history = args.data_dir / "history"

    copy(args.overall_csv, current / "overall_ranking.csv")
    copy(args.metadata_json, current / "rating_metadata.json")
    copy(args.course_summary_csv, current / "course_summary.csv")
    copy(args.course_difficulty_csv, current / "course_difficulty.csv")
    copy(args.run_summary_json, current / "run_summary.json")

    # One compact file per JST calendar day. A manual re-run on the same day
    # replaces that day’s snapshot rather than creating duplicates.
    copy(args.overall_csv, history / f"{day}_overall_ranking.csv")
    copy(args.metadata_json, history / f"{day}_rating_metadata.json")
    copy(args.course_difficulty_csv, history / f"{day}_course_difficulty.csv")
    print(f"Archived current rating outputs and history snapshot for {day}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

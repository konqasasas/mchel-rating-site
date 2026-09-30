#!/usr/bin/env python3
"""Archive the minimum persistent state needed by the rating site.

Daily history keeps only overall_ranking.csv because it is enough for player
rating charts, Peak Rating and Weekly comparisons.  Difficulty is archived
only when the monthly table is refreshed.  Current player-course state is
replaced in-place and is used to detect the next run's activity events.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--overall-csv", type=Path, required=True)
    p.add_argument("--metadata-json", type=Path, required=True)
    p.add_argument("--course-summary-csv", type=Path, required=True)
    p.add_argument("--course-difficulty-csv", type=Path, required=True)
    p.add_argument("--player-course-records-csv", type=Path, required=True)
    p.add_argument("--course-records-csv", type=Path, required=True)
    p.add_argument("--run-summary-json", type=Path, required=True)
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--difficulty-refreshed", action="store_true")
    return p.parse_args()


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



def copy_compact_player_state(source: Path, target: Path) -> None:
    """Persist only columns needed to diff the next snapshot."""
    columns = [
        "player_uuid", "course_name", "rank", "time_ms",
        "base_course_score", "grade", "difficulty_adjusted_course_score",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8-sig", newline="") as src, target.open(
        "w", encoding="utf-8-sig", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"player state is missing columns: {missing}")
        writer = csv.DictWriter(dst, fieldnames=columns)
        writer.writeheader()
        for row in reader:
            writer.writerow({c: row.get(c, "") for c in columns})

def main() -> int:
    args = parse_args()
    day = snapshot_date(args.run_summary_json)
    current = args.data_dir / "current"
    history = args.data_dir / "history"

    copy(args.overall_csv, current / "overall_ranking.csv")
    copy(args.metadata_json, current / "rating_metadata.json")
    copy(args.course_summary_csv, current / "course_summary.csv")
    copy(args.course_difficulty_csv, current / "course_difficulty.csv")
    copy_compact_player_state(args.player_course_records_csv, current / "player_course_state.csv")
    copy(args.course_records_csv, current / "course_records.csv")
    copy(args.run_summary_json, current / "run_summary.json")

    # A manual rerun on the same day replaces that day's compact history.
    copy(args.overall_csv, history / f"{day}_overall_ranking.csv")

    if args.difficulty_refreshed:
        copy(
            args.course_difficulty_csv,
            args.data_dir / "difficulty" / "history" / f"{day}_course_difficulty.csv",
        )

    print(f"Archived current state and compact overall history for {day}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

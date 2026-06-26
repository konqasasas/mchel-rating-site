#!/usr/bin/env python3
"""Fetch MCHel 1.12.2 athletic rankings from the public JSON APIs.

The catalog is fetched from https://www.mchel.net/data/athletics.json.
Each non-seasonal course is then fetched from:
  https://api.mchel.net/v1/athletic/<course name>/ranking

The API intentionally returns at most the top 100 rankings per course.
This script is designed for the daily GitHub Actions workflow. It writes all
files first, then exits with a non-zero status if any course could not be
retrieved. That prevents an incomplete fetch from replacing the public site.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ATHLETICS_URL = "https://www.mchel.net/data/athletics.json"
RANKING_URL = "https://api.mchel.net/v1/athletic/{course}/ranking"
EXCLUDED_CATEGORIES = {"期間限定"}
USER_AGENT = "mchel-rating-site/1.0 (+https://konqasasas.github.io/mchel-rating-site/)"

RANKING_COLUMNS = [
    "course_name", "category", "column", "rank", "player_name", "player_uuid",
    "time_ms", "time_display", "recorded_at_epoch", "recorded_at_utc",
    "source_url", "fetched_at_utc",
]
COURSE_COLUMNS = [
    "course_name", "category", "column", "status", "records_fetched", "max_rank",
    "duration_seconds", "source_url",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch MCHel 1.12.2 rankings from public APIs.")
    parser.add_argument("--out-dir", type=Path, default=Path("work/latest"))
    parser.add_argument("--sleep-seconds", type=float, default=0.20,
                        help="Delay after each course request (default: 0.20).")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--allow-partial", action="store_true",
                        help="Write partial data and return success even if a course fails. Not for normal use.")
    return parser.parse_args()


def request_json(url: str, timeout: float, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs are fixed public endpoints
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(attempt)
    assert last_error is not None
    raise last_error


def flatten_courses(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("athletics.json must contain a JSON object.")
    courses: list[dict[str, str]] = []
    for column, groups in payload.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            category = str(group.get("head", "")).strip()
            names = group.get("name", [])
            if not isinstance(names, list):
                continue
            for raw_name in names:
                course_name = str(raw_name).strip()
                # Catalog separators such as ------ are not courses.
                if not course_name or not course_name.strip("-").strip():
                    continue
                courses.append({
                    "course_name": course_name,
                    "category": category,
                    "column": str(column),
                })
    if not courses:
        raise ValueError("No courses found in athletics.json.")
    return courses


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        for key in ("ranking", "data", "records", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [record for record in value if isinstance(record, dict)]
    raise ValueError("Could not find a ranking array in the API response.")


def format_time_ms(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return ""
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def epoch_to_utc(value: Any) -> str:
    try:
        epoch = float(value)
        if epoch > 10_000_000_000:  # milliseconds epoch
            epoch /= 1000
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def numeric_rank(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if args.sleep_seconds < 0:
        raise ValueError("--sleep-seconds must be zero or greater.")
    if args.max_retries < 1:
        raise ValueError("--max-retries must be at least 1.")

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rankings_csv = out_dir / "mchel_1_12_2_rankings_excluding_seasonal.csv"
    courses_csv = out_dir / "mchel_1_12_2_course_summary_excluding_seasonal.csv"
    summary_json = out_dir / "mchel_1_12_2_run_summary_excluding_seasonal.json"
    errors_json = out_dir / "mchel_1_12_2_errors_excluding_seasonal.json"

    started = time.perf_counter()
    fetched_at_utc = datetime.now(timezone.utc).isoformat()
    catalog_payload = request_json(ATHLETICS_URL, args.timeout_seconds, args.max_retries)
    all_courses = flatten_courses(catalog_payload)
    excluded_courses = [course for course in all_courses if course["category"] in EXCLUDED_CATEGORIES]
    courses = [course for course in all_courses if course["category"] not in EXCLUDED_CATEGORIES]

    print(f"Catalog courses: {len(all_courses)}")
    print(f"Excluded seasonal courses: {len(excluded_courses)}")
    print(f"Fetching courses: {len(courses)}")

    ranking_rows: list[dict[str, Any]] = []
    course_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, course in enumerate(courses, start=1):
        course_name = course["course_name"]
        source_url = RANKING_URL.format(course=quote(course_name, safe=""))
        course_started = time.perf_counter()
        try:
            records = extract_records(request_json(source_url, args.timeout_seconds, args.max_retries))
            for record in records:
                rank = record.get("rank", record.get("ranking", ""))
                player_name = record.get("name", record.get("player_name", ""))
                player_uuid = record.get("uuid", record.get("player_uuid", ""))
                time_ms = record.get("time", record.get("time_ms", ""))
                epoch = record.get("epoch", record.get("recorded_at", ""))
                ranking_rows.append({
                    "course_name": course_name,
                    "category": course["category"],
                    "column": course["column"],
                    "rank": rank,
                    "player_name": player_name,
                    "player_uuid": player_uuid,
                    "time_ms": time_ms,
                    "time_display": format_time_ms(time_ms),
                    "recorded_at_epoch": epoch,
                    "recorded_at_utc": epoch_to_utc(epoch),
                    "source_url": source_url,
                    "fetched_at_utc": fetched_at_utc,
                })
            course_rows.append({
                "course_name": course_name,
                "category": course["category"],
                "column": course["column"],
                "status": "ok",
                "records_fetched": len(records),
                "max_rank": max((numeric_rank(record.get("rank", record.get("ranking", 0))) for record in records), default=0),
                "duration_seconds": round(time.perf_counter() - course_started, 2),
                "source_url": source_url,
            })
            print(f"[{index:>3}/{len(courses)}] OK    {course_name} ({len(records)} records)")
        except Exception as exc:  # preserve enough diagnostics while continuing other courses
            errors.append({
                "course_name": course_name,
                "category": course["category"],
                "column": course["column"],
                "source_url": source_url,
                "error": repr(exc),
            })
            course_rows.append({
                "course_name": course_name,
                "category": course["category"],
                "column": course["column"],
                "status": "error",
                "records_fetched": 0,
                "max_rank": 0,
                "duration_seconds": round(time.perf_counter() - course_started, 2),
                "source_url": source_url,
            })
            print(f"[{index:>3}/{len(courses)}] ERROR {course_name}: {exc}", file=sys.stderr)
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    write_csv(rankings_csv, RANKING_COLUMNS, ranking_rows)
    write_csv(courses_csv, COURSE_COLUMNS, course_rows)

    summary = {
        "fetched_at_utc": fetched_at_utc,
        "catalog_course_count": len(all_courses),
        "excluded_categories": sorted(EXCLUDED_CATEGORIES),
        "excluded_course_count": len(excluded_courses),
        "excluded_courses": [course["course_name"] for course in excluded_courses],
        "course_count": len(courses),
        "successful_course_count": sum(row["status"] == "ok" for row in course_rows),
        "failed_course_count": len(errors),
        "ranking_row_count": len(ranking_rows),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "sleep_seconds_per_course": args.sleep_seconds,
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    errors_json.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if errors and not args.allow_partial:
        print("One or more courses failed; refusing to publish a partial snapshot.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

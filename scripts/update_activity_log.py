#!/usr/bin/env python3
"""Create compact per-player activity events from two published snapshots.

The repository does not retain raw ranking snapshots.  Instead it retains one
compact current player-course state and a rolling JSONL event log.  On a
monthly difficulty refresh, an optional baseline calculated with the old
frozen difficulty table isolates the rating change caused by the new table.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mchel_time_attack_rating_v1 import BEST_N, make_best30_weights, public_rating

JST = ZoneInfo("Asia/Tokyo")
KEEP_DAYS = 180


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--previous-overall", type=Path)
    p.add_argument("--previous-player-state", type=Path)
    p.add_argument("--previous-course-records", type=Path)
    p.add_argument("--current-overall", type=Path, required=True)
    p.add_argument("--current-player-state", type=Path, required=True)
    p.add_argument("--current-course-records", type=Path, required=True)
    p.add_argument("--baseline-overall", type=Path)
    p.add_argument("--baseline-player-state", type=Path)
    p.add_argument("--baseline-course-records", type=Path)
    p.add_argument("--run-summary-json", type=Path, required=True)
    p.add_argument("--activity-log", type=Path, default=Path("data/activity/events.jsonl"))
    p.add_argument("--events-out", type=Path, default=Path("work/rating/activity_events.json"))
    return p.parse_args()


def read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def snapshot_date(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    stamp = data.get("fetched_at_utc") or data.get("source_run_fetched_at_utc")
    if not stamp:
        raise ValueError("run summary has no fetched_at_utc")
    dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return dt.astimezone(JST).date().isoformat()


def rating_map(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {}
    return dict(zip(df["player_uuid"].astype(str), pd.to_numeric(df["published_rating"])))


def rank_map(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    return dict(zip(df["player_uuid"].astype(str), pd.to_numeric(df["overall_rank"]).astype(int)))


def name_map(df: pd.DataFrame) -> dict[str, str]:
    if df.empty:
        return {}
    return dict(zip(df["player_uuid"].astype(str), df["player_name"].fillna("").astype(str)))


def normalize_state(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["player_uuid"] = out["player_uuid"].astype(str)
    out["course_name"] = out["course_name"].astype(str)
    for col in ["time_ms", "rank", "base_course_score", "difficulty_adjusted_course_score"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def positions(df: pd.DataFrame) -> dict[tuple[str, str], int]:
    if df.empty:
        return {}
    ordered = df.sort_values(
        ["player_uuid", "difficulty_adjusted_course_score", "course_name"],
        ascending=[True, False, True], kind="stable"
    ).copy()
    ordered["pos"] = ordered.groupby("player_uuid").cumcount() + 1
    return {(str(r.player_uuid), str(r.course_name)): int(r.pos) for r in ordered.itertuples()}


def rating_from_player_state(player_rows: pd.DataFrame) -> float | None:
    if len(player_rows) < BEST_N:
        return None
    ordered = player_rows.sort_values(
        ["difficulty_adjusted_course_score", "course_name"],
        ascending=[False, True], kind="stable"
    ).head(BEST_N)
    weights = make_best30_weights()["weight"].to_numpy(float)
    scores = ordered["difficulty_adjusted_course_score"].to_numpy(float)
    p = float(np.dot(scores, weights) / weights.sum())
    return float(public_rating(pd.Series([p])).iloc[0])


def marginal_delta(
    current_state: pd.DataFrame,
    player_uuid: str,
    course_name: str,
    previous_row: pd.Series | None,
    current_rating: float,
) -> float:
    rows = current_state.loc[current_state["player_uuid"].eq(player_uuid)].copy()
    rows = rows.loc[~rows["course_name"].eq(course_name)].copy()
    if previous_row is not None:
        restored = previous_row.to_dict()
        restored["player_uuid"] = player_uuid
        restored["course_name"] = course_name
        rows = pd.concat([rows, pd.DataFrame([restored])], ignore_index=True)
    cf = rating_from_player_state(rows)
    if cf is None:
        return 0.0
    return float(current_rating - cf)


def row_lookup(df: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    if df.empty:
        return {}
    return {
        (str(row["player_uuid"]), str(row["course_name"])): row
        for _, row in df.iterrows()
    }


def course_record_lookup(df: pd.DataFrame) -> dict[str, pd.Series]:
    if df.empty:
        return {}
    return {str(row["course_name"]): row for _, row in df.iterrows()}


def split_pipe(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    return [x for x in str(value).split("|") if x]


def load_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def main() -> int:
    args = parse_args()
    day = snapshot_date(args.run_summary_json)

    prev_overall = read_csv(args.previous_overall)
    prev_state = normalize_state(read_csv(args.previous_player_state))
    prev_records = read_csv(args.previous_course_records)
    final_overall = read_csv(args.current_overall)
    final_state = normalize_state(read_csv(args.current_player_state))
    final_records = read_csv(args.current_course_records)

    # Record-driven comparison uses the same (old) difficulty table on monthly
    # refresh days. On ordinary days baseline == final.
    baseline_overall = read_csv(args.baseline_overall)
    baseline_state = normalize_state(read_csv(args.baseline_player_state))
    baseline_records = read_csv(args.baseline_course_records)
    if baseline_overall.empty:
        baseline_overall = final_overall
    if baseline_state.empty:
        baseline_state = final_state
    if baseline_records.empty:
        baseline_records = final_records

    events: list[dict] = []
    if not prev_overall.empty and not prev_state.empty and not prev_records.empty:
        prev_lookup = row_lookup(prev_state)
        curr_lookup = row_lookup(baseline_state)
        prev_pos = positions(prev_state)
        curr_pos = positions(baseline_state)
        current_ratings = rating_map(baseline_overall)
        names = name_map(baseline_overall)

        # PB changes. A PB that remains outside Best 30 is omitted, except an
        # existing #1 improved to another #1 (explicit user-facing rule).
        changed_by_player_course: set[tuple[str, str]] = set()
        for key, cur in curr_lookup.items():
            uuid, course = key
            old = prev_lookup.get(key)
            old_time = float(old["time_ms"]) if old is not None else None
            new_time = float(cur["time_ms"])
            if old_time is not None and not (new_time < old_time):
                continue
            if old_time is None:
                # A newly visible top-100 record is only activity-worthy if it
                # reaches Best 30. Otherwise this is outside -> outside.
                pass

            old_p = prev_pos.get(key)
            new_p = curr_pos.get(key)
            old_rank = int(old["rank"]) if old is not None and pd.notna(old["rank"]) else None
            new_rank = int(cur["rank"]) if pd.notna(cur["rank"]) else None
            self_wr_update = old_rank == 1 and new_rank == 1
            if not self_wr_update and not ((old_p and old_p <= BEST_N) or (new_p and new_p <= BEST_N)):
                continue

            if old_p is None or old_p > BEST_N:
                best30_status = "NEW BEST 30" if new_p and new_p <= BEST_N else "OUTSIDE BEST 30"
            elif new_p and new_p <= BEST_N:
                best30_status = "BEST 30 RETAINED"
            else:
                best30_status = "OUTSIDE BEST 30"

            current_rating = current_ratings.get(uuid)
            delta = 0.0
            if current_rating is not None:
                delta = marginal_delta(baseline_state, uuid, course, old, current_rating)

            events.append({
                "date": day,
                "player_uuid": uuid,
                "player_name": names.get(uuid, str(cur.get("player_name", ""))),
                "type": "self_wr_update" if self_wr_update else "pb_update",
                "course_name": course,
                "old_time_ms": int(old_time) if old_time is not None else None,
                "new_time_ms": int(new_time),
                "old_rank": old_rank,
                "new_rank": new_rank,
                "old_grade": str(old.get("grade", "")) if old is not None else None,
                "new_grade": str(cur.get("grade", "")),
                "best30_status": best30_status,
                "rating_delta": round(delta, 6),
            })
            changed_by_player_course.add(key)

        # Course-record changes by another player. Show them only for players
        # whose affected course was/is in Best 30.
        old_courses = course_record_lookup(prev_records)
        new_courses = course_record_lookup(baseline_records)
        for course, new_rec in new_courses.items():
            old_rec = old_courses.get(course)
            if old_rec is None:
                continue
            old_wr = int(float(old_rec["course_record_time_ms"]))
            new_wr = int(float(new_rec["course_record_time_ms"]))
            if new_wr >= old_wr:
                continue
            old_holders = set(split_pipe(old_rec.get("holder_uuids")))
            new_holders = set(split_pipe(new_rec.get("holder_uuids")))
            new_names = split_pipe(new_rec.get("holder_names"))
            new_uuids_order = split_pipe(new_rec.get("holder_uuids"))
            updater_names = [
                name for uid, name in zip(new_uuids_order, new_names)
                if uid not in old_holders
            ]
            updater = ", ".join(updater_names) or ", ".join(new_names) or "Unknown"

            affected_keys = {
                key for key in set(prev_lookup) | set(curr_lookup)
                if key[1] == course
            }
            for key in sorted(affected_keys):
                uuid, _ = key
                if key in changed_by_player_course:
                    continue
                if uuid in new_holders:
                    continue
                old_p = prev_pos.get(key)
                new_p = curr_pos.get(key)
                if not ((old_p and old_p <= BEST_N) or (new_p and new_p <= BEST_N)):
                    continue
                old = prev_lookup.get(key)
                cur = curr_lookup.get(key)
                if cur is None or old is None:
                    continue
                current_rating = current_ratings.get(uuid)
                delta = 0.0
                if current_rating is not None:
                    delta = marginal_delta(baseline_state, uuid, course, old, current_rating)
                events.append({
                    "date": day,
                    "player_uuid": uuid,
                    "player_name": names.get(uuid, str(cur.get("player_name", ""))),
                    "type": "wr_update",
                    "course_name": course,
                    "updater_name": updater,
                    "old_wr_ms": old_wr,
                    "new_wr_ms": new_wr,
                    "old_raw_score": float(old["base_course_score"]),
                    "new_raw_score": float(cur["base_course_score"]),
                    "rating_delta": round(delta, 6),
                })

    # Monthly Difficulty update is one aggregate card per player. Its delta is
    # isolated using the same current ranking snapshot under old vs new tables.
    if args.baseline_overall and args.baseline_overall.exists():
        before = rating_map(baseline_overall)
        after = rating_map(final_overall)
        names = name_map(final_overall)
        for uuid, new_rating in after.items():
            if uuid not in before:
                continue
            delta = new_rating - before[uuid]
            if round(delta, 2) == 0:
                continue
            events.append({
                "date": day,
                "player_uuid": uuid,
                "player_name": names.get(uuid, ""),
                "type": "difficulty_update",
                "old_rating": round(before[uuid], 6),
                "new_rating": round(new_rating, 6),
                "rating_delta": round(delta, 6),
            })

    args.events_out.parent.mkdir(parents=True, exist_ok=True)
    args.events_out.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")

    # Append / replace same-day generated events, then trim to a rolling window.
    existing = [e for e in load_existing(args.activity_log) if e.get("date") != day]
    combined = existing + events
    cutoff = datetime.fromisoformat(day).date() - timedelta(days=KEEP_DAYS)
    kept = []
    for event in combined:
        try:
            event_day = datetime.fromisoformat(str(event.get("date"))).date()
        except ValueError:
            continue
        if event_day >= cutoff:
            kept.append(event)
    kept.sort(key=lambda e: (str(e.get("date", "")), str(e.get("player_uuid", "")), str(e.get("type", "")), str(e.get("course_name", ""))))
    args.activity_log.parent.mkdir(parents=True, exist_ok=True)
    args.activity_log.write_text(
        "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in kept),
        encoding="utf-8",
    )
    print(f"Generated {len(events)} activity events for {day}; rolling log has {len(kept)} events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

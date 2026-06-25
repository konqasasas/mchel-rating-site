#!/usr/bin/env python3
"""
MCHel 1.12.2 Athletic / Time-Attack Overall Rating

Input:
  A rankings CSV in the schema used by
  mchel_1_12_2_rankings_excluding_seasonal.csv

This script uses ONLY the rankings CSV.  A course is eligible for the overall
rating if the snapshot contains exactly 100 ranking rows for that course.

Rating specification
--------------------
1) Eligible course:
       number of ranking rows for the course == 100

2) Course score:
       score = 100 * course_record_time / player_time
   The course record is the minimum time_ms in the input snapshot.
   rank is not used for the score.

3) Eligible player:
       at least 30 results on eligible courses

4) Raw performance index P:
       Take the player's 30 highest course scores.
       Weight positions 1..30 with a right-shifted inverse-logistic curve:
          midpoint m = 20
          steepness k = 0.18
          weight(1) = 1.00, weight(30) = 0.50 exactly
       P is the weighted mean of the 30 scores.

5) Published rating:
       h(P) = ln((P + 0.5) / (100.5 - P))
       rating = 100 + 1100 * (h(P) - h(0)) / (h(100) - h(0))

   This finite soft-logit conversion maps P=0 exactly to 100 and P=100
   exactly to 1200. It cannot produce a negative rating and needs no clipping.

6) Tiers:
       D:   100.00–699.99
       C:   700.00–799.99
       B:   800.00–899.99
       A:   900.00–999.99
       S:   1000.00–1099.99
       SS:  1100.00–1199.99
       SSS: 1200.00 (only when P is exactly 100 within numeric tolerance)

Outputs:
  overall_ranking.csv
  best30_components.csv
  course_summary.csv
  rating_metadata.json
  weight_curve.png
  rating_distribution.png

Requirements:
  pip install pandas numpy matplotlib

Example:
  python mchel_best30_rating.py \
      mchel_1_12_2_rankings_excluding_seasonal.csv \
      --out-dir rating_output
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ---- Fixed, published rating parameters -------------------------------------

BEST_N = 30
WEIGHT_FLOOR = 0.50
WEIGHT_MIDPOINT = 20.0
WEIGHT_STEEPNESS = 0.18

RATING_MIN = 100.0
RATING_MAX = 1200.0
SOFT_LOGIT_MARGIN = 0.5

REQUIRED_COLUMNS = {
    "course_name",
    "player_name",
    "player_uuid",
    "time_ms",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate MCHel Best-30 weighted overall ratings from one rankings CSV."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Ranking snapshot CSV (for example mchel_1_12_2_rankings_excluding_seasonal.csv)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("mchel_rating_output"),
        help="Directory for CSV, JSON, and PNG outputs (default: mchel_rating_output)",
    )
    return parser.parse_args()


def require_columns(df: pd.DataFrame, required: Iterable[str]) -> None:
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise ValueError(
            "Input CSV is missing required columns: "
            + ", ".join(missing)
        )


def choose_latest_player_name(df: pd.DataFrame) -> pd.Series:
    """
    Return one display name per UUID.

    If recorded_at_epoch is available, use the most recently recorded row.
    Otherwise, use the last appearance in the input CSV.
    """
    name_source = df[["player_uuid", "player_name"]].copy()
    if "recorded_at_epoch" in df.columns:
        name_source["recorded_at_epoch"] = pd.to_numeric(
            df["recorded_at_epoch"], errors="coerce"
        )
        name_source = name_source.sort_values(
            ["player_uuid", "recorded_at_epoch"],
            kind="stable",
            na_position="first",
        )
    return name_source.groupby("player_uuid", sort=False)["player_name"].last()


def deduplicate_player_course_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    There should normally be one row per course_name x player_uuid.

    If duplicates exist, retain the fastest time.  For equal times, retain the
    latest recorded_at_epoch when available.  A warning is emitted so the
    snapshot can be investigated.
    """
    key = ["course_name", "player_uuid"]
    duplicated = df.duplicated(key, keep=False)
    if not duplicated.any():
        return df.copy()

    duplicate_rows = int(duplicated.sum())
    duplicate_pairs = int(df.loc[duplicated].groupby(key).ngroups)
    warnings.warn(
        f"Found {duplicate_rows} rows across {duplicate_pairs} duplicate "
        "course/player pairs. Keeping the fastest time per pair.",
        RuntimeWarning,
        stacklevel=2,
    )

    work = df.copy()
    sort_columns = ["course_name", "player_uuid", "time_ms"]
    ascending = [True, True, True]

    if "recorded_at_epoch" in work.columns:
        work["_recorded_at_epoch_numeric"] = pd.to_numeric(
            work["recorded_at_epoch"], errors="coerce"
        )
        sort_columns.append("_recorded_at_epoch_numeric")
        ascending.append(False)

    work = work.sort_values(sort_columns, ascending=ascending, kind="stable")
    work = work.drop_duplicates(key, keep="first")
    return work.drop(columns=["_recorded_at_epoch_numeric"], errors="ignore")


def make_best30_weights() -> pd.DataFrame:
    """
    Build exact endpoint weights:
      position 1  = 1.00
      position 30 = 0.50
    with a right-shifted inverse-logistic decline.
    """
    positions = np.arange(1, BEST_N + 1, dtype=float)
    raw = 1.0 / (
        1.0 + np.exp(WEIGHT_STEEPNESS * (positions - WEIGHT_MIDPOINT))
    )
    weights = WEIGHT_FLOOR + (1.0 - WEIGHT_FLOOR) * (
        (raw - raw[-1]) / (raw[0] - raw[-1])
    )

    # Explicitly set endpoints against tiny floating-point drift.
    weights[0] = 1.0
    weights[-1] = WEIGHT_FLOOR

    return pd.DataFrame(
        {
            "best30_position": positions.astype(int),
            "weight": weights,
        }
    )


def finite_soft_logit_rating(performance_index: pd.Series) -> pd.Series:
    """
    Convert a raw performance index P in [0, 100] into [100, 1200] exactly.

    No clipping is used. Values outside [0, 100] indicate an upstream
    data/calculation problem and are rejected.
    """
    p = performance_index.astype(float)

    tolerance = 1e-10
    if ((p < -tolerance) | (p > 100.0 + tolerance)).any():
        bad = p[(p < -tolerance) | (p > 100.0 + tolerance)].head(5).tolist()
        raise ValueError(
            "Raw performance index must be within [0, 100]. "
            f"Examples of invalid values: {bad}"
        )

    def h(x: float | pd.Series) -> float | pd.Series:
        return np.log((x + SOFT_LOGIT_MARGIN) / (100.0 + SOFT_LOGIT_MARGIN - x))

    h0 = h(0.0)
    h100 = h(100.0)

    return RATING_MIN + (RATING_MAX - RATING_MIN) * (h(p) - h0) / (h100 - h0)


def assign_tier(
    raw_performance_index: float,
    published_rating: float,
) -> str:
    """
    Use P, not rounded displayed rating, to award SSS.
    """
    if math.isclose(raw_performance_index, 100.0, rel_tol=0.0, abs_tol=1e-10):
        return "SSS"
    if published_rating >= 1100.0:
        return "SS"
    if published_rating >= 1000.0:
        return "S"
    if published_rating >= 900.0:
        return "A"
    if published_rating >= 800.0:
        return "B"
    if published_rating >= 700.0:
        return "C"
    return "D"


def make_course_summary(work: pd.DataFrame) -> pd.DataFrame:
    summary = (
        work.groupby("course_name", as_index=False)
        .agg(
            ranking_row_count=("player_uuid", "size"),
            course_record_time_ms=("time_ms", "min"),
            unique_players=("player_uuid", "nunique"),
        )
        .sort_values(["ranking_row_count", "course_name"], ascending=[False, True])
    )
    summary["eligible_for_overall"] = summary["ranking_row_count"].eq(100)
    return summary


def calculate_ratings(input_csv: Path, out_dir: Path) -> pd.DataFrame:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    out_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(input_csv)
    require_columns(raw, REQUIRED_COLUMNS)

    raw["course_name"] = raw["course_name"].astype("string").str.strip()
    raw["player_uuid"] = raw["player_uuid"].astype("string").str.strip()
    raw["player_name"] = raw["player_name"].astype("string").fillna("").str.strip()
    raw["time_ms"] = pd.to_numeric(raw["time_ms"], errors="coerce")

    invalid = (
        raw["course_name"].isna()
        | raw["course_name"].eq("")
        | raw["player_uuid"].isna()
        | raw["player_uuid"].eq("")
        | raw["time_ms"].isna()
        | raw["time_ms"].le(0)
    )
    if invalid.any():
        raise ValueError(
            f"Input has {int(invalid.sum())} invalid rows "
            "(missing course/player UUID or non-positive time_ms)."
        )

    latest_names = choose_latest_player_name(raw)
    work = deduplicate_player_course_rows(raw)

    course_summary = make_course_summary(work)
    course_summary.to_csv(out_dir / "course_summary.csv", index=False)

    # The policy is deliberately simple: only full top-100 lists count.
    eligible_courses = course_summary.loc[
        course_summary["eligible_for_overall"], "course_name"
    ]
    eligible = work.loc[work["course_name"].isin(eligible_courses)].copy()

    course_records = eligible.groupby("course_name")["time_ms"].min().rename(
        "course_record_time_ms"
    )
    eligible = eligible.merge(
        course_records,
        on="course_name",
        how="left",
        validate="many_to_one",
    )
    eligible["course_score"] = (
        100.0 * eligible["course_record_time_ms"] / eligible["time_ms"]
    )

    # A ratio cannot exceed 100 because course_record_time_ms is the min time.
    if eligible["course_score"].gt(100.0 + 1e-10).any():
        raise AssertionError("Course score exceeded 100; inspect course record calculation.")

    eligible_course_counts = (
        eligible.groupby("player_uuid")["course_name"]
        .nunique()
        .rename("eligible_course_count")
    )
    official_uuids = eligible_course_counts[
        eligible_course_counts.ge(BEST_N)
    ].index

    official = eligible.loc[eligible["player_uuid"].isin(official_uuids)].copy()
    official = official.sort_values(
        ["player_uuid", "course_score", "course_name"],
        ascending=[True, False, True],
        kind="stable",
    )
    best30 = official.groupby("player_uuid", group_keys=False).head(BEST_N).copy()
    best30["best30_position"] = best30.groupby("player_uuid").cumcount() + 1

    weights = make_best30_weights()
    weights.to_csv(out_dir / "best30_weights.csv", index=False)

    best30 = best30.merge(
        weights,
        on="best30_position",
        how="left",
        validate="many_to_one",
    )
    best30["weighted_score"] = best30["course_score"] * best30["weight"]

    player_scores = (
        best30.groupby("player_uuid", as_index=False)
        .agg(
            weighted_score_sum=("weighted_score", "sum"),
            weight_sum=("weight", "sum"),
            best_course_score=("course_score", "max"),
            thirtieth_course_score=("course_score", "min"),
        )
    )
    player_scores["raw_performance_index"] = (
        player_scores["weighted_score_sum"] / player_scores["weight_sum"]
    )
    player_scores["published_rating"] = finite_soft_logit_rating(
        player_scores["raw_performance_index"]
    )

    player_scores["eligible_course_count"] = player_scores["player_uuid"].map(
        eligible_course_counts
    )
    player_scores["player_name"] = player_scores["player_uuid"].map(latest_names)
    player_scores["tier"] = [
        assign_tier(p, r)
        for p, r in zip(
            player_scores["raw_performance_index"],
            player_scores["published_rating"],
        )
    ]

    player_scores = player_scores.sort_values(
        ["published_rating", "player_uuid"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    player_scores.insert(0, "overall_rank", np.arange(1, len(player_scores) + 1))

    # Keep unrounded numbers in CSV for reproducibility; front-end can format them.
    ranking_columns = [
        "overall_rank",
        "tier",
        "player_uuid",
        "player_name",
        "published_rating",
        "raw_performance_index",
        "eligible_course_count",
        "best_course_score",
        "thirtieth_course_score",
    ]
    ranking = player_scores[ranking_columns].copy()
    ranking.to_csv(out_dir / "overall_ranking.csv", index=False)

    component_columns = [
        "player_uuid",
        "player_name",
        "best30_position",
        "weight",
        "course_name",
        "time_ms",
        "course_record_time_ms",
        "course_score",
        "weighted_score",
    ]
    best30["player_name"] = best30["player_uuid"].map(latest_names)
    best30 = best30.merge(
        ranking[["player_uuid", "overall_rank", "tier", "published_rating", "raw_performance_index"]],
        on="player_uuid",
        how="left",
        validate="many_to_one",
    )
    component_columns = [
        "overall_rank",
        "tier",
        "published_rating",
        "raw_performance_index",
    ] + component_columns
    best30[component_columns].sort_values(
        ["overall_rank", "best30_position"]
    ).to_csv(out_dir / "best30_components.csv", index=False)

    # Plot: the exact public weight curve.
    plt.figure(figsize=(9, 5.2))
    plt.plot(weights["best30_position"], weights["weight"])
    plt.xlabel("Position within personal Best 30")
    plt.ylabel("Weight")
    plt.title("MCHel Best-30 Weight Curve")
    plt.tight_layout()
    plt.savefig(out_dir / "weight_curve.png", dpi=180)
    plt.close()

    # Plot: official ranking's rating distribution.
    plt.figure(figsize=(9, 5.2))
    lower = max(100.0, math.floor(ranking["published_rating"].min() / 25.0) * 25.0)
    bins = np.arange(lower, RATING_MAX + 25.0, 25.0)
    if len(bins) < 2:
        bins = np.array([RATING_MIN, RATING_MAX])
    plt.hist(ranking["published_rating"], bins=bins)
    for boundary in [700, 800, 900, 1000, 1100, 1200]:
        plt.axvline(boundary, linewidth=1)
    plt.xlabel("Published rating")
    plt.ylabel("Players")
    plt.title("MCHel Overall Rating Distribution")
    plt.tight_layout()
    plt.savefig(out_dir / "rating_distribution.png", dpi=180)
    plt.close()

    tier_order = ["D", "C", "B", "A", "S", "SS", "SSS"]
    tier_counts = (
        ranking["tier"]
        .value_counts()
        .reindex(tier_order, fill_value=0)
        .to_dict()
    )
    metadata = {
        "input_csv": str(input_csv.resolve()),
        "input_rows": int(len(raw)),
        "rows_after_deduplication": int(len(work)),
        "course_count": int(len(course_summary)),
        "eligible_course_count": int(course_summary["eligible_for_overall"].sum()),
        "eligible_course_rule": "ranking_row_count == 100",
        "official_player_count": int(len(ranking)),
        "official_player_rule": f"eligible_course_count >= {BEST_N}",
        "best_n": BEST_N,
        "weight_parameters": {
            "type": "right_shifted_inverse_logistic",
            "midpoint": WEIGHT_MIDPOINT,
            "steepness": WEIGHT_STEEPNESS,
            "position_1_weight": float(weights["weight"].iloc[0]),
            "position_30_weight": float(weights["weight"].iloc[-1]),
        },
        "course_score_formula": "100 * course_record_time_ms / player_time_ms",
        "published_rating_formula": (
            "R = 100 + 1100 * (h(P)-h(0))/(h(100)-h(0)); "
            "h(P)=ln((P+0.5)/(100.5-P))"
        ),
        "published_rating_range": [RATING_MIN, RATING_MAX],
        "tier_counts": tier_counts,
    }
    with (out_dir / "rating_metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return ranking


def main() -> int:
    args = parse_args()
    try:
        ranking = calculate_ratings(args.input_csv, args.out_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Created {len(ranking)} official player ratings in: {args.out_dir.resolve()}")
    print("Main output:", (args.out_dir / "overall_ranking.csv").resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

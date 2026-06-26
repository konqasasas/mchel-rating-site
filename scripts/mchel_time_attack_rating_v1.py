#!/usr/bin/env python3
"""
MCHel Time-Attack Overall Rating v1

Calculates course difficulty and player rating from ONE rankings CSV snapshot.

Input requirements
------------------
Required columns:
  course_name, player_uuid, player_name, time_ms

Optional columns:
  category, recorded_at_epoch, rank

Important:
- `rank` is NOT used to calculate score or rating.
- A course is eligible for the overall calculation when it has exactly 100
  valid, deduplicated player-course results in the input CSV.
- A player is officially rated when they have results on at least 30 eligible
  courses.

Run:
  pip install pandas numpy
  python mchel_time_attack_rating_v1.py rankings.csv --out-dir rating_output

Outputs (all CSV files use UTF-8 with BOM for Excel compatibility):
  course_summary.csv
  course_difficulty.csv
  best30_weights.csv
  overall_ranking.csv
  best30_components.csv  (includes source course rank for display/audit)
  catalog_theoretical_max_top30_difficulty.csv
  rating_metadata.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# =============================================================================
# Published constants: Rating Formula v1
# =============================================================================

FORMULA_VERSION = "mchel_time_attack_rating_v1"

# Overall eligibility
ELIGIBLE_COURSE_ROW_COUNT = 100
BEST_N = 30

# Course-difficulty multiplier
ALPHA = 0.10

# Best-30 right-shifted inverse-logistic weight curve
WEIGHT_FLOOR = 0.50
WEIGHT_MIDPOINT = 20.0
WEIGHT_STEEPNESS = 0.18

# Public rating conversion
RAW_FORMULA_MIN = 0.0
RAW_FORMULA_MAX = 100.0 * (1.0 + ALPHA)  # 110.0

# P <= 100: flattened normal segment, maps 0 -> 100 and 100 -> 1000
LOWER_LOGIT_LEFT_MARGIN = 50.0
LOWER_LOGIT_RIGHT_MARGIN = 5.0
LOWER_RATING_MIN = 100.0
LOWER_RATING_MAX = 1000.0

# P > 100: elite segment, maps 100 -> 1000 and 110 -> 1600
ELITE_LOGIT_LEFT_MARGIN = 26.25
ELITE_LOGIT_RIGHT_MARGIN = 400.0
ELITE_RATING_MAX = 1600.0

REQUIRED_COLUMNS = {"course_name", "player_uuid", "player_name", "time_ms"}


# =============================================================================
# CLI and input handling
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate MCHel Time-Attack course difficulty and overall rating."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Rankings CSV snapshot.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("mchel_rating_v1_output"),
        help="Output directory (default: mchel_rating_v1_output).",
    )
    parser.add_argument(
        "--difficulty-table",
        type=Path,
        default=None,
        help=(
            "Frozen course_difficulty.csv to use for the multiplier table. "
            "When omitted, calculate a fresh table from this snapshot."
        ),
    )
    parser.add_argument(
        "--difficulty-version",
        default="snapshot_calculated",
        help="Stable identifier of the difficulty table used for this run.",
    )
    return parser.parse_args()


def require_columns(df: pd.DataFrame, required: Iterable[str]) -> None:
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise ValueError(
            "Input CSV is missing required columns: " + ", ".join(missing)
        )


def latest_player_names(raw: pd.DataFrame) -> pd.Series:
    """
    Choose a display name for each UUID.

    `player_uuid` is the stable identifier. `player_name` is display-only.
    If recorded_at_epoch exists, use the latest name; otherwise use the last
    name appearing in the input file.
    """
    names = raw[["player_uuid", "player_name"]].copy()
    if "recorded_at_epoch" in raw.columns:
        names["_recorded_at_epoch"] = pd.to_numeric(
            raw["recorded_at_epoch"], errors="coerce"
        )
        names = names.sort_values(
            ["player_uuid", "_recorded_at_epoch"],
            kind="stable",
            na_position="first",
        )
    return names.groupby("player_uuid", sort=False)["player_name"].last()


def deduplicate_player_course_rows(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Keep one fastest result per course_name x player_uuid.

    A valid snapshot normally contains no duplicates. If duplicates are found,
    use the fastest time, and on an exact time tie retain the newest record
    when recorded_at_epoch is available.
    """
    key = ["course_name", "player_uuid"]
    duplicate_mask = raw.duplicated(key, keep=False)
    if not duplicate_mask.any():
        return raw.copy()

    duplicate_rows = int(duplicate_mask.sum())
    duplicate_pairs = int(raw.loc[duplicate_mask].groupby(key).ngroups)
    warnings.warn(
        f"Found {duplicate_rows} rows in {duplicate_pairs} duplicate course/player "
        "pairs. Keeping the fastest valid result in each pair.",
        RuntimeWarning,
        stacklevel=2,
    )

    work = raw.copy()
    sort_columns = ["course_name", "player_uuid", "time_ms"]
    ascending = [True, True, True]

    if "recorded_at_epoch" in work.columns:
        work["_recorded_at_epoch"] = pd.to_numeric(
            work["recorded_at_epoch"], errors="coerce"
        )
        sort_columns.append("_recorded_at_epoch")
        ascending.append(False)

    work = work.sort_values(sort_columns, ascending=ascending, kind="stable")
    work = work.drop_duplicates(key, keep="first")
    return work.drop(columns="_recorded_at_epoch", errors="ignore")


# =============================================================================
# Course difficulty
# =============================================================================

def build_course_summary(raw: pd.DataFrame, work: pd.DataFrame) -> pd.DataFrame:
    raw_counts = raw.groupby("course_name").size().rename("raw_ranking_row_count")
    summary = (
        work.groupby("course_name", as_index=False)
        .agg(
            ranking_row_count=("player_uuid", "size"),
            unique_players=("player_uuid", "nunique"),
            course_record_time_ms=("time_ms", "min"),
        )
        .merge(raw_counts, on="course_name", how="left", validate="one_to_one")
    )

    if "category" in work.columns:
        category = (
            work.groupby("course_name", as_index=False)["category"]
            .first()
        )
        summary = summary.merge(category, on="course_name", how="left")
    else:
        summary["category"] = ""

    summary["eligible_for_overall"] = (
        summary["ranking_row_count"] == ELIGIBLE_COURSE_ROW_COUNT
    )
    return summary[
        [
            "course_name",
            "category",
            "raw_ranking_row_count",
            "ranking_row_count",
            "unique_players",
            "course_record_time_ms",
            "eligible_for_overall",
        ]
    ].sort_values(
        ["eligible_for_overall", "ranking_row_count", "course_name"],
        ascending=[False, False, True],
        kind="stable",
    )


def calculate_course_difficulty(
    eligible: pd.DataFrame,
    course_summary: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate 1st-place difficulty for each eligible course.

    Let t1..t10 be top 10 times on one course.

    Raw first-place outlierness:
      ln(median(t2,t3,t4,t5) / t1)

    Raw top-field spread:
      ln(t10 / t2)
    A smaller spread means a denser upper field.

    Both measures are converted to mid-rank percentiles within the current
    eligible course catalog. Top density is direction-reversed so larger
    values are denser. The final difficulty is their harmonic mean.

      D = 2 * O * C / (O + C)
    """
    top10 = (
        eligible.sort_values(
            ["course_name", "time_ms", "player_uuid"],
            ascending=[True, True, True],
            kind="stable",
        )
        .groupby("course_name", group_keys=False)
        .head(10)
        .copy()
    )
    top10["position"] = top10.groupby("course_name").cumcount() + 1

    times = top10.pivot(
        index="course_name",
        columns="position",
        values="time_ms",
    )
    times = times.rename(columns=lambda position: f"rank{position}_time_ms")

    required_top_columns = [f"rank{i}_time_ms" for i in range(1, 11)]
    if set(required_top_columns) - set(times.columns):
        raise ValueError(
            "Every eligible course must contain at least top-10 data. "
            "This should be guaranteed by the 100-row eligibility rule."
        )

    course_meta = (
        course_summary.loc[
            course_summary["eligible_for_overall"],
            ["course_name", "category", "ranking_row_count", "unique_players"],
        ]
        .set_index("course_name")
    )

    difficulty = course_meta.join(times, how="inner").reset_index()
    difficulty["rank2to5_median_time_ms"] = difficulty[
        [f"rank{i}_time_ms" for i in range(2, 6)]
    ].median(axis=1)

    difficulty["first_place_outlierness_raw"] = np.log(
        difficulty["rank2to5_median_time_ms"] / difficulty["rank1_time_ms"]
    )
    difficulty["top_density_spread_raw"] = np.log(
        difficulty["rank10_time_ms"] / difficulty["rank2_time_ms"]
    )

    course_count = len(difficulty)
    if course_count == 0:
        raise ValueError("No eligible courses were found.")

    # Mid-rank percentile: strictly inside (0,1), which keeps the harmonic
    # mean defined without arbitrary clipping at zero.
    outlier_rank = difficulty["first_place_outlierness_raw"].rank(
        method="average", ascending=True
    )
    density_spread_rank = difficulty["top_density_spread_raw"].rank(
        method="average", ascending=True
    )

    difficulty["first_place_outlierness"] = (outlier_rank - 0.5) / course_count
    difficulty["top_density"] = 1.0 - (
        density_spread_rank - 0.5
    ) / course_count

    difficulty["first_place_difficulty"] = (
        2.0
        * difficulty["first_place_outlierness"]
        * difficulty["top_density"]
        / (
            difficulty["first_place_outlierness"]
            + difficulty["top_density"]
        )
    )
    difficulty["difficulty_multiplier"] = (
        1.0 + ALPHA * difficulty["first_place_difficulty"]
    )

    difficulty["rank1_to_rank2to5_median_percent_gap"] = 100.0 * (
        difficulty["rank2to5_median_time_ms"] / difficulty["rank1_time_ms"] - 1.0
    )
    difficulty["rank2_to_rank10_percent_spread"] = 100.0 * (
        difficulty["rank10_time_ms"] / difficulty["rank2_time_ms"] - 1.0
    )

    difficulty = difficulty.sort_values(
        ["first_place_difficulty", "course_name"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    difficulty.insert(0, "difficulty_rank", np.arange(1, len(difficulty) + 1))

    output_columns = [
        "difficulty_rank",
        "course_name",
        "category",
        "ranking_row_count",
        "unique_players",
        "rank1_time_ms",
        "rank2_time_ms",
        "rank10_time_ms",
        "rank2to5_median_time_ms",
        "rank1_to_rank2to5_median_percent_gap",
        "rank2_to_rank10_percent_spread",
        "first_place_outlierness_raw",
        "top_density_spread_raw",
        "first_place_outlierness",
        "top_density",
        "first_place_difficulty",
        "difficulty_multiplier",
    ]
    return difficulty[output_columns]


def load_frozen_difficulty_table(
    difficulty_table: Path,
    course_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Load a previously published difficulty table for ordinary daily runs.

    The v1 method keeps the difficulty table fixed between planned refreshes.
    A new eligible course that is absent from the frozen table is treated as a
    deliberate maintenance event: the run fails instead of silently assigning
    it a newly calculated multiplier.
    """
    if not difficulty_table.exists():
        raise FileNotFoundError(f"Frozen difficulty table was not found: {difficulty_table}")

    frozen = pd.read_csv(difficulty_table)
    required = {
        "course_name",
        "difficulty_rank",
        "first_place_difficulty",
        "difficulty_multiplier",
    }
    require_columns(frozen, required)

    frozen = frozen.copy()
    frozen["course_name"] = frozen["course_name"].astype("string").str.strip()
    if frozen["course_name"].isna().any() or frozen["course_name"].eq("").any():
        raise ValueError("Frozen difficulty table contains an empty course_name.")
    if frozen["course_name"].duplicated().any():
        raise ValueError("Frozen difficulty table contains duplicate course_name values.")

    for column in ["first_place_difficulty", "difficulty_multiplier"]:
        frozen[column] = pd.to_numeric(frozen[column], errors="coerce")
    if frozen[["first_place_difficulty", "difficulty_multiplier"]].isna().any().any():
        raise ValueError("Frozen difficulty table has non-numeric difficulty values.")

    eligible_names = course_summary.loc[
        course_summary["eligible_for_overall"], "course_name"
    ].astype(str)
    frozen_by_course = frozen.set_index("course_name", drop=False)
    missing = sorted(set(eligible_names) - set(frozen_by_course.index))
    if missing:
        preview = ", ".join(missing[:8])
        suffix = " …" if len(missing) > 8 else ""
        raise ValueError(
            "The frozen difficulty table does not include newly eligible courses: "
            f"{preview}{suffix}. Refresh the difficulty table deliberately."
        )

    # Preserve the published table exactly for auditability, while restricting
    # it to courses that are eligible in the current snapshot.
    selected = frozen_by_course.loc[list(eligible_names)].copy()
    if "category" not in selected.columns:
        categories = course_summary.set_index("course_name")["category"]
        selected["category"] = selected.index.to_series().map(categories).fillna("")

    # course_name is intentionally both the lookup index and a CSV column at
    # this point; remove the index before sort_values to avoid ambiguity.
    selected = selected.reset_index(drop=True)
    selected = selected.sort_values(
        ["difficulty_rank", "course_name"],
        ascending=[True, True],
        kind="stable",
    ).reset_index(drop=True)
    return selected


# =============================================================================
# Player score and rating
# =============================================================================

def make_best30_weights() -> pd.DataFrame:
    """
    Build the published right-shifted inverse-logistic weight curve.

    Position 1 = 1.00 exactly
    Position 30 = 0.50 exactly
    """
    positions = np.arange(1, BEST_N + 1, dtype=float)
    inverse_logistic = 1.0 / (
        1.0 + np.exp(WEIGHT_STEEPNESS * (positions - WEIGHT_MIDPOINT))
    )
    weights = WEIGHT_FLOOR + (1.0 - WEIGHT_FLOOR) * (
        inverse_logistic - inverse_logistic[-1]
    ) / (
        inverse_logistic[0] - inverse_logistic[-1]
    )

    weights[0] = 1.0
    weights[-1] = WEIGHT_FLOOR

    return pd.DataFrame(
        {
            "best30_position": positions.astype(int),
            "weight": weights,
        }
    )


def public_rating(raw_performance_index: pd.Series) -> pd.Series:
    """
    Convert raw P into the published 100..1600 rating without clipping.

    P in [0,100]:
      h(P)=ln((P+50)/(105-P))
      R=100+900*(h(P)-h(0))/(h(100)-h(0))

    P in (100,110]:
      x=P-100
      g(x)=ln((x+26.25)/(410-x))
      R=1000+600*(g(x)-g(0))/(g(10)-g(0))
    """
    p = raw_performance_index.astype(float)
    tolerance = 1e-10
    bad = (p < RAW_FORMULA_MIN - tolerance) | (
        p > RAW_FORMULA_MAX + tolerance
    )
    if bad.any():
        examples = p.loc[bad].head(5).tolist()
        raise ValueError(
            "Raw performance index fell outside its theoretical [0, 110] range. "
            f"Examples: {examples}"
        )

    rating = pd.Series(index=p.index, dtype=float)
    lower_mask = p <= 100.0

    def lower_h(x: float | pd.Series) -> float | pd.Series:
        return np.log(
            (x + LOWER_LOGIT_LEFT_MARGIN)
            / (100.0 + LOWER_LOGIT_RIGHT_MARGIN - x)
        )

    lower_h0 = lower_h(0.0)
    lower_h100 = lower_h(100.0)
    rating.loc[lower_mask] = LOWER_RATING_MIN + (
        LOWER_RATING_MAX - LOWER_RATING_MIN
    ) * (
        lower_h(p.loc[lower_mask]) - lower_h0
    ) / (
        lower_h100 - lower_h0
    )

    elite_mask = ~lower_mask
    if elite_mask.any():
        x = p.loc[elite_mask] - 100.0

        def elite_g(v: float | pd.Series) -> float | pd.Series:
            return np.log(
                (v + ELITE_LOGIT_LEFT_MARGIN)
                / (10.0 + ELITE_LOGIT_RIGHT_MARGIN - v)
            )

        elite_g0 = elite_g(0.0)
        elite_g10 = elite_g(10.0)
        rating.loc[elite_mask] = LOWER_RATING_MAX + (
            ELITE_RATING_MAX - LOWER_RATING_MAX
        ) * (
            elite_g(x) - elite_g0
        ) / (
            elite_g10 - elite_g0
        )

    return rating


def assign_tier(rating: float) -> str:
    """Final public tier names. Boundaries use unrounded rating."""
    if rating >= 1300.0:
        return "EX"
    if rating >= 1250.0:
        return "S+"
    if rating >= 1200.0:
        return "S"
    if rating >= 1150.0:
        return "A+"
    if rating >= 1100.0:
        return "A"
    if rating >= 1050.0:
        return "B+"
    if rating >= 1000.0:
        return "B"
    if rating >= 950.0:
        return "B-"
    if rating >= 900.0:
        return "C+"
    if rating >= 850.0:
        return "C"
    if rating >= 800.0:
        return "C-"
    if rating >= 750.0:
        return "D+"
    if rating >= 700.0:
        return "D"
    return "E"


def calculate_player_ratings(
    eligible: pd.DataFrame,
    difficulty: pd.DataFrame,
    latest_names: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """
    Calculate difficulty-adjusted course scores, individual Best 30, and
    public ratings.
    """
    course_records = (
        eligible.groupby("course_name")["time_ms"]
        .min()
        .rename("course_record_time_ms")
    )

    work = (
        eligible.merge(
            course_records,
            on="course_name",
            how="left",
            validate="many_to_one",
        )
        .merge(
            difficulty[
                [
                    "course_name",
                    "difficulty_rank",
                    "first_place_difficulty",
                    "difficulty_multiplier",
                ]
            ],
            on="course_name",
            how="left",
            validate="many_to_one",
        )
        .copy()
    )

    work["base_course_score"] = (
        100.0 * work["course_record_time_ms"] / work["time_ms"]
    )
    work["difficulty_adjusted_course_score"] = (
        work["base_course_score"] * work["difficulty_multiplier"]
    )

    if work["base_course_score"].gt(100.0 + 1e-10).any():
        raise AssertionError(
            "A base course score exceeded 100. Check course record calculation."
        )
    if work["difficulty_adjusted_course_score"].gt(
        RAW_FORMULA_MAX + 1e-10
    ).any():
        raise AssertionError(
            "A difficulty-adjusted score exceeded the formula ceiling."
        )

    eligible_course_count = (
        work.groupby("player_uuid")["course_name"]
        .nunique()
        .rename("eligible_course_count")
    )
    official_uuids = eligible_course_count[
        eligible_course_count >= BEST_N
    ].index

    official = work.loc[work["player_uuid"].isin(official_uuids)].copy()
    official = official.sort_values(
        ["player_uuid", "difficulty_adjusted_course_score", "course_name"],
        ascending=[True, False, True],
        kind="stable",
    )

    best30 = official.groupby(
        "player_uuid", group_keys=False
    ).head(BEST_N).copy()
    best30["best30_position"] = best30.groupby("player_uuid").cumcount() + 1

    weights = make_best30_weights()
    best30 = best30.merge(
        weights,
        on="best30_position",
        how="left",
        validate="many_to_one",
    )
    best30["weighted_contribution"] = (
        best30["difficulty_adjusted_course_score"] * best30["weight"]
    )

    weight_sum = float(weights["weight"].sum())
    ratings = (
        best30.groupby("player_uuid", as_index=False)
        .agg(
            weighted_score_sum=("weighted_contribution", "sum"),
            raw_performance_index=(
                "weighted_contribution",
                lambda values: float(values.sum()) / weight_sum,
            ),
            best_course_score=("difficulty_adjusted_course_score", "max"),
            thirtieth_course_score=("difficulty_adjusted_course_score", "min"),
        )
    )

    ratings["published_rating"] = public_rating(
        ratings["raw_performance_index"]
    )
    ratings["tier"] = [
        assign_tier(value) for value in ratings["published_rating"]
    ]
    ratings["eligible_course_count"] = ratings["player_uuid"].map(
        eligible_course_count
    )
    ratings["player_name"] = ratings["player_uuid"].map(latest_names)

    ratings = ratings.sort_values(
        ["published_rating", "player_uuid"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    ratings.insert(0, "overall_rank", np.arange(1, len(ratings) + 1))

    best30["player_name"] = best30["player_uuid"].map(latest_names)
    best30 = best30.merge(
        ratings[
            [
                "player_uuid",
                "overall_rank",
                "tier",
                "raw_performance_index",
                "published_rating",
            ]
        ],
        on="player_uuid",
        how="left",
        validate="many_to_one",
    )

    return ratings, best30, eligible_course_count


# =============================================================================
# Current-catalog theoretical maximum
# =============================================================================

def calculate_catalog_theoretical_max(
    difficulty: pd.DataFrame,
    weights: pd.DataFrame,
) -> tuple[pd.DataFrame, float, float]:
    """
    Catalog-constrained maximum under the frozen current difficulty table:

    Select the 30 highest-difficulty eligible courses and assume first place
    on all of them. The Best-30 weight curve still applies.

    This differs from the mathematical ceiling P=110 / R=1600, which would
    require all 30 selected courses to have difficulty exactly 1.0.
    """
    top30 = difficulty.sort_values(
        ["first_place_difficulty", "course_name"],
        ascending=[False, True],
        kind="stable",
    ).head(BEST_N).copy()

    if len(top30) < BEST_N:
        raise ValueError(
            f"At least {BEST_N} eligible courses are required to calculate "
            "the catalog-constrained maximum."
        )

    top30["best30_position"] = np.arange(1, BEST_N + 1)
    top30 = top30.merge(
        weights,
        on="best30_position",
        how="left",
        validate="one_to_one",
    )
    top30["theoretical_course_score_at_rank1"] = (
        100.0 * top30["difficulty_multiplier"]
    )
    top30["weighted_contribution"] = (
        top30["theoretical_course_score_at_rank1"] * top30["weight"]
    )

    raw_max = float(
        top30["weighted_contribution"].sum() / top30["weight"].sum()
    )
    rating_max = float(public_rating(pd.Series([raw_max])).iloc[0])

    output_columns = [
        "best30_position",
        "course_name",
        "category",
        "first_place_difficulty",
        "difficulty_multiplier",
        "weight",
        "theoretical_course_score_at_rank1",
        "weighted_contribution",
    ]
    return top30[output_columns], raw_max, rating_max


# =============================================================================
# Main process and output
# =============================================================================

def write_csv(df: pd.DataFrame, destination: Path) -> None:
    """Write an Excel-friendly UTF-8 BOM CSV."""
    df.to_csv(destination, index=False, encoding="utf-8-sig")


def calculate(
    input_csv: Path,
    output_dir: Path,
    difficulty_table: Path | None = None,
    difficulty_version: str = "snapshot_calculated",
) -> dict[str, object]:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV was not found: {input_csv}")

    output_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(input_csv)
    require_columns(raw, REQUIRED_COLUMNS)

    raw["course_name"] = raw["course_name"].astype("string").str.strip()
    raw["player_uuid"] = raw["player_uuid"].astype("string").str.strip()
    raw["player_name"] = raw["player_name"].astype("string").fillna("").str.strip()
    raw["time_ms"] = pd.to_numeric(raw["time_ms"], errors="coerce")

    if "category" in raw.columns:
        raw["category"] = raw["category"].astype("string").fillna("").str.strip()

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
            f"The input has {int(invalid.sum())} invalid rows: each row must "
            "have course_name, player_uuid, and a positive numeric time_ms."
        )

    latest_names = latest_player_names(raw)
    work = deduplicate_player_course_rows(raw)
    # Keep the source ranking position for audit and player-page display. It is
    # never used by the rating formula. A blank value keeps the output schema
    # stable if a future input snapshot omits the optional rank column.
    if "rank" not in work.columns:
        work["rank"] = pd.NA
    course_summary = build_course_summary(raw, work)

    eligible_names = course_summary.loc[
        course_summary["eligible_for_overall"], "course_name"
    ]
    eligible = work.loc[work["course_name"].isin(eligible_names)].copy()

    if difficulty_table is None:
        difficulty = calculate_course_difficulty(eligible, course_summary)
        difficulty_source = "calculated_from_current_snapshot"
    else:
        difficulty = load_frozen_difficulty_table(difficulty_table, course_summary)
        difficulty_source = "frozen_difficulty_table"

    weights = make_best30_weights()
    ratings, best30, eligible_course_counts = calculate_player_ratings(
        eligible=eligible,
        difficulty=difficulty,
        latest_names=latest_names,
    )
    theoretical_top30, catalog_raw_max, catalog_rating_max = (
        calculate_catalog_theoretical_max(difficulty, weights)
    )

    # Output file names are intentionally stable for scheduled snapshots.
    write_csv(course_summary, output_dir / "course_summary.csv")
    write_csv(difficulty, output_dir / "course_difficulty.csv")
    write_csv(weights, output_dir / "best30_weights.csv")

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
    write_csv(ratings[ranking_columns], output_dir / "overall_ranking.csv")

    component_columns = [
        "overall_rank",
        "tier",
        "player_uuid",
        "player_name",
        "published_rating",
        "raw_performance_index",
        "best30_position",
        "weight",
        "course_name",
        "category",
        "rank",
        "time_ms",
        "course_record_time_ms",
        "difficulty_rank",
        "first_place_difficulty",
        "difficulty_multiplier",
        "base_course_score",
        "difficulty_adjusted_course_score",
        "weighted_contribution",
    ]
    write_csv(
        best30[component_columns].sort_values(
            ["overall_rank", "best30_position"],
            ascending=[True, True],
            kind="stable",
        ),
        output_dir / "best30_components.csv",
    )
    write_csv(
        theoretical_top30,
        output_dir / "catalog_theoretical_max_top30_difficulty.csv",
    )

    tier_order = [
        "E", "D", "D+", "C-", "C", "C+", "B-", "B", "B+",
        "A", "A+", "S", "S+", "EX",
    ]
    tier_counts = (
        ratings["tier"]
        .value_counts()
        .reindex(tier_order, fill_value=0)
        .to_dict()
    )

    metadata = {
        "formula_version": FORMULA_VERSION,
        "difficulty_version": difficulty_version,
        "difficulty_source": difficulty_source,
        "difficulty_table": (
            str(difficulty_table.resolve()) if difficulty_table is not None else None
        ),
        "input_csv": str(input_csv.resolve()),
        "input_rows": int(len(raw)),
        "rows_after_player_course_deduplication": int(len(work)),
        "course_count": int(len(course_summary)),
        "eligible_course_count": int(len(eligible_names)),
        "eligible_course_rule": (
            "Course has exactly 100 valid, deduplicated player-course rows."
        ),
        "official_player_count": int(len(ratings)),
        "official_player_rule": (
            "Player has results on at least 30 eligible courses."
        ),
        "scoring": {
            "base_course_score": (
                "100 * course_record_time_ms / player_time_ms"
            ),
            "difficulty_multiplier": (
                "1 + 0.10 * first_place_difficulty"
            ),
            "difficulty_adjusted_course_score": (
                "base_course_score * difficulty_multiplier"
            ),
            "best_n": BEST_N,
            "best30_weight": {
                "type": "right_shifted_inverse_logistic",
                "midpoint": WEIGHT_MIDPOINT,
                "steepness": WEIGHT_STEEPNESS,
                "position_1_weight": float(weights["weight"].iloc[0]),
                "position_30_weight": float(weights["weight"].iloc[-1]),
            },
        },
        "course_difficulty": {
            "first_place_outlierness_raw": (
                "ln(median(rank2..rank5 time) / rank1 time)"
            ),
            "top_density_spread_raw": "ln(rank10 time / rank2 time)",
            "top_density": (
                "Reverse mid-rank percentile of top_density_spread_raw"
            ),
            "first_place_difficulty": (
                "Harmonic mean of outlierness percentile and top-density percentile"
            ),
        },
        "rating_transform": {
            "raw_formula_range": [RAW_FORMULA_MIN, RAW_FORMULA_MAX],
            "normal_segment": {
                "raw_range": [0, 100],
                "rating_range": [100, 1000],
                "formula": (
                    "h(P)=ln((P+50)/(105-P)); "
                    "R=100+900*(h(P)-h(0))/(h(100)-h(0))"
                ),
            },
            "elite_segment": {
                "raw_range": [100, 110],
                "rating_range": [1000, 1600],
                "formula": (
                    "x=P-100; g(x)=ln((x+26.25)/(410-x)); "
                    "R=1000+600*(g(x)-g(0))/(g(10)-g(0))"
                ),
            },
        },
        "tier_counts": {tier: int(count) for tier, count in tier_counts.items()},
        "catalog_constrained_theoretical_max": {
            "definition": (
                "First place on the 30 currently highest-difficulty courses, "
                "with the current difficulty table frozen."
            ),
            "raw_performance_index": catalog_raw_max,
            "published_rating": catalog_rating_max,
            "tier": assign_tier(catalog_rating_max),
        },
        "mathematical_formula_ceiling": {
            "raw_performance_index": RAW_FORMULA_MAX,
            "published_rating": ELITE_RATING_MAX,
            "note": (
                "Requires all 30 selected courses to have difficulty 1.0; "
                "not currently possible with the catalog."
            ),
        },
    }
    (output_dir / "rating_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return metadata


def main() -> int:
    args = parse_args()

    try:
        metadata = calculate(
            args.input_csv,
            args.out_dir,
            difficulty_table=args.difficulty_table,
            difficulty_version=args.difficulty_version,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    theoretical = metadata["catalog_constrained_theoretical_max"]
    print(f"Formula version: {FORMULA_VERSION}")
    print(f"Difficulty version: {metadata['difficulty_version']}")
    print(f"Difficulty source: {metadata['difficulty_source']}")
    print(f"Eligible courses: {metadata['eligible_course_count']}")
    print(f"Official players: {metadata['official_player_count']}")
    print(
        "Current-catalog theoretical maximum: "
        f"{theoretical['published_rating']:.6f} ({theoretical['tier']})"
    )
    print(f"Output directory: {args.out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

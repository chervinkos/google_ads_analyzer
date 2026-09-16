#!/usr/bin/env python3
"""Search-term waste analysis.

Flags a search term as wasted spend if it clears the cost threshold
(regardless of clicks) OR clears the click threshold with low/zero
conversions. Cost-based flags always take priority over the click floor.
Rows without a usable search term or campaign are reported separately as
insufficient data rather than silently dropped.

Usage:
    analyze_wasted_spend.py --config configs/<project>.yaml --input search_terms.csv
        [--min-clicks N] [--min-cost N] [--output file.csv]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

REQUIRED_COLUMNS = ["search_term", "campaign", "clicks", "cost"]
OUTPUT_FIELDS = [
    "search_term", "campaign", "ad_group", "cluster", "brand_term",
    "clicks", "cost", "conversions", "reason",
]

# Conversions below this count as "low/zero" for the click-floor rule.
LOW_CONVERSIONS_THRESHOLD = 1.0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--input", required=True, help="Search-term CSV (already joined to campaign names)")
    parser.add_argument("--min-clicks", type=float, default=None, help="Override config thresholds.min_clicks")
    parser.add_argument("--min-cost", type=float, default=None, help="Override config thresholds.min_cost")
    parser.add_argument("--output", default=None, help="Output CSV path (default: <project>-wasted-spend.csv)")
    return parser.parse_args()


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    brand_keywords = config.get("brand_keywords", [])
    thresholds = config.get("thresholds", {})

    min_clicks = args.min_clicks if args.min_clicks is not None else thresholds.get("min_clicks", 0)
    min_cost = args.min_cost if args.min_cost is not None else thresholds.get("min_cost", 0)
    output_path = args.output or f"{project_name}-wasted-spend.csv"

    records = common.load_table(args.input)
    if not records:
        sys.exit(f"No data rows found in {args.input}")

    cols = common.resolve_columns(records[0].keys(), REQUIRED_COLUMNS + ["ad_group", "conversions"])
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {args.input} "
                  f"(saw headers: {list(records[0].keys())})")

    # Resolved once per numeric column (not per cell) - see
    # infer_decimal_style() in ads_common.py: a single ambiguous cell like
    # "22.283" can't tell decimal from thousands-grouped on its own, but an
    # unambiguous sibling elsewhere in the same column can, and usually is
    # there (e.g. a fractional Google Ads conversions value like 73.3006).
    decimal_styles = common.resolve_decimal_styles(records, cols, ["clicks", "cost", "conversions"])

    flagged, insufficient = [], []
    total_cost = 0.0
    cluster_breakdown = {}

    for row in records:
        search_term = row.get(cols["search_term"], "").strip()
        campaign = row.get(cols["campaign"], "").strip()

        if not search_term or not campaign:
            insufficient.append(row)
            continue

        clicks = common.clean_number(row.get(cols["clicks"]), decimal_styles.get("clicks"))
        cost = common.clean_number(row.get(cols["cost"]), decimal_styles.get("cost"))
        conversions = common.clean_number(row.get(cols["conversions"]), decimal_styles.get("conversions")) if "conversions" in cols else 0.0

        cost_flag = cost >= min_cost
        click_flag = clicks >= min_clicks and conversions < LOW_CONVERSIONS_THRESHOLD
        if not (cost_flag or click_flag):
            continue

        reason = "cost_threshold" if cost_flag else "clicks_no_conversion"
        cluster = common.match_cluster(campaign, clusters)
        is_brand = common.matches_any_keyword(search_term, brand_keywords)

        flagged.append({
            "search_term": search_term,
            "campaign": campaign,
            "ad_group": row.get(cols.get("ad_group", ""), ""),
            "cluster": cluster,
            "brand_term": is_brand,
            "clicks": clicks,
            "cost": round(cost, 2),
            "conversions": conversions,
            "reason": reason,
        })
        total_cost += cost
        bucket = cluster_breakdown.setdefault(cluster, {"count": 0, "cost": 0.0})
        bucket["count"] += 1
        bucket["cost"] += cost

    flagged.sort(key=lambda r: r["cost"], reverse=True)
    common.write_csv(output_path, OUTPUT_FIELDS, flagged)

    insufficient_path = None
    if insufficient:
        insufficient_path = str(Path(output_path).with_name(
            Path(output_path).stem + "-insufficient-data.csv"))
        common.write_csv(insufficient_path, list(records[0].keys()), insufficient)

    print(f"Wasted spend analysis for {project_name}")
    print(f"  Input: {args.input}")
    print(f"  Thresholds: min_clicks={min_clicks}, min_cost={min_cost}")
    print(f"  Flagged terms: {len(flagged)}")
    print(f"  Total wasted spend: {total_cost:.2f}")
    print(f"  Insufficient-data rows: {len(insufficient)}"
          + (f" (see {insufficient_path})" if insufficient_path else ""))
    print("  Breakdown by cluster:")
    for cluster, stats in sorted(cluster_breakdown.items(), key=lambda kv: kv[1]["cost"], reverse=True):
        print(f"    {cluster}: {stats['count']} terms, {stats['cost']:.2f}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()

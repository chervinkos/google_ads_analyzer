#!/usr/bin/env python3
"""Search-term opportunity / pattern discovery.

Compares a trailing window against a baseline window (plus the current
active keyword list) to flag search terms worth acting on, across four
independent signal types:

  - new_demand:      falls to the catch-all cluster AND doesn't match any
                      other cluster's keywords by content either
  - intent_pattern:  matches one or more of the config's intent_keywords
                      (independent of geographic cluster)
  - underexploited:  strong conversion rate / low CAC, not yet an active
                      keyword
  - rising_trend:    meaningfully higher volume trailing vs. baseline

A term can carry multiple signals at once; all are reported together with
a suggested action.

Usage:
    analyze_search_opportunities.py --config configs/<project>.yaml \\
        --trailing trailing_search_terms.csv --baseline baseline_search_terms.csv \\
        --keywords current_keywords.csv [--output file.csv]
        [--min-conversions N] [--max-cac-ratio R] [--min-trend-clicks N] [--trend-growth-pct P]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

TERM_COLUMNS = ["search_term", "campaign", "clicks", "cost", "conversions"]
OUTPUT_FIELDS = [
    "search_term", "campaign", "cluster", "brand_term",
    "clicks", "cost", "conversions", "cpa",
    "baseline_clicks", "growth_pct",
    "signals", "suggested_action",
]

SUGGESTED_ACTIONS = {
    "new_demand": "Consider a new cluster/campaign for this term",
    "intent_pattern": "Route to an intent-specific ad group or landing page",
    "underexploited": "Add as an exact-match keyword",
    "rising_trend": "Increase bid/budget for this term",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--trailing", required=True, help="Trailing-window search-term CSV")
    parser.add_argument("--baseline", required=True, help="Baseline-window search-term CSV")
    parser.add_argument("--keywords", required=True, help="Current active keyword-list export CSV")
    parser.add_argument("--output", default=None, help="Output CSV path (default: <project>-opportunities.csv)")
    parser.add_argument("--min-conversions", type=float, default=1.0,
                         help="Minimum trailing conversions to qualify as underexploited (default 1)")
    parser.add_argument("--max-cac-ratio", type=float, default=0.5,
                         help="Flag as underexploited when CPA <= this fraction of the trailing account-average CPA (default 0.5)")
    parser.add_argument("--min-trend-clicks", type=float, default=5.0,
                         help="Minimum trailing clicks to qualify for the rising_trend signal (default 5)")
    parser.add_argument("--trend-growth-pct", type=float, default=50.0,
                         help="Minimum %% growth trailing vs. baseline clicks to qualify as rising_trend (default 50)")
    return parser.parse_args()


def resolve_term_columns(records, label):
    if not records:
        sys.exit(f"No data rows found in {label}")
    cols = common.resolve_columns(records[0].keys(), TERM_COLUMNS)
    missing = [c for c in ("search_term", "campaign", "clicks", "cost") if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {label} "
                  f"(saw headers: {list(records[0].keys())})")
    return cols


def aggregate_by_term(records, cols):
    """Group rows by search term, summing metrics across campaigns/ad
    groups, keeping the campaign of the highest-cost row for tagging."""
    # Resolved once per numeric column, not per cell - see
    # infer_decimal_style() in ads_common.py.
    decimal_styles = common.resolve_decimal_styles(records, cols, ["clicks", "cost", "conversions"])

    terms = {}
    for row in records:
        search_term = row.get(cols["search_term"], "").strip()
        if not search_term:
            continue
        campaign = row.get(cols["campaign"], "").strip()
        clicks = common.clean_number(row.get(cols["clicks"]), decimal_styles.get("clicks"))
        cost = common.clean_number(row.get(cols["cost"]), decimal_styles.get("cost"))
        conversions = common.clean_number(row.get(cols["conversions"]), decimal_styles.get("conversions")) if "conversions" in cols else 0.0

        key = search_term.lower()
        agg = terms.setdefault(key, {
            "search_term": search_term, "campaign": campaign, "top_cost": -1.0,
            "clicks": 0.0, "cost": 0.0, "conversions": 0.0,
        })
        agg["clicks"] += clicks
        agg["cost"] += cost
        agg["conversions"] += conversions
        if cost > agg["top_cost"]:
            agg["top_cost"] = cost
            agg["campaign"] = campaign
    return terms


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    brand_keywords = config.get("brand_keywords", [])
    intent_keywords = config.get("intent_keywords", [])
    cluster_keywords = common.flattened_cluster_keywords(clusters)

    catch_all_names = {c["name"] for c in clusters if c.get("match_campaign_name") == "catch-all"}

    output_path = args.output or f"{project_name}-opportunities.csv"

    trailing_records = common.load_table(args.trailing)
    baseline_records = common.load_table(args.baseline)
    keyword_records = common.load_table(args.keywords)

    # Trailing is the window being analyzed, so it must have both columns
    # and data. Baseline and the active-keyword list may legitimately be
    # empty (e.g. a brand-new term has no baseline history) - treat an
    # empty table there as "nothing to compare against", not an error.
    trailing_cols = resolve_term_columns(trailing_records, args.trailing)
    baseline_cols = common.resolve_columns(baseline_records[0].keys(), TERM_COLUMNS) if baseline_records else {}

    active_keywords = set()
    if keyword_records:
        kw_cols = common.resolve_columns(keyword_records[0].keys(), ["keyword"])
        if "keyword" not in kw_cols:
            sys.exit(f"Could not find a keyword column in {args.keywords} "
                      f"(saw headers: {list(keyword_records[0].keys())})")
        active_keywords = {
            row.get(kw_cols["keyword"], "").strip().lower()
            for row in keyword_records if row.get(kw_cols["keyword"], "").strip()
        }

    trailing_terms = aggregate_by_term(trailing_records, trailing_cols)
    baseline_terms = aggregate_by_term(baseline_records, baseline_cols) if baseline_records else {}

    converting = [t for t in trailing_terms.values() if t["conversions"] > 0]
    if converting:
        avg_cpa = sum(t["cost"] / t["conversions"] for t in converting) / len(converting)
    else:
        avg_cpa = None

    flagged = []
    signal_counts = {name: 0 for name in SUGGESTED_ACTIONS}

    for key, term in trailing_terms.items():
        search_term = term["search_term"]
        campaign = term["campaign"]
        clicks = term["clicks"]
        cost = term["cost"]
        conversions = term["conversions"]
        cpa = (cost / conversions) if conversions > 0 else None

        cluster = common.match_cluster(campaign, clusters)
        is_brand = common.matches_any_keyword(search_term, brand_keywords)
        intents = common.match_intents(search_term, intent_keywords)

        baseline = baseline_terms.get(key)
        baseline_clicks = baseline["clicks"] if baseline else 0.0
        if baseline_clicks > 0:
            growth_pct = ((clicks - baseline_clicks) / baseline_clicks) * 100
        else:
            growth_pct = None

        signals = []

        if cluster in catch_all_names and not common.matches_any_keyword(search_term, cluster_keywords):
            signals.append("new_demand")

        if intents:
            signals.append("intent_pattern")

        if (conversions >= args.min_conversions and avg_cpa is not None
                and cpa is not None and cpa <= avg_cpa * args.max_cac_ratio
                and search_term.lower() not in active_keywords):
            signals.append("underexploited")

        is_new_with_volume = baseline_clicks == 0 and clicks >= args.min_trend_clicks
        is_grown = (baseline_clicks > 0 and clicks >= args.min_trend_clicks
                    and growth_pct is not None and growth_pct >= args.trend_growth_pct)
        if is_new_with_volume or is_grown:
            signals.append("rising_trend")

        if not signals:
            continue

        for s in signals:
            signal_counts[s] += 1

        flagged.append({
            "search_term": search_term,
            "campaign": campaign,
            "cluster": cluster,
            "brand_term": is_brand,
            "clicks": clicks,
            "cost": round(cost, 2),
            "conversions": conversions,
            "cpa": round(cpa, 2) if cpa is not None else "",
            "baseline_clicks": baseline_clicks,
            "growth_pct": round(growth_pct, 1) if growth_pct is not None else ("new" if is_new_with_volume else ""),
            "signals": ";".join(signals + (intents if "intent_pattern" in signals else [])),
            "suggested_action": "; ".join(SUGGESTED_ACTIONS[s] for s in signals),
        })

    flagged.sort(key=lambda r: r["cost"], reverse=True)
    common.write_csv(output_path, OUTPUT_FIELDS, flagged)

    print(f"Opportunity discovery for {project_name}")
    print(f"  Trailing: {args.trailing} ({len(trailing_terms)} unique terms)")
    print(f"  Baseline: {args.baseline} ({len(baseline_terms)} unique terms)")
    print(f"  Active keywords: {args.keywords} ({len(active_keywords)} keywords)")
    print(f"  Flagged rows: {len(flagged)}")
    print("  Breakdown by signal type:")
    for name, count in signal_counts.items():
        print(f"    {name}: {count}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()

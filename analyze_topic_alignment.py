#!/usr/bin/env python3
"""Search-term topic alignment: content-derived topic vs. actual campaign
cluster (mismatch), the same search term appearing under multiple
distinct campaigns (duplication), and terms matching a tracked-but-
unserved country (tracked_no_campaign). All three signals read the same
merged search-term CSV already produced for analyze_wasted_spend.py /
analyze_search_opportunities.py - no new API pull required.

Usage:
    analyze_topic_alignment.py --config configs/<project>.yaml --input search_terms.csv
        [--campaigns campaign_list.csv]
        [--mismatch-output file.csv] [--duplication-output file.csv]
        [--tracked-output file.csv] [--unclassified-output file.csv]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

TERM_COLUMNS = ["search_term", "campaign", "ad_group", "clicks", "cost", "conversions"]

MISMATCH_FIELDS = [
    "search_term", "campaign", "ad_groups", "actual_cluster", "derived_topic",
    "topic_outcome", "clicks", "cost", "conversions", "suggested_action",
]
TRACKED_FIELDS = [
    "search_term", "campaign", "ad_groups", "topic",
    "clicks", "cost", "conversions", "suggested_action",
]
DUPLICATION_FIELDS = [
    "search_term", "total_cost", "distinct_campaign_count", "campaign", "cluster",
    "clicks", "cost", "conversions",
]
UNCLASSIFIED_FIELDS = [
    "search_term", "campaign", "ad_groups", "clicks", "cost", "conversions",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--input", required=True, help="Merged search-term CSV (Search + PMax)")
    parser.add_argument("--campaigns", default=None,
                         help="Optional supplementary campaign-list CSV (union of campaign names, "
                              "to catch campaigns with zero search-term rows in --input)")
    parser.add_argument("--mismatch-output", default=None, help="Default: <project>-topic-mismatch.csv")
    parser.add_argument("--duplication-output", default=None, help="Default: <project>-topic-duplication.csv")
    parser.add_argument("--tracked-output", default=None, help="Default: <project>-topic-tracked-countries.csv")
    parser.add_argument("--unclassified-output", default=None, help="Default: <project>-topic-unclassified.csv")
    return parser.parse_args()


def aggregate_by_term_campaign(records, cols):
    """Group rows by (search_term, campaign), summing metrics across ad
    groups. Unlike analyze_search_opportunities.aggregate_by_term (which
    collapses to a single representative campaign per term), this keeps
    every distinct campaign a term appears in - required for both the
    mismatch signal (a term can be correctly homed in one campaign and
    mismatched in another) and the duplication signal (which needs the
    full set of campaigns per term)."""
    aggregated = {}
    insufficient = []
    for row in records:
        search_term = row.get(cols["search_term"], "").strip()
        campaign = row.get(cols["campaign"], "").strip()
        if not search_term or not campaign:
            insufficient.append(row)
            continue

        ad_group = row.get(cols.get("ad_group", ""), "").strip() if "ad_group" in cols else ""
        clicks = common.clean_number(row.get(cols["clicks"]))
        cost = common.clean_number(row.get(cols["cost"]))
        conversions = common.clean_number(row.get(cols["conversions"])) if "conversions" in cols else 0.0

        key = (search_term.lower(), campaign)
        agg = aggregated.setdefault(key, {
            "search_term": search_term, "campaign": campaign, "ad_groups": set(),
            "clicks": 0.0, "cost": 0.0, "conversions": 0.0,
        })
        if ad_group:
            agg["ad_groups"].add(ad_group)
        agg["clicks"] += clicks
        agg["cost"] += cost
        agg["conversions"] += conversions
    return aggregated, insufficient


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    topic_keywords = config.get("topic_keywords", [])

    if not topic_keywords:
        sys.exit(f"Config {args.config} has no topic_keywords section - "
                  f"this script is inert without it (see CLAUDE.md's Known technical notes)")

    mismatch_output = args.mismatch_output or f"{project_name}-topic-mismatch.csv"
    duplication_output = args.duplication_output or f"{project_name}-topic-duplication.csv"
    tracked_output = args.tracked_output or f"{project_name}-topic-tracked-countries.csv"
    unclassified_output = args.unclassified_output or f"{project_name}-topic-unclassified.csv"

    records = common.load_table(args.input)
    if not records:
        sys.exit(f"No data rows found in {args.input}")

    cols = common.resolve_columns(records[0].keys(), TERM_COLUMNS)
    missing = [c for c in ("search_term", "campaign", "clicks", "cost") if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {args.input} "
                  f"(saw headers: {list(records[0].keys())})")

    aggregated, insufficient = aggregate_by_term_campaign(records, cols)

    all_campaign_names = {agg["campaign"] for agg in aggregated.values()}
    if args.campaigns:
        campaign_records = common.load_table(args.campaigns)
        if campaign_records:
            camp_cols = common.resolve_columns(campaign_records[0].keys(), ["campaign"])
            if "campaign" not in camp_cols:
                sys.exit(f"Could not find a campaign column in {args.campaigns} "
                          f"(saw headers: {list(campaign_records[0].keys())})")
            all_campaign_names |= {
                row.get(camp_cols["campaign"], "").strip()
                for row in campaign_records if row.get(camp_cols["campaign"], "").strip()
            }

    core_topic_names = {
        c["name"] for c in clusters if c.get("match_campaign_name") != "catch-all"
    } & {t["name"] for t in topic_keywords}

    cluster_campaign_map = common.campaigns_by_cluster(all_campaign_names, clusters)
    topic_campaign_map = common.campaigns_by_topic(all_campaign_names, topic_keywords)

    mismatch_rows = []
    tracked_rows = []
    unclassified_rows = []
    outcome_counts = {"single": 0, "tracked_no_campaign": 0, "ambiguous": 0, "none": 0}
    reason_counts = {"mismatch": 0, "ambiguous": 0, "re_home": 0, "negative": 0,
                      "tracked_campaign_exists": 0, "tracked_no_campaign_at_all": 0}

    for agg in aggregated.values():
        search_term = agg["search_term"]
        campaign = agg["campaign"]
        ad_groups = "; ".join(sorted(agg["ad_groups"]))
        clicks = agg["clicks"]
        cost = round(agg["cost"], 2)
        conversions = agg["conversions"]

        topic, outcome = common.classify_topic(search_term, topic_keywords, core_topic_names)
        outcome_counts[outcome] += 1

        if outcome == "none":
            unclassified_rows.append({
                "search_term": search_term, "campaign": campaign, "ad_groups": ad_groups,
                "clicks": clicks, "cost": cost, "conversions": conversions,
            })
            continue

        actual_cluster = common.match_cluster(campaign, clusters)

        if outcome == "single":
            if topic == actual_cluster:
                continue  # correctly homed - not interesting
            reason_counts["mismatch"] += 1
            candidates = sorted(cluster_campaign_map.get(topic, []))
            if candidates:
                reason_counts["re_home"] += 1
                action = f"Re-home to existing {topic} campaign: {', '.join(candidates)}"
            else:
                reason_counts["negative"] += 1
                action = f"No existing {topic} campaign — add as negative keyword in {campaign}"
            mismatch_rows.append({
                "search_term": search_term, "campaign": campaign, "ad_groups": ad_groups,
                "actual_cluster": actual_cluster, "derived_topic": topic, "topic_outcome": outcome,
                "clicks": clicks, "cost": cost, "conversions": conversions, "suggested_action": action,
            })

        elif outcome == "ambiguous":
            reason_counts["ambiguous"] += 1
            action = f"Ambiguous topic match ({topic}) — review manually before re-homing or negating"
            mismatch_rows.append({
                "search_term": search_term, "campaign": campaign, "ad_groups": ad_groups,
                "actual_cluster": actual_cluster, "derived_topic": topic, "topic_outcome": outcome,
                "clicks": clicks, "cost": cost, "conversions": conversions, "suggested_action": action,
            })

        elif outcome == "tracked_no_campaign":
            existing = sorted(topic_campaign_map.get(topic, []))
            if existing:
                reason_counts["tracked_campaign_exists"] += 1
                action = (f"Matches tracked list — an uncategorized campaign already exists "
                          f"({', '.join(existing)}) — consider adding a clusters entry")
            else:
                reason_counts["tracked_no_campaign_at_all"] += 1
                action = "Matches tracked list — no existing campaign — evaluate for a dedicated campaign"
            tracked_rows.append({
                "search_term": search_term, "campaign": campaign, "ad_groups": ad_groups,
                "topic": topic, "clicks": clicks, "cost": cost, "conversions": conversions,
                "suggested_action": action,
            })

    mismatch_rows.sort(key=lambda r: r["cost"], reverse=True)
    unclassified_rows.sort(key=lambda r: r["cost"], reverse=True)
    tracked_rows.sort(key=lambda r: (r["topic"], -r["cost"]))

    common.write_csv(mismatch_output, MISMATCH_FIELDS, mismatch_rows)
    common.write_csv(tracked_output, TRACKED_FIELDS, tracked_rows)
    common.write_csv(unclassified_output, UNCLASSIFIED_FIELDS, unclassified_rows)

    # Duplication signal: independent of topic, grouped from the same
    # (search_term, campaign) aggregation.
    by_term = {}
    for agg in aggregated.values():
        by_term.setdefault(agg["search_term"].lower(), []).append(agg)

    duplication_rows = []
    for term_key, group in by_term.items():
        distinct_campaigns = {a["campaign"] for a in group}
        if len(distinct_campaigns) <= 1:
            continue
        total_cost = round(sum(a["cost"] for a in group), 2)
        for a in group:
            duplication_rows.append({
                "search_term": a["search_term"], "total_cost": total_cost,
                "distinct_campaign_count": len(distinct_campaigns),
                "campaign": a["campaign"], "cluster": common.match_cluster(a["campaign"], clusters),
                "clicks": a["clicks"], "cost": round(a["cost"], 2), "conversions": a["conversions"],
            })
    duplication_rows.sort(key=lambda r: (-r["total_cost"], -r["cost"]))
    common.write_csv(duplication_output, DUPLICATION_FIELDS, duplication_rows)

    insufficient_path = None
    if insufficient:
        insufficient_path = str(Path(mismatch_output).with_name(
            Path(mismatch_output).stem + "-insufficient-data.csv"))
        common.write_csv(insufficient_path, list(records[0].keys()), insufficient)

    dup_terms = {r["search_term"].lower() for r in duplication_rows}

    print(f"Topic-alignment analysis for {project_name}")
    print(f"  Input: {args.input} ({len(records)} rows, {len(aggregated)} unique search_term+campaign pairs)")
    print(f"  Topic keywords: {len(topic_keywords)} topics configured "
          f"({len(core_topic_names)} core, {len(topic_keywords) - len(core_topic_names)} tracked)")
    print("  Topic outcome breakdown:")
    print(f"    single (core, comparable): {outcome_counts['single']}")
    print(f"    tracked_no_campaign:       {outcome_counts['tracked_no_campaign']}")
    print(f"    ambiguous (2+ topics):     {outcome_counts['ambiguous']}")
    print(f"    no-topic-signal:           {outcome_counts['none']}")
    print("  Mismatch signal:")
    print(f"    Flagged mismatch rows: {reason_counts['mismatch']} "
          f"(cost: {sum(r['cost'] for r in mismatch_rows if r['topic_outcome'] == 'single'):.2f})")
    print(f"    Ambiguous rows:        {reason_counts['ambiguous']} "
          f"(cost: {sum(r['cost'] for r in mismatch_rows if r['topic_outcome'] == 'ambiguous'):.2f})")
    print(f"    -> re-home suggested:  {reason_counts['re_home']}   "
          f"-> negative-keyword suggested: {reason_counts['negative']}")
    print("  Tracked-country signal:")
    print(f"    Rows: {len(tracked_rows)} (cost: {sum(r['cost'] for r in tracked_rows):.2f})")
    by_topic_cost = {}
    for r in tracked_rows:
        by_topic_cost[r["topic"]] = by_topic_cost.get(r["topic"], 0.0) + r["cost"]
    top5 = sorted(by_topic_cost.items(), key=lambda kv: -kv[1])[:5]
    print(f"    By topic (top 5 by cost): {', '.join(f'{k} {v:.2f}' for k, v in top5)}")
    print(f"    -> campaign already exists (uncategorized): {reason_counts['tracked_campaign_exists']}   "
          f"-> no campaign at all: {reason_counts['tracked_no_campaign_at_all']}")
    print("  Duplication signal:")
    print(f"    Search terms in 2+ campaigns: {len(dup_terms)} "
          f"(rows: {len(duplication_rows)}, total cost: {sum(r['cost'] for r in duplication_rows):.2f})")
    print(f"  Insufficient-data rows: {len(insufficient)}"
          + (f" (see {insufficient_path})" if insufficient_path else ""))
    print("  Outputs:")
    print(f"    Mismatch:      {mismatch_output}")
    print(f"    Tracked:       {tracked_output}")
    print(f"    Duplication:   {duplication_output}")
    print(f"    Unclassified:  {unclassified_output}")


if __name__ == "__main__":
    main()

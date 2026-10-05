#!/usr/bin/env python3
"""Search-term opportunity / pattern discovery.

Checks a trailing-window search-term report (plus the current active
keyword list) to flag search terms worth acting on, across two
independent signal types:

  - new_demand:      the term's text matches exactly one topic in the
                      config's topic_keywords AND no ENABLED campaign
                      currently serves that topic - real demand with
                      nothing live to capture it
  - underexploited:  strong conversion rate / low CAC, not yet an active
                      keyword

A term can carry both signals at once; all are reported together with
a suggested action.

new_demand uses the same content-based engine as analyze_topic_alignment.py
(ads_common.classify_topic / match_topics), not campaign-name matching.
classify_topic() returns one of four labels, and new_demand is exactly
one of them:
  - tracked_no_campaign -> new_demand. Exactly one topic matched, but no
      ENABLED campaign serves it (a tracked country with no cluster, or a
      core topic whose cluster's campaigns are all paused/removed).
  - single              -> not new_demand. A live campaign already serves
      the topic; if the term is running in the wrong campaign, that is
      analyze_topic_alignment.py's re-home signal, not new demand.
  - ambiguous           -> not new_demand. 2+ topics matched (conflicting
      signals), so there's no single uncovered topic to point at.
  - none                -> not new_demand. No topic pattern matched, so the
      script can't say what the term is about, let alone that it's
      uncovered. (The previous catch-all-campaign logic flagged these.)
Classification depends only on the term's text and the live campaign
list, so it is independent of which campaign(s) the term ran in - no
single top-cost campaign decides anything.

--campaigns is therefore required: a live campaign list (name + status)
pulled fresh via MCP, never cached. classify_topic() can't tell "single"
from "tracked_no_campaign" without it, and an empty or stale list silently
turns every core topic into new_demand (see coverage_warning()).

Added/Excluded status (term_status - live-verified 2026-09-21 on the
Search path (search_term_view.status) and confirmed as the same enum on
the PMax path (segments.search_term_targeting_status), see CLAUDE.md's
Known technical notes; a fresh live re-check of both paths together is
still pending) genuinely filters underexploited only - a term already
Added as a keyword (in ANY campaign it appears in) isn't underexploited
by definition, so it's excluded from that signal entirely. new_demand
is never filtered by it.

The output carries this as `in_account` - a plain boolean, True if the
term is Added or Added/Excluded in any campaign it appears in, False
otherwise - rather than a per-row Added/Excluded status string. A
representative status picked from whichever campaign occurrence had the
highest cost would be ambiguous (it could show "excluded" for a term
that's actually Added in a lower-cost campaign, or vice versa) and
wouldn't necessarily match what the underexploited filter itself checks.
in_account mirrors that filter's own "added anywhere" logic exactly, the
same way Google Ads' own UI reports "already in account" as a single
boolean rather than a per-occurrence status.
If the input CSV doesn't have a resolvable term_status column at all,
every term's in_account is False and the underexploited filter is
unaffected - the column is optional input, not a hard requirement.

Usage:
    analyze_search_opportunities.py --config configs/<project>.yaml \\
        --trailing trailing_search_terms.csv \\
        --keywords current_keywords.csv --campaigns campaigns.csv \\
        [--output file.csv] [--min-conversions N] [--max-cac-ratio R]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

TERM_COLUMNS = ["search_term", "campaign", "clicks", "cost", "conversions", "term_status"]
REQUIRED_CAMPAIGN_COLUMNS = ["campaign", "status"]
OUTPUT_FIELDS = [
    "search_term", "campaign", "cluster", "brand_term", "in_account",
    "clicks", "cost", "conversions", "cpa",
    "signals", "suggested_action",
]

SUGGESTED_ACTIONS = {
    "new_demand": "Consider a new cluster/campaign for this term",
    "underexploited": "Add as an exact-match keyword",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--trailing", required=True, help="Trailing-window search-term CSV")
    parser.add_argument("--keywords", required=True, help="Current active keyword-list export CSV")
    parser.add_argument("--campaigns", required=True,
                         help="Live campaign list CSV (campaign name + status), pulled fresh via MCP - "
                              "never cached, since status changes over time")
    parser.add_argument("--output", default=None, help="Output CSV path (default: <project>-opportunities.csv)")
    parser.add_argument("--min-conversions", type=float, default=1.0,
                         help="Minimum trailing conversions to qualify as underexploited (default 1)")
    parser.add_argument("--max-cac-ratio", type=float, default=0.5,
                         help="Flag as underexploited when CPA <= this fraction of the trailing account-average CPA (default 0.5)")
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


def load_campaigns(path):
    records = common.load_table(path)
    if not records:
        sys.exit(f"No data rows found in {path}")
    cols = common.resolve_columns(records[0].keys(), REQUIRED_CAMPAIGN_COLUMNS)
    missing = [c for c in REQUIRED_CAMPAIGN_COLUMNS if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {path} "
                  f"(saw headers: {list(records[0].keys())})")
    return [
        {"name": row.get(cols["campaign"], "").strip(), "status": row.get(cols["status"], "").strip().upper()}
        for row in records
    ]


def classify_new_demand(search_term, topic_keywords, topic_campaigns):
    """Returns (is_new_demand, classification, matched_topics).

    is_new_demand is True exactly when classify_topic() returns
    "tracked_no_campaign" - see the module docstring for why each of the
    four labels does or doesn't count.
    """
    classification, matched_topics = common.classify_topic(search_term, topic_keywords, topic_campaigns)
    return classification == "tracked_no_campaign", classification, matched_topics


def coverage_warning(topic_keywords, topic_campaigns, campaigns):
    """Message if the campaign list looks empty/stale enough that
    classify_topic() would silently mislabel core topics as
    tracked_no_campaign (e.g. a wrong or empty pull), else None."""
    if not any(c["status"] == "ENABLED" for c in campaigns):
        return ("--campaigns contains no ENABLED campaigns - every topic will classify as "
                "tracked_no_campaign (new_demand). Check that this is a fresh, complete pull.")
    core_topics = [t for t in topic_keywords if t.get("cluster")]
    if core_topics and not any(t["name"] in topic_campaigns for t in core_topics):
        return ("no ENABLED campaign in --campaigns maps to any core topic's cluster - every core-topic "
                "term will classify as tracked_no_campaign (new_demand). Check that this is a fresh, "
                "complete pull and that campaign names still match the config's clusters.")
    return None


def new_demand_action(matched_topics):
    base = SUGGESTED_ACTIONS["new_demand"]
    return f"{base} ({matched_topics[0]}: no ENABLED campaign serves this topic)" if matched_topics else base


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

        status = common.normalize_term_status(row.get(cols.get("term_status", ""), "")) if "term_status" in cols else "none"

        key = search_term.lower()
        agg = terms.setdefault(key, {
            "search_term": search_term, "campaign": campaign, "top_cost": -1.0,
            "clicks": 0.0, "cost": 0.0, "conversions": 0.0,
            "added_anywhere": False,
        })
        agg["clicks"] += clicks
        agg["cost"] += cost
        agg["conversions"] += conversions
        # "Added anywhere" (not just in the top-cost campaign) is what
        # actually disqualifies underexploited - a term already an active
        # exact-match keyword in any campaign it runs in isn't
        # underexploited there, regardless of which campaign is top-cost.
        # This is also the output's in_account value directly - no
        # separate "representative status" is tracked, so there's nothing
        # for the two to disagree about.
        if status in ("added", "added_excluded"):
            agg["added_anywhere"] = True
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
    topic_keywords = config.get("topic_keywords", [])
    if not topic_keywords:
        sys.exit(f"No topic_keywords found in {args.config} - new_demand is classified from them")

    output_path = args.output or f"{project_name}-opportunities.csv"

    trailing_records = common.load_table(args.trailing)
    keyword_records = common.load_table(args.keywords)

    campaigns = load_campaigns(args.campaigns)
    topic_campaigns = common.campaigns_by_topic(campaigns, clusters, topic_keywords, active_statuses=("ENABLED",))
    warning = coverage_warning(topic_keywords, topic_campaigns, campaigns)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)

    # Trailing is the window being analyzed, so it must have both columns
    # and data. The active-keyword list may legitimately be empty - treat
    # an empty table there as "no active keywords", not an error.
    trailing_cols = resolve_term_columns(trailing_records, args.trailing)

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

    converting = [t for t in trailing_terms.values() if t["conversions"] > 0]
    if converting:
        avg_cpa = sum(t["cost"] / t["conversions"] for t in converting) / len(converting)
    else:
        avg_cpa = None

    flagged = []
    signal_counts = {name: 0 for name in SUGGESTED_ACTIONS}
    topic_tally = {}

    for key, term in trailing_terms.items():
        search_term = term["search_term"]
        campaign = term["campaign"]
        clicks = term["clicks"]
        cost = term["cost"]
        conversions = term["conversions"]
        cpa = (cost / conversions) if conversions > 0 else None

        # Descriptive tag only: the cluster of the term's single top-cost
        # campaign. It no longer feeds any signal - new_demand classifies by
        # topic (see classify_new_demand), and underexploited never used it.
        cluster = common.match_cluster(campaign, clusters)
        is_brand = common.matches_any_keyword(search_term, brand_keywords)
        added_anywhere = term.get("added_anywhere", False)

        signals = []

        is_new_demand, topic_class, matched_topics = classify_new_demand(search_term, topic_keywords, topic_campaigns)
        topic_tally[topic_class] = topic_tally.get(topic_class, 0) + 1
        if is_new_demand:
            signals.append("new_demand")

        if (conversions >= args.min_conversions and avg_cpa is not None
                and cpa is not None and cpa <= avg_cpa * args.max_cac_ratio
                and search_term.lower() not in active_keywords
                and not added_anywhere):
            signals.append("underexploited")

        if not signals:
            continue

        for s in signals:
            signal_counts[s] += 1

        flagged.append({
            "search_term": search_term,
            "campaign": campaign,
            "cluster": cluster,
            "brand_term": is_brand,
            "in_account": added_anywhere,
            "clicks": clicks,
            "cost": round(cost, 2),
            "conversions": conversions,
            "cpa": round(cpa, 2) if cpa is not None else "",
            "signals": ";".join(signals),
            "suggested_action": "; ".join(
                new_demand_action(matched_topics) if s == "new_demand" else SUGGESTED_ACTIONS[s]
                for s in signals
            ),
        })

    flagged.sort(key=lambda r: r["cost"], reverse=True)
    common.write_csv(output_path, OUTPUT_FIELDS, flagged)

    print(f"Opportunity discovery for {project_name}")
    print(f"  Trailing: {args.trailing} ({len(trailing_terms)} unique terms)")
    print(f"  Active keywords: {args.keywords} ({len(active_keywords)} keywords)")
    enabled_count = sum(1 for c in campaigns if c["status"] == "ENABLED")
    print(f"  Campaign list: {args.campaigns} ({len(campaigns)} campaigns, {enabled_count} ENABLED)")
    print("  Topic classification of unique trailing terms (only tracked_no_campaign is new_demand):")
    for label, count in sorted(topic_tally.items(), key=lambda kv: -kv[1]):
        print(f"    {label}: {count}")
    print(f"  Flagged rows: {len(flagged)}")
    print("  Breakdown by signal type:")
    for name, count in signal_counts.items():
        print(f"    {name}: {count}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()

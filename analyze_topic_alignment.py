#!/usr/bin/env python3
"""Search-term topic alignment: content-based country/destination detection
vs. campaign-based clusters, independent axes that should usually agree.

For each search term, classify_topic() checks the term's *content* against
topic_keywords (configs/<project>.yaml) and compares that to the *cluster*
its current campaign belongs to (clusters[], matched by campaign name):

  - single              content topic has a live active campaign to serve
                         it. If that differs from the term's current
                         cluster, it's a mismatch: flagged with a re-home
                         suggestion pointing at the existing campaign(s).
  - tracked_no_campaign  content topic is a tracked country with no
                         dedicated cluster/campaign - real demand signal.
                         Tiered by whether a dormant (paused/removed)
                         campaign already exists for that country: cheaper
                         to reactivate than to build from scratch.
  - ambiguous            2+ conflicting topics matched - manual review.
  - none                 no topic pattern matched - written to a separate
                          "-topic-unclassified.csv" rather than silently
                          dropped (used to check topic_keywords coverage
                          against real query volume).

A dormant campaign is never itself suggested as a re-home target (only a
live ENABLED campaign is) - it only changes the tracked_no_campaign
suggested action from "new campaign candidate" to "evaluate/reactivate
existing campaign".

Added/Excluded status (term_status - NOT yet live-verified as available
via the Google Ads MCP connector, see CLAUDE.md's Known technical notes)
refines the "single" mismatch suggestion two ways: if the term is already
Excluded in its current (wrong) campaign, the mismatch is noted without
repeating a redundant action - Google is already suppressing it there, so
re-homing isn't urgent. If the term is already Added as a keyword in one
of the re-home target campaigns, that's noted alongside the suggestion
("already present in the correct campaign"). Until the column is
confirmed available, every row's term_status is "none" and this logic is
inert (falls through to the plain re-home suggestion, matching prior
behavior exactly).

Usage:
    analyze_topic_alignment.py --config configs/<project>.yaml
        --input search_terms.csv --campaigns campaigns.csv [--output file.csv]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

REQUIRED_TERM_COLUMNS = ["search_term", "campaign"]
REQUIRED_CAMPAIGN_COLUMNS = ["campaign", "status"]
OUTPUT_FIELDS = [
    "search_term", "campaign", "ad_group", "current_cluster",
    "matched_topics", "classification", "term_status", "suggested_action",
    "clicks", "cost", "conversions",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--input", required=True, help="Search-term CSV (already joined to campaign names)")
    parser.add_argument("--campaigns", required=True,
                         help="Live campaign list CSV (campaign name + status), pulled fresh via MCP - "
                              "never cached, since status changes over time")
    parser.add_argument("--output", default=None, help="Output CSV path (default: <project>-topic-alignment.csv)")
    return parser.parse_args()


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


def build_term_campaign_status(records, cols):
    """(search_term.lower(), campaign) -> normalized term_status, scanning
    every row - not just the one currently being classified - since this
    is used to check whether a term is already Added as a keyword in a
    *different* campaign than the one a given row happens to be in (e.g.
    a mismatch's re-home target). Empty when term_status isn't a resolved
    column at all (not yet live-verified as available - see CLAUDE.md),
    so the lookup is harmlessly inert until then.
    """
    lookup = {}
    if "term_status" not in cols:
        return lookup
    for row in records:
        term = row.get(cols["search_term"], "").strip().lower()
        campaign = row.get(cols["campaign"], "").strip()
        status = common.normalize_term_status(row.get(cols["term_status"], ""))
        if term and campaign and status != "none":
            lookup[(term, campaign)] = status
    return lookup


def suggest_action(classification, matched_topics, current_cluster, topic_campaigns_active, topics_by_name,
                    campaigns, search_term, campaign, own_term_status, term_campaign_status):
    if classification == "ambiguous":
        return f"ambiguous - manual review (matched: {', '.join(matched_topics)})"

    if classification == "single":
        topic_name = matched_topics[0]
        targets = sorted({c["name"] for c in topic_campaigns_active.get(topic_name, [])})
        if current_cluster == topic_name:
            return "correctly homed"

        if own_term_status in ("excluded", "added_excluded"):
            # Google is already suppressing this term here - re-homing
            # would help, but there's no urgent negative-keyword action
            # left to take in the meantime, so don't repeat one.
            return f"already excluded here ({campaign}) - mismatch noted, no negative-keyword action needed"

        action = f"re-home to existing {topic_name} campaign(s): {'; '.join(targets)}"
        already_added = sorted({
            t for t in targets
            if term_campaign_status.get((search_term.lower(), t)) in ("added", "added_excluded")
        })
        if already_added:
            action += f" (already present as a keyword in: {'; '.join(already_added)})"
        return action

    if classification == "tracked_no_campaign":
        topic_name = matched_topics[0]
        topic = topics_by_name[topic_name]
        active_named = [c for c in common.campaigns_matching_topic_name(campaigns, topic) if c["status"] == "ENABLED"]
        if active_named:
            names = sorted({c["name"] for c in active_named})
            return (f"UNEXPECTED: active campaign already exists for tracked topic {topic_name} "
                    f"({'; '.join(names)}) - consider promoting to a core topic/cluster")
        matched = common.campaigns_matching_topic_name(campaigns, topic)
        paused_named = [c for c in matched if c["status"] == "PAUSED"]
        removed_named = [c for c in matched if c["status"] == "REMOVED"]
        if paused_named:
            # PAUSED can actually be re-enabled - REMOVED cannot (Google Ads
            # doesn't allow un-removing a campaign), so only PAUSED gets the
            # "reactivate" framing. A REMOVED-only match is folded into the
            # no-campaign branch below, with its history noted as reference.
            names = sorted({c["name"] for c in paused_named})
            note = ""
            if removed_named:
                removed_names = sorted({c["name"] for c in removed_named})
                note = f"; also previously removed (reference only, can't be reactivated): {'; '.join(removed_names)}"
            return f"evaluate/reactivate paused campaign(s) for {topic_name}: {'; '.join(names)}{note}"
        if removed_named:
            names = sorted({c["name"] for c in removed_named})
            return (f"no active or reactivatable campaign for {topic_name} - new campaign candidate "
                     f"(a removed campaign exists for reference, can't be reactivated: {'; '.join(names)})")
        return f"no campaign ever existed for {topic_name} - new campaign candidate"

    return ""


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    topic_keywords = config.get("topic_keywords", [])
    if not topic_keywords:
        sys.exit(f"No topic_keywords found in {args.config}")
    topics_by_name = {t["name"]: t for t in topic_keywords}

    output_path = args.output or f"{project_name}-topic-alignment.csv"

    campaigns = load_campaigns(args.campaigns)
    topic_campaigns_active = common.campaigns_by_topic(campaigns, clusters, topic_keywords, active_statuses=("ENABLED",))

    records = common.load_table(args.input)
    if not records:
        sys.exit(f"No data rows found in {args.input}")

    cols = common.resolve_columns(records[0].keys(), REQUIRED_TERM_COLUMNS + ["ad_group", "clicks", "cost", "conversions", "term_status"])
    missing = [c for c in REQUIRED_TERM_COLUMNS if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {args.input} "
                  f"(saw headers: {list(records[0].keys())})")

    # Resolved once per numeric column, not per cell - see
    # infer_decimal_style() in ads_common.py.
    decimal_styles = common.resolve_decimal_styles(records, cols, ["clicks", "cost", "conversions"])
    term_campaign_status = build_term_campaign_status(records, cols)

    classified, unclassified = [], []
    tally = {}

    for row in records:
        search_term = row.get(cols["search_term"], "").strip()
        campaign = row.get(cols["campaign"], "").strip()
        if not search_term or not campaign:
            continue  # not this script's job - analyze_wasted_spend.py already reports these

        classification, matched_topics = common.classify_topic(search_term, topic_keywords, topic_campaigns_active)
        tally[classification] = tally.get(classification, 0) + 1

        if classification == "none":
            unclassified.append(row)
            continue

        current_cluster = common.match_cluster(campaign, clusters)
        clicks = common.clean_number(row.get(cols.get("clicks", ""), ""), decimal_styles.get("clicks"))
        cost = common.clean_number(row.get(cols.get("cost", ""), ""), decimal_styles.get("cost"))
        conversions = common.clean_number(row.get(cols.get("conversions", ""), ""), decimal_styles.get("conversions"))
        own_term_status = common.normalize_term_status(row.get(cols.get("term_status", ""), "")) if "term_status" in cols else "none"

        classified.append({
            "search_term": search_term,
            "campaign": campaign,
            "ad_group": row.get(cols.get("ad_group", ""), ""),
            "current_cluster": current_cluster,
            "matched_topics": "; ".join(matched_topics),
            "classification": classification,
            "term_status": own_term_status,
            "suggested_action": suggest_action(
                classification, matched_topics, current_cluster,
                topic_campaigns_active, topics_by_name, campaigns,
                search_term, campaign, own_term_status, term_campaign_status,
            ),
            "clicks": clicks,
            "cost": round(cost, 2),
            "conversions": conversions,
        })

    classified.sort(key=lambda r: r["cost"], reverse=True)
    common.write_csv(output_path, OUTPUT_FIELDS, classified)

    unclassified_path = None
    if unclassified:
        unclassified_path = str(Path(output_path).with_name(
            Path(output_path).stem + "-topic-unclassified.csv"))
        common.write_csv(unclassified_path, list(records[0].keys()), unclassified)

    mismatches = [r for r in classified if r["classification"] == "single" and r["suggested_action"] != "correctly homed"]

    print(f"Topic alignment analysis for {project_name}")
    print(f"  Search-term input: {args.input}")
    print(f"  Campaign list: {args.campaigns} ({len(campaigns)} campaigns)")
    print(f"  Classified rows: {len(classified)}")
    for classification, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"    {classification}: {count}")
    print(f"  Mismatches (single topic, wrong campaign): {len(mismatches)}")
    print(f"  Unclassified ('none') rows: {len(unclassified)}"
          + (f" (see {unclassified_path})" if unclassified_path else ""))
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()

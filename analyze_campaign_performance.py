#!/usr/bin/env python3
"""Campaign performance report (v2: structured findings, Ads-only - GA4
revenue merge is a later version, not built toward here).

ARCHITECTURE (v2 change from v1): this script no longer renders a markdown
narrative itself. It emits one structured JSON file (three top-level
sections - what_was_done / performance / next_steps - matching the report
structure below) plus CSV backups of the cohort/campaign rows. Composing
the actual narrative prose - explaining what a correlation likely means,
phrasing the "next steps" conclusions - happens in the Claude Code session
that invokes this script, reading the JSON, the same way script-output
summaries are already written elsewhere in this toolkit. This keeps every
number reproducible/testable in code while keeping judgment-language out
of the script. v1's render_report()/markdown output has been removed, not
just deprecated.

Report structure (the JSON mirrors this):
  1. what_was_done      - factual account change list for the period: new/
                           paused campaigns, ad group/asset group changes,
                           ad copy edits, budget changes, bid strategy/
                           target changes, geo/keyword/audience changes.
                           No analysis, just facts. STATUS: gated - see
                           "Change-log gating" below. Always emits
                           {"status": "not_available", "changes": []}
                           until that gate clears, unless --changes is
                           passed (see load_change_events()).
  2. performance         - account-level before/after, then the same
                           breakdown per cluster (cohort), then per-campaign
                           within each cluster - CAC x volume quadrant
                           classification (Star/Efficient/Review/
                           Underperformer, relative to a benchmark),
                           conversion rate and Lost IS as diagnostics, and a
                           correlation_flags list per cluster/campaign
                           cross-referencing what_was_done's changes against
                           that entity's performance shift (see
                           build_correlation_flags() - also gated, see
                           below).
  3. next_steps          - scale/cut/edit candidates and top-by-metric
                           rankings, derived from the same quadrant data,
                           at both the cluster and campaign level.

Change-log gating (CLAUDE.md's Known technical notes / Planned section):
  The correlation logic in build_correlation_flags() is NOT implemented -
  it always returns [] - because the change_event resource's schema
  (retention limit, granularity, available fields) has not been confirmed
  via a live metadata_get_resource_metadata call (Google Ads MCP Connector
  was unavailable every time this was attempted). This is a harder gate
  than the rest of this script's "not yet live-verified" status: those
  other parts (quadrant math, deltas, CSV/JSON shape) are built on
  reasonable, testable assumptions and just need a live data run to
  confirm; the correlation matching logic literally cannot be written
  correctly without knowing change_event's fields first, so it's left as
  a stub with the call site wired in, not a best-guess implementation.
  what_was_done is populated only if the caller supplies --changes (a CSV
  in whatever shape the eventual MCP pull produces); columns are read
  generically (date/campaign/change_type/description, case-insensitive)
  since the real schema isn't confirmed - update load_change_events() once
  it is.

Quadrants (never a single composite score - see causal_note() below for a
qualitative explanation layer that sits *alongside* this, not inside it):
  Star                              - efficient (CAC <= target) AND high
                                       volume (conversions >= target)
  Efficient (underexploited)        - efficient, but below target volume
  Review (high spend, high volume)  - high volume, but not efficient
  Underperformer                    - neither efficient nor high volume

Quadrant floor (thresholds.min_conversions_per_30_days in config) is
proportional to the selected period, not flat: 10 Parcels conversions over
90 days is a real low-rate signal, not "just needs more data" the way 10
over 2 weeks would be. floor = max(MIN_QUADRANT_FLOOR_ABSOLUTE, round(rate
* period_days / 30)). A cohort/campaign under the floor is "insufficient
data" - never dropped, never force-classified. Below the floor, a campaign
is further split by campaign_age_status() into new (started within
thresholds.new_campaign_window_days of the period's end - low volume here
is upside potential, not a problem) vs. stagnant (older, still below floor
- worth a second look) vs. unknown (no usable start_date in the input).
Clusters in config's exclude_from_scoring (brand) are never classified or
age-split, though they still appear in raw CSV/JSON output. The account
rollup (performance.account) is a true account total and always includes
excluded clusters, unlike the scored cohort/campaign peer groups.

Lost Impression Share (rank/budget) and conversion rate (vs. the account
average) never enter the quadrant formula itself - they only feed
causal_note(), a short qualitative annotation attached *alongside* the
quadrant label explaining the likely "why" (e.g. "Underperformer - conv.
rate 1.2% (below average) -> likely a traffic quality/offer issue, not
budget", or "Star - Lost IS (rank): 45% -> still room to grow if bids/
quality improve"). Still exactly 4 quadrants - this is prose, not a fifth
or sixth classification axis.

tcpa_recommendation() is a separate, per-campaign-only suggestion (cohorts
aren't bid strategies) on Target CPA candidacy, built from the same CAC/
volume data: thresholds.tcpa_min_conversions_per_30_days (default 30,
Google's general guidance for stable tCPA, scaled to the period like the
quadrant floor) as the volume bar, plus a period-over-comparison CAC-swing
check (thresholds.tcpa_cac_stability_pct) as a stability proxy. Both
thresholds are explicitly adjustable assumptions, not hard rules - actual
stability requirements vary by account.

Benchmark modes (--benchmark):
  cluster  Peer-average within the immediate group: at the cohort layer
           that's all other scored cohorts (so identical to `account` at
           this layer - the two modes only diverge at the campaign layer,
           where `cluster` benchmarks a campaign against its own cohort
           instead of the whole account).
  account  Whole-account scored peer average, current period.
  target   Trailing historical average from a separate --target pull (a
           window that must NOT overlap --comparison, or "target" and
           "comparison" collapse into near-duplicate benchmarks - enforced
           via --target-start/--target-end, not just a documented caution).

All three benchmarks are weighted CAC (total cost / total conversions across
the peer group) and mean volume (simple average of each member's
conversions) - never a naive mean-of-CACs, which a single tiny-volume
campaign could swing wildly.

Numeric parsing note: raw API-precision values (e.g. Parcels conversions
as 22.282975) are safe to write as-is - aggregate_campaign_metrics() infers
each column's decimal-separator convention from the whole column (see
ads_common.infer_decimal_style()), not by guessing at each cell in
isolation, so an ambiguous single cell like "22.283" is resolved by any
unambiguous sibling elsewhere in the same column. Only round to 2 decimals
as a fallback if a column could plausibly have zero unambiguous values
anywhere in it (rare).

NOT YET LIVE-VERIFIED: this v2 restructure (account rollup, conversion-rate
deltas, what_was_done/next_steps shape, correlation-flag wiring) has not
been run against a real MCP pull - it joins the existing backlog of
regex/logic-verified-only items (term_status, the ported topic_keywords
patterns). Run it against a real period/comparison pull and sanity-check
the JSON before treating any of it as confirmed.

Usage:
    analyze_campaign_performance.py --config configs/<project>.yaml \\
        --period period_metrics.csv --period-start 2026-08-17 --period-end 2026-09-15 \\
        --comparison comparison_metrics.csv --comparison-start 2026-07-18 --comparison-end 2026-08-16 \\
        [--benchmark cluster|account|target] \\
        [--target target_metrics.csv --target-start ... --target-end ...] \\
        [--changes change_events.csv] \\
        [--output file.json]
"""
import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import ads_common as common

REQUIRED_METRIC_COLUMNS = ["campaign", "cost", "conversions"]
OPTIONAL_METRIC_COLUMNS = [
    "channel_type", "impressions", "clicks", "conversions_value",
    "search_impression_share", "lost_is_rank", "lost_is_budget", "start_date",
]
COHORT_CSV_FIELDS = [
    "cluster", "status", "quadrant", "causal_note", "lost_is_diagnostic", "suggested_action",
    "cost", "cost_change_pct", "conversions", "conversions_change_pct",
    "cac", "cac_change_pct", "conversion_rate", "conversion_rate_change_pct",
    "conversions_value", "campaign_count",
]
CAMPAIGN_CSV_FIELDS = [
    "campaign", "cluster", "channel_type", "status", "quadrant", "causal_note",
    "lost_is_diagnostic", "suggested_action", "age_status", "age_days", "tcpa_recommendation",
    "cost", "cost_change_pct", "conversions", "conversions_change_pct",
    "cac", "cac_change_pct", "conversion_rate", "conversion_rate_change_pct", "conversions_value",
]

# Hard floor under the proportional quadrant-eligibility bar (thresholds.
# min_conversions_per_30_days * period_days / 30) - protects a very short
# custom period from letting 1 conversion count as "sufficient data".
MIN_QUADRANT_FLOOR_ABSOLUTE = 3

NEXT_STEPS_TOP_N = 10


def _parse_date(parser, flag, value):
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        parser.error(f"{flag} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a <= end_b and start_b <= end_a


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--period", required=True, help="Campaign-metrics CSV for the analysis period")
    parser.add_argument("--period-start", required=True, help="Analysis period start date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--period-end", required=True, help="Analysis period end date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--comparison", required=True, help="Campaign-metrics CSV for the comparison period")
    parser.add_argument("--comparison-start", required=True, help="Comparison period start date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--comparison-end", required=True, help="Comparison period end date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--benchmark", choices=["cluster", "account", "target"], default="cluster")
    parser.add_argument("--target", default=None, help="Campaign-metrics CSV for the trailing benchmark window (required with --benchmark target)")
    parser.add_argument("--target-start", default=None, help="Trailing benchmark window start date, YYYY-MM-DD (required with --benchmark target)")
    parser.add_argument("--target-end", default=None, help="Trailing benchmark window end date, YYYY-MM-DD (required with --benchmark target)")
    parser.add_argument("--changes", default=None,
                         help="Change-log CSV for the period (date/campaign/change_type/description columns, "
                              "case-insensitive) - optional, populates what_was_done and enables correlation "
                              "flags once change_event's real schema is confirmed (see module docstring's "
                              "'Change-log gating'). Omit to leave what_was_done as not_available.")
    parser.add_argument("--output", default=None, help="Structured-findings JSON path (default: <project>-campaign-performance.json)")
    args = parser.parse_args()

    args.period_start = _parse_date(parser, "--period-start", args.period_start)
    args.period_end = _parse_date(parser, "--period-end", args.period_end)
    args.comparison_start = _parse_date(parser, "--comparison-start", args.comparison_start)
    args.comparison_end = _parse_date(parser, "--comparison-end", args.comparison_end)
    args.target_start = _parse_date(parser, "--target-start", args.target_start)
    args.target_end = _parse_date(parser, "--target-end", args.target_end)

    if args.period_end < args.period_start:
        parser.error("--period-end must not be before --period-start")
    if args.comparison_end < args.comparison_start:
        parser.error("--comparison-end must not be before --comparison-start")

    if args.benchmark == "target" and (not args.target or not args.target_start or not args.target_end):
        parser.error("--benchmark target requires --target, --target-start, and --target-end")
    if args.target_start and args.target_end:
        if args.target_end < args.target_start:
            parser.error("--target-end must not be before --target-start")
        # A target window that overlaps the comparison window makes the two
        # benchmarks near-duplicates (see module docstring) - this used to
        # be a documented caution only; now that both are real dates, it's
        # an enforced check instead of something the caller has to remember.
        if _ranges_overlap(args.target_start, args.target_end, args.comparison_start, args.comparison_end):
            parser.error(
                f"--target window ({args.target_start} to {args.target_end}) overlaps --comparison "
                f"({args.comparison_start} to {args.comparison_end}) - pick a trailing window that "
                f"doesn't, or 'target' and 'comparison' collapse into near-duplicate benchmarks"
            )

    args.period_label = f"{args.period_start} to {args.period_end}"
    args.comparison_label = f"{args.comparison_start} to {args.comparison_end}"
    args.target_label = f"{args.target_start} to {args.target_end}" if args.target_start else None
    args.period_days = (args.period_end - args.period_start).days + 1
    return args


def load_metrics_csv(path, label):
    records = common.load_table(path)
    if not records:
        sys.exit(f"No data rows found in {label} ({path})")
    cols = common.resolve_columns(records[0].keys(), REQUIRED_METRIC_COLUMNS + OPTIONAL_METRIC_COLUMNS)
    missing = [c for c in REQUIRED_METRIC_COLUMNS if c not in cols]
    if missing:
        sys.exit(f"Could not find required column(s) {missing} in {label} ({path}) "
                  f"(saw headers: {list(records[0].keys())})")
    return common.aggregate_campaign_metrics(records, cols)


def load_change_events(path):
    """Pass-through loader for a change-log CSV in an as-yet-unconfirmed
    shape (see module docstring's "Change-log gating"). Reads a plain
    header row directly (csv.DictReader) rather than common.load_table()'s
    Google-Ads-UI-export header detection: that heuristic requires 2+ column
    labels matching HEADER_HINTS (built for search-term/campaign exports),
    which a change-log pull's own columns (date/change-type/description)
    won't hit, and this toolkit's own MCP-pull CSVs are hand-built without
    a UI export's preamble title row anyway - so there's nothing here for
    that detection to earn its keep on. Column matching is deliberately
    loose (case-insensitive, tolerates "change_type" or "change type") since
    the real change_event field names aren't confirmed yet - update this
    once metadata_get_resource_metadata confirms them.

    Returns None (not []) when --changes wasn't passed, so callers can tell
    "no changes happened" apart from "we didn't look".
    """
    if not path:
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    lower_map = {k.strip().lower(): k for k in rows[0].keys() if k}

    def col(*candidates):
        for candidate in candidates:
            if candidate in lower_map:
                return lower_map[candidate]
        return None

    date_col = col("date")
    campaign_col = col("campaign", "campaign name")
    type_col = col("change_type", "change type", "resource", "resource type")
    desc_col = col("description", "change", "change description")
    return [
        {
            "date": (row.get(date_col) or "").strip() if date_col else "",
            "campaign": (row.get(campaign_col) or "").strip() if campaign_col else "",
            "change_type": (row.get(type_col) or "").strip() if type_col else "",
            "description": (row.get(desc_col) or "").strip() if desc_col else "",
        }
        for row in rows
    ]


def build_what_was_done(change_events):
    if change_events is None:
        return {
            "status": "not_available",
            "reason": (
                "Change-log resource investigation (change_event: retention limit, granularity, "
                "available fields) has not been completed - the Google Ads MCP Connector was "
                "unavailable every time this was attempted. Pass --changes once a pull is available, "
                "or wire the fetch into /performance-report once the schema is confirmed - see "
                "CLAUDE.md's Known technical notes / Planned section."
            ),
            "changes": [],
        }
    return {"status": "available", "reason": None, "changes": change_events}


def build_correlation_flags(entity_name, changes):
    """Cross-reference one cluster/campaign's performance shift against
    account changes logged during the period. Intentionally a stub - see
    module docstring's "Change-log gating": the matching logic (which
    changes plausibly affected which cluster/campaign, over what lag) can't
    be written correctly without change_event's confirmed field names and
    granularity, so this always returns [] rather than guessing. The call
    site is wired into both the cohort and campaign loops in main() so
    real logic can be dropped in here once the schema is confirmed, without
    touching the rest of the pipeline.
    """
    if not changes:
        return []
    return []


def build_cohorts(campaigns_by_name, clusters, exclude_from_scoring):
    cohorts = {}
    for name, m in campaigns_by_name.items():
        cluster = common.match_cluster(name, clusters)
        c = cohorts.setdefault(cluster, {
            "cluster": cluster, "excluded": cluster in exclude_from_scoring,
            "campaigns": [], "cost": 0.0, "conversions": 0.0,
            "conversions_value": 0.0, "impressions": 0.0, "clicks": 0.0,
        })
        c["campaigns"].append(m)
        c["cost"] += m["cost"]
        c["conversions"] += m["conversions"]
        c["conversions_value"] += m["conversions_value"]
        c["impressions"] += m["impressions"]
        c["clicks"] += m["clicks"]
    return cohorts


def peer_benchmark(members):
    """Weighted-CAC / mean-volume benchmark across a peer group - a list of
    cohort dicts or campaign-metric dicts, either works (both carry "cost"
    and "conversions"). Caller has already excluded exclude_from_scoring
    members and decided what the peer group *is* (that's what distinguishes
    cluster/account/target mode, and cohort-layer from campaign-layer)."""
    members = list(members)
    if not members:
        return None, None
    total_cost = sum(m["cost"] for m in members)
    total_conversions = sum(m["conversions"] for m in members)
    target_cac = (total_cost / total_conversions) if total_conversions > 0 else None
    target_volume = total_conversions / len(members)
    return target_cac, target_volume


def pct_change(current, previous):
    if previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def cohort_weighted_lost_is(cohort):
    """Cost-weighted average Lost IS across a cohort's campaigns, as a
    rollup for the cohort-level diagnostic tag - a straight mean would let
    a tiny campaign's noisy Lost IS swing the cohort's number as much as
    its biggest spender."""
    members = [m for m in cohort["campaigns"]
               if m.get("cost", 0) > 0 and m.get("lost_is_rank") is not None and m.get("lost_is_budget") is not None]
    total_cost = sum(m["cost"] for m in members)
    if not members or total_cost <= 0:
        return None, None
    avg_rank = sum(m["cost"] * m["lost_is_rank"] for m in members) / total_cost
    avg_budget = sum(m["cost"] * m["lost_is_budget"] for m in members) / total_cost
    return avg_rank, avg_budget


def conversion_rate(conversions, clicks):
    if clicks and clicks > 0:
        return conversions / clicks
    return None


def parse_iso_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def campaign_age_status(start_date_raw, period_end, new_campaign_window_days):
    """('new'/'stagnant'/'unknown', age_days_or_None).

    Age is measured to the analysis period's *end* date, not "today" - so
    re-running the same report later against the same period always gives
    the same answer, instead of a campaign quietly aging out of "new"
    between two runs over the same historical window.
    """
    start = parse_iso_date(start_date_raw)
    if start is None:
        return "unknown", None
    age_days = (period_end - start).days
    if age_days < 0:
        return "unknown", None  # start date after period end - bad data, don't guess
    return ("new" if age_days <= new_campaign_window_days else "stagnant"), age_days


CONV_RATE_BAND = 0.20  # relative deviation from account average treated as "below"/"above" rather than "near"


def causal_note(quadrant, conv_rate, account_avg_conv_rate, lost_is_rank, lost_is_budget):
    """Qualitative annotation alongside the quadrant label - explains *why*
    using conversion rate (vs. the account average) and Lost IS, without
    adding a new axis to the classification itself: still exactly the same
    4 quadrants, just with a short explanation of what's likely driving
    this one attached alongside the label.
    """
    if quadrant is None:
        return None
    threshold = common.LOST_IS_FLAG_THRESHOLD
    rel = (conv_rate / account_avg_conv_rate) if (conv_rate is not None and account_avg_conv_rate) else None
    cr_pct = f"{conv_rate * 100:.1f}%" if conv_rate is not None else None

    if quadrant in ("Underperformer", "Review (high spend, high volume)"):
        if rel is None:
            return None  # no clicks data to compute a conversion rate - nothing to say
        if rel < 1 - CONV_RATE_BAND:
            return f"conv. rate {cr_pct} (below average) → likely a traffic quality/offer issue, not budget"
        if rel > 1 + CONV_RATE_BAND:
            if lost_is_budget is not None and lost_is_budget >= threshold:
                return (f"conv. rate {cr_pct} (above average), Lost IS (budget): {lost_is_budget * 100:.0f}% "
                        f"→ traffic is efficient but capped by budget; increase budget rather than cutting")
            return f"conv. rate {cr_pct} (above average), but still inefficient on CAC → check offer/pricing/CPCs, not traffic quality"
        return f"conv. rate {cr_pct} (near account average) → not a conversion-rate problem; check CPCs/bids"

    # Star / Efficient (underexploited): economics are already good - explain headroom, if any.
    if lost_is_rank is not None and lost_is_rank >= threshold:
        return f"Lost IS (rank): {lost_is_rank * 100:.0f}% → still room to grow if bids/quality improve"
    if lost_is_budget is not None and lost_is_budget >= threshold:
        return f"Lost IS (budget): {lost_is_budget * 100:.0f}% → still room to grow with more budget"
    if rel is not None and rel > 1 + CONV_RATE_BAND:
        return f"conv. rate {cr_pct} (above average) → strong traffic quality backing the efficiency"
    return None


def tcpa_recommendation(conversions, cac, cac_change_pct, period_days, min_conversions_per_30_days, stability_pct):
    """tCPA candidacy - an adjustable-assumption guideline, not a hard
    rule: volume threshold defaults to Google's general ~30-conversions/
    30-days guidance for stable Target CPA performance, scaled to the
    selected period the same way the quadrant floor is. CAC "stability" is
    this toolkit's own proxy (period-over-comparison CAC swing), since a
    two-period comparison can't measure true variance - just a swing big
    enough to be a caution flag.
    """
    if cac is None:
        return None  # no conversions at all - nothing to recommend
    required = max(1, round(min_conversions_per_30_days * period_days / 30))
    if conversions < required:
        return (f"Stay on manual/Enhanced CPC - insufficient volume for stable tCPA "
                 f"({conversions:.0f}/{required} conversions needed at ~{min_conversions_per_30_days}/30 days)")
    if cac_change_pct is not None and abs(cac_change_pct) > stability_pct:
        return (f"Hold off on tCPA - volume is sufficient but CAC swung {cac_change_pct:+.0f}% vs. "
                f"comparison (unstable); revisit once CAC settles")
    return f"Good tCPA candidate - sufficient, stable volume ({conversions:.0f} conversions); consider tCPA around {cac:.2f}"


def campaigns_by_cluster_list(campaigns_by_name, clusters, exclude_from_scoring):
    """Period campaigns grouped into lists per cluster, dropping clusters in
    exclude_from_scoring entirely (never a peer-group member, never scored)."""
    grouped = {}
    for name, m in campaigns_by_name.items():
        cluster = common.match_cluster(name, clusters)
        if cluster in exclude_from_scoring:
            continue
        grouped.setdefault(cluster, []).append(m)
    return grouped


def classify_campaigns(period_campaigns, comparison_campaigns, clusters, exclude_from_scoring,
                        min_conversions, benchmark_mode, cluster_targets, flat_target,
                        account_avg_conv_rate, period_end, new_campaign_window_days,
                        period_days, tcpa_min_conversions_per_30_days, tcpa_stability_pct,
                        change_events):
    rows = []
    for name, m in period_campaigns.items():
        cluster = common.match_cluster(name, clusters)
        excluded = cluster in exclude_from_scoring
        comparison = comparison_campaigns.get(name)
        cac = common.compute_cac(m["cost"], m["conversions"])
        comparison_cac = common.compute_cac(comparison["cost"], comparison["conversions"]) if comparison else None

        if excluded:
            status, quadrant, action = "excluded", None, None
        elif m["conversions"] < min_conversions:
            status, quadrant, action = "insufficient_data", None, None
        else:
            if benchmark_mode == "cluster":
                target_cac, target_volume = cluster_targets.get(cluster, (None, None))
            else:
                target_cac, target_volume = flat_target
            quadrant = common.classify_quadrant(cac, m["conversions"], target_cac, target_volume)
            status = "scored"
            action = common.QUADRANT_ACTIONS.get(quadrant)

        lost_is = common.lost_is_diagnostic(m.get("lost_is_rank"), m.get("lost_is_budget"))
        cost_delta = pct_change(m["cost"], comparison["cost"]) if comparison else None
        conv_delta = pct_change(m["conversions"], comparison["conversions"]) if comparison else None
        cac_delta = pct_change(cac, comparison_cac) if (cac is not None and comparison_cac is not None) else None

        conv_rate = conversion_rate(m["conversions"], m["clicks"])
        comparison_conv_rate = conversion_rate(comparison["conversions"], comparison["clicks"]) if comparison else None
        conv_rate_delta = pct_change(conv_rate, comparison_conv_rate) if (conv_rate is not None and comparison_conv_rate is not None) else None
        note = causal_note(quadrant, conv_rate, account_avg_conv_rate,
                            m.get("lost_is_rank"), m.get("lost_is_budget")) if status == "scored" else None

        age, age_days = campaign_age_status(m.get("start_date", ""), period_end, new_campaign_window_days)

        tcpa = None
        if not excluded:
            tcpa = tcpa_recommendation(m["conversions"], cac, cac_delta, period_days,
                                        tcpa_min_conversions_per_30_days, tcpa_stability_pct)

        rows.append({
            "campaign": name,
            "cluster": cluster,
            "channel_type": m.get("channel_type", ""),
            "status": status,
            "quadrant": quadrant or "",
            "causal_note": note or "",
            "lost_is_diagnostic": lost_is or "",
            "suggested_action": action or "",
            "age_status": age,
            "age_days": age_days if age_days is not None else "",
            "tcpa_recommendation": tcpa or "",
            "cost": round(m["cost"], 2),
            "cost_change_pct": round(cost_delta, 1) if cost_delta is not None else "",
            "conversions": round(m["conversions"], 2),
            "conversions_change_pct": round(conv_delta, 1) if conv_delta is not None else "",
            "cac": round(cac, 2) if cac is not None else "",
            "cac_change_pct": round(cac_delta, 1) if cac_delta is not None else "",
            "conversion_rate": round(conv_rate * 100, 2) if conv_rate is not None else "",
            "conversion_rate_change_pct": round(conv_rate_delta, 1) if conv_rate_delta is not None else "",
            "conversions_value": round(m["conversions_value"], 2),
            "correlation_flags": build_correlation_flags(name, change_events),
        })
    return rows


def build_account_snapshot(campaigns_by_name):
    """True account total - unlike the scored cohort/campaign peer groups,
    this always includes exclude_from_scoring clusters (e.g. brand), since
    "account-level before/after" means the whole account, not just the
    scored slice of it."""
    cost = sum(m["cost"] for m in campaigns_by_name.values())
    conversions = sum(m["conversions"] for m in campaigns_by_name.values())
    conversions_value = sum(m["conversions_value"] for m in campaigns_by_name.values())
    clicks = sum(m["clicks"] for m in campaigns_by_name.values())
    impressions = sum(m["impressions"] for m in campaigns_by_name.values())
    cac = common.compute_cac(cost, conversions)
    cr = conversion_rate(conversions, clicks)
    return {
        "cost": round(cost, 2), "conversions": round(conversions, 2),
        "conversions_value": round(conversions_value, 2),
        "clicks": round(clicks, 2), "impressions": round(impressions, 2),
        "cac": round(cac, 2) if cac is not None else None,
        "conversion_rate": round(cr * 100, 2) if cr is not None else None,
    }


ACCOUNT_DELTA_FIELDS = ["cost", "conversions", "conversions_value", "clicks", "impressions", "cac", "conversion_rate"]


def build_account_deltas(period_snap, comparison_snap):
    deltas = {}
    for field in ACCOUNT_DELTA_FIELDS:
        p, c = period_snap.get(field), comparison_snap.get(field)
        deltas[field + "_change_pct"] = round(pct_change(p, c), 1) if (p is not None and c is not None) else None
    return deltas


def _rank_row(r, kind):
    row = {
        "kind": kind, "cluster": r["cluster"], "quadrant": r["quadrant"],
        "cost": r["cost"], "conversions": r["conversions"], "cac": r["cac"],
        "lost_is_diagnostic": r["lost_is_diagnostic"], "suggested_action": r["suggested_action"],
    }
    if kind == "campaign":
        row["campaign"] = r["campaign"]
        row["tcpa_recommendation"] = r["tcpa_recommendation"]
    return row


def build_next_steps(cohort_rows, campaign_rows):
    """Scale/cut/edit candidates and top-by-metric rankings at both the
    cluster and campaign level, derived from the same quadrant data as the
    performance section - no new computation, just re-sorting/re-grouping
    what's already been classified."""
    scored_clusters = [r for r in cohort_rows if r["status"] == "scored"]
    scored_campaigns = [r for r in campaign_rows if r["status"] == "scored"]

    def top(rows, kind, quadrants, n=NEXT_STEPS_TOP_N):
        matches = sorted((r for r in rows if r["quadrant"] in quadrants), key=lambda r: -r["cost"])
        return [_rank_row(r, kind) for r in matches[:n]]

    scale_quadrants = ("Star", "Efficient (underexploited)")
    return {
        "scale_candidates": {
            "clusters": top(scored_clusters, "cluster", scale_quadrants),
            "campaigns": top(scored_campaigns, "campaign", scale_quadrants),
        },
        "cut_candidates": {
            "clusters": top(scored_clusters, "cluster", ("Underperformer",)),
            "campaigns": top(scored_campaigns, "campaign", ("Underperformer",)),
        },
        "edit_candidates": {
            "clusters": top(scored_clusters, "cluster", ("Review (high spend, high volume)",)),
            "campaigns": top(scored_campaigns, "campaign", ("Review (high spend, high volume)",)),
        },
        "top_by_conversions": {
            "clusters": [_rank_row(r, "cluster") for r in sorted(scored_clusters, key=lambda r: -r["conversions"])[:5]],
            "campaigns": [_rank_row(r, "campaign") for r in sorted(scored_campaigns, key=lambda r: -r["conversions"])[:5]],
        },
        "unused_potential_headroom": [
            _rank_row(r, "campaign") for r in scored_campaigns
            if r["quadrant"] == "Efficient (underexploited)" and r["lost_is_diagnostic"]
        ],
    }


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    primary_conversion = config.get("primary_conversion_name", "conversions")
    exclude_from_scoring = set(config.get("exclude_from_scoring", []))

    thresholds = config.get("thresholds", {})
    min_conversions_per_30_days = thresholds.get("min_conversions_per_30_days", 10)
    new_campaign_window_days = thresholds.get("new_campaign_window_days", 30)
    tcpa_min_conversions_per_30_days = thresholds.get("tcpa_min_conversions_per_30_days", 30)
    tcpa_stability_pct = thresholds.get("tcpa_cac_stability_pct", 30)
    # Proportional, not flat: 10 conversions over 90 days is a real low-rate
    # signal, not "just needs more time" the way 10 over 2 weeks would be -
    # see the config comment next to min_conversions_per_30_days.
    min_conversions = max(MIN_QUADRANT_FLOOR_ABSOLUTE, round(min_conversions_per_30_days * args.period_days / 30))

    output_path = args.output or f"{project_name}-campaign-performance.json"
    cohort_csv_path = str(Path(output_path).with_suffix("")) + "-cohorts.csv"
    campaign_csv_path = str(Path(output_path).with_suffix("")) + "-campaigns.csv"

    period_campaigns = load_metrics_csv(args.period, "period")
    comparison_campaigns = load_metrics_csv(args.comparison, "comparison")
    change_events = load_change_events(args.changes)
    what_was_done = build_what_was_done(change_events)

    period_cohorts = build_cohorts(period_campaigns, clusters, exclude_from_scoring)
    comparison_cohorts = build_cohorts(comparison_campaigns, clusters, exclude_from_scoring)

    scored_period_cohorts = {n: c for n, c in period_cohorts.items() if not c["excluded"]}
    non_excluded_period_campaigns = [m for m in period_campaigns.values()
                                      if common.match_cluster(m["campaign"], clusters) not in exclude_from_scoring]

    # Account-wide reference for the causal-note layer - always the current
    # period's own account average, regardless of --benchmark mode (that
    # flag only controls the CAC/volume quadrant target, a separate thing).
    total_conv = sum(m["conversions"] for m in non_excluded_period_campaigns)
    total_clicks = sum(m["clicks"] for m in non_excluded_period_campaigns)
    account_avg_conv_rate = conversion_rate(total_conv, total_clicks)

    if args.benchmark == "target":
        target_campaigns = load_metrics_csv(args.target, "target")
        target_cohorts = build_cohorts(target_campaigns, clusters, exclude_from_scoring)
        scored_target_cohorts = {n: c for n, c in target_cohorts.items() if not c["excluded"]}
        non_excluded_target_campaigns = [m for m in target_campaigns.values()
                                          if common.match_cluster(m["campaign"], clusters) not in exclude_from_scoring]
        cohort_target = peer_benchmark(scored_target_cohorts.values())
        flat_campaign_target = peer_benchmark(non_excluded_target_campaigns)
        cluster_campaign_targets = None  # target mode uses one flat benchmark, no per-cluster variant
        benchmark_note = f"trailing historical average from {args.target_label} (excludes the comparison window)"
    else:
        cohort_target = peer_benchmark(scored_period_cohorts.values())
        flat_campaign_target = peer_benchmark(non_excluded_period_campaigns)
        if args.benchmark == "cluster":
            # A cohort with only one campaign has no in-cohort peers to
            # compare against - self-only peer_benchmark() would make that
            # campaign's CAC/volume trivially equal its own "target",
            # always classifying it Star regardless of actual performance.
            # Fall back to the account-wide benchmark for single-campaign
            # cohorts instead, so the classification stays meaningful.
            cluster_campaign_targets = {
                cluster: (peer_benchmark(members) if len(members) >= 2 else flat_campaign_target)
                for cluster, members in campaigns_by_cluster_list(period_campaigns, clusters, exclude_from_scoring).items()
            }
            benchmark_note = ("cluster and account modes are identical at the cohort layer (the peer group is the "
                               "whole scored account either way) - they diverge at the campaign layer, where cluster "
                               "mode benchmarks each campaign against its own cohort instead of the whole account "
                               "(falling back to the account-wide benchmark for single-campaign cohorts, which have "
                               "no in-cohort peers to compare against)")
        else:
            cluster_campaign_targets = None
            benchmark_note = "whole-account scored peer average, current period"

    target_cac, target_volume = cohort_target

    cohort_rows = []
    for name, cohort in sorted(period_cohorts.items(), key=lambda kv: -kv[1]["cost"]):
        comparison = comparison_cohorts.get(name)
        cac = common.compute_cac(cohort["cost"], cohort["conversions"])
        comparison_cac = common.compute_cac(comparison["cost"], comparison["conversions"]) if comparison else None

        if cohort["excluded"]:
            status, quadrant, action = "excluded", None, None
        elif cohort["conversions"] < min_conversions:
            status, quadrant, action = "insufficient_data", None, None
        else:
            quadrant = common.classify_quadrant(cac, cohort["conversions"], target_cac, target_volume)
            status = "scored"
            action = common.QUADRANT_ACTIONS.get(quadrant)

        lost_is_rank, lost_is_budget = cohort_weighted_lost_is(cohort)
        lost_is = common.lost_is_diagnostic(lost_is_rank, lost_is_budget)
        cost_delta = pct_change(cohort["cost"], comparison["cost"]) if comparison else None
        conv_delta = pct_change(cohort["conversions"], comparison["conversions"]) if comparison else None
        cac_delta = pct_change(cac, comparison_cac) if (cac is not None and comparison_cac is not None) else None

        cohort_conv_rate = conversion_rate(cohort["conversions"], cohort["clicks"])
        comparison_conv_rate = conversion_rate(comparison["conversions"], comparison["clicks"]) if comparison else None
        conv_rate_delta = pct_change(cohort_conv_rate, comparison_conv_rate) if (cohort_conv_rate is not None and comparison_conv_rate is not None) else None
        note = causal_note(quadrant, cohort_conv_rate, account_avg_conv_rate,
                            lost_is_rank, lost_is_budget) if status == "scored" else None

        cohort_rows.append({
            "cluster": name,
            "status": status,
            "quadrant": quadrant or "",
            "causal_note": note or "",
            "lost_is_diagnostic": lost_is or "",
            "suggested_action": action or "",
            "cost": round(cohort["cost"], 2),
            "cost_change_pct": round(cost_delta, 1) if cost_delta is not None else "",
            "conversions": round(cohort["conversions"], 2),
            "conversions_change_pct": round(conv_delta, 1) if conv_delta is not None else "",
            "cac": round(cac, 2) if cac is not None else "",
            "cac_change_pct": round(cac_delta, 1) if cac_delta is not None else "",
            "conversion_rate": round(cohort_conv_rate * 100, 2) if cohort_conv_rate is not None else "",
            "conversion_rate_change_pct": round(conv_rate_delta, 1) if conv_rate_delta is not None else "",
            "conversions_value": round(cohort["conversions_value"], 2),
            "campaign_count": len(cohort["campaigns"]),
            "correlation_flags": build_correlation_flags(name, what_was_done["changes"]),
        })

    common.write_csv(cohort_csv_path, COHORT_CSV_FIELDS,
                      [{k: v for k, v in row.items() if k in COHORT_CSV_FIELDS} for row in cohort_rows])

    campaign_rows = classify_campaigns(
        period_campaigns, comparison_campaigns, clusters, exclude_from_scoring,
        min_conversions, args.benchmark, cluster_campaign_targets or {}, flat_campaign_target,
        account_avg_conv_rate, args.period_end, new_campaign_window_days,
        args.period_days, tcpa_min_conversions_per_30_days, tcpa_stability_pct,
        what_was_done["changes"],
    )
    campaign_rows.sort(key=lambda r: -r["cost"])
    common.write_csv(campaign_csv_path, CAMPAIGN_CSV_FIELDS,
                      [{k: v for k, v in row.items() if k in CAMPAIGN_CSV_FIELDS} for row in campaign_rows])

    campaigns_by_cluster_for_output = {}
    for row in campaign_rows:
        campaigns_by_cluster_for_output.setdefault(row["cluster"], []).append(row)

    account_period_snapshot = build_account_snapshot(period_campaigns)
    account_comparison_snapshot = build_account_snapshot(comparison_campaigns)
    account_deltas = build_account_deltas(account_period_snapshot, account_comparison_snapshot)

    clusters_out = [
        {**row, "campaigns": campaigns_by_cluster_for_output.get(row["cluster"], [])}
        for row in cohort_rows
    ]

    output = {
        "project_name": project_name,
        "for_narrative_composition_by": (
            "Claude Code, reading this file - this script emits structured findings only; see the "
            "module docstring's ARCHITECTURE note for why narrative prose is not generated here."
        ),
        "period": {"start": str(args.period_start), "end": str(args.period_end), "label": args.period_label, "days": args.period_days},
        "comparison": {"start": str(args.comparison_start), "end": str(args.comparison_end), "label": args.comparison_label},
        "benchmark": {
            "mode": args.benchmark, "note": benchmark_note,
            "target_cac": round(target_cac, 2) if target_cac is not None else None,
            "target_volume": round(target_volume, 1) if target_volume is not None else None,
        },
        "primary_conversion_name": primary_conversion,
        "quadrant_floor": min_conversions,
        "what_was_done": what_was_done,
        "performance": {
            "account": {
                "period": account_period_snapshot,
                "comparison": account_comparison_snapshot,
                "deltas": account_deltas,
            },
            "clusters": clusters_out,
        },
        "next_steps": build_next_steps(cohort_rows, campaign_rows),
    }
    Path(output_path).write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Campaign performance findings for {project_name}")
    print(f"  Period: {args.period_label}  vs  Comparison: {args.comparison_label}")
    print(f"  Benchmark: {args.benchmark} (target CAC={target_cac:.2f} target volume={target_volume:.1f})" if target_cac else f"  Benchmark: {args.benchmark} (no target - insufficient scored data)")
    print(f"  What was done: {what_was_done['status']}"
          + ("" if what_was_done["status"] == "available" else " (change-log gate not cleared - see module docstring)"))
    print(f"  Cohorts: {len(cohort_rows)} ({sum(1 for r in cohort_rows if r['status']=='scored')} scored, "
          f"{sum(1 for r in cohort_rows if r['status']=='insufficient_data')} insufficient data, "
          f"{sum(1 for r in cohort_rows if r['status']=='excluded')} excluded)")
    print(f"  Campaigns: {len(campaign_rows)} ({sum(1 for r in campaign_rows if r['status']=='scored')} scored, "
          f"{sum(1 for r in campaign_rows if r['status']=='insufficient_data')} insufficient data, "
          f"{sum(1 for r in campaign_rows if r['status']=='excluded')} excluded)")
    print(f"  Structured findings: {output_path}")
    print(f"  Cohort CSV: {cohort_csv_path}")
    print(f"  Campaign CSV: {campaign_csv_path}")


if __name__ == "__main__":
    main()

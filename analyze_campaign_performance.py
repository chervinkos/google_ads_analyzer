#!/usr/bin/env python3
"""Campaign performance narrative report (v1: Ads-only, GA4 revenue merge
is a later version, not built toward here).

Classifies each cohort (destination cluster) - and, once the campaign-level
nesting layer is added, each campaign within it - into a CAC x volume
quadrant relative to a benchmark, using "Parcels" (config's
primary_conversion_name) as the sole efficiency metric. Other conversion
actions may appear in raw data but never drive scoring.

Quadrants (never a single composite score):
  Star                              - efficient (CAC <= target) AND high
                                       volume (conversions >= target)
  Efficient (underexploited)        - efficient, but below target volume
  Review (high spend, high volume)  - high volume, but not efficient
  Underperformer                    - neither efficient nor high volume

A campaign/cohort with fewer than thresholds.min_conversions_for_quadrant
Parcels conversions in the period goes to "insufficient data" instead of
being classified either way - never silently dropped, never force-fit.
Clusters in config's exclude_from_scoring (brand) are never classified,
though they still appear in raw CSV output.

Lost Impression Share (rank/budget) is a separate diagnostic tag, never
folded into the quadrant itself - it's attached alongside a classification
as context on *why* (e.g. "Underperformer - Lost IS (budget): 34%").

Benchmark modes (--benchmark):
  cluster  Peer-average within the immediate group: at the cohort layer
           that's all other scored cohorts (so identical to `account` at
           this layer - the two modes only diverge once campaign-level
           nesting is added, where `cluster` benchmarks a campaign against
           its own cohort instead of the whole account).
  account  Whole-account scored peer average, current period.
  target   Trailing historical average from a separate --target pull (a
           window that must NOT overlap --comparison, or "target" and
           "comparison" collapse into near-duplicate benchmarks - see
           --target-label, which the caller must state explicitly).

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

Usage:
    analyze_campaign_performance.py --config configs/<project>.yaml \\
        --period period_metrics.csv --period-label "2026-08-17 to 2026-09-15" \\
        --comparison comparison_metrics.csv --comparison-label "2026-07-18 to 2026-08-16" \\
        [--benchmark cluster|account|target] \\
        [--target target_metrics.csv --target-label "..."] \\
        [--output file.md]
"""
import argparse
import sys
from pathlib import Path

import ads_common as common

REQUIRED_METRIC_COLUMNS = ["campaign", "cost", "conversions"]
OPTIONAL_METRIC_COLUMNS = [
    "channel_type", "impressions", "clicks", "conversions_value",
    "search_impression_share", "lost_is_rank", "lost_is_budget",
]
COHORT_CSV_FIELDS = [
    "cluster", "status", "quadrant", "lost_is_diagnostic", "suggested_action",
    "cost", "cost_change_pct", "conversions", "conversions_change_pct",
    "cac", "cac_change_pct", "conversions_value", "campaign_count",
]
CAMPAIGN_CSV_FIELDS = [
    "campaign", "cluster", "channel_type", "status", "quadrant", "lost_is_diagnostic", "suggested_action",
    "cost", "cost_change_pct", "conversions", "conversions_change_pct",
    "cac", "cac_change_pct", "conversions_value",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to configs/<project>.yaml")
    parser.add_argument("--period", required=True, help="Campaign-metrics CSV for the analysis period")
    parser.add_argument("--period-label", required=True, help='Actual date range analyzed, e.g. "2026-08-17 to 2026-09-15"')
    parser.add_argument("--comparison", required=True, help="Campaign-metrics CSV for the comparison period")
    parser.add_argument("--comparison-label", required=True, help="Actual comparison date range")
    parser.add_argument("--benchmark", choices=["cluster", "account", "target"], default="cluster")
    parser.add_argument("--target", default=None, help="Campaign-metrics CSV for the trailing benchmark window (required with --benchmark target)")
    parser.add_argument("--target-label", default=None, help="Actual trailing-window date range (required with --benchmark target)")
    parser.add_argument("--output", default=None, help="Markdown report path (default: <project>-campaign-performance.md)")
    args = parser.parse_args()
    if args.benchmark == "target" and (not args.target or not args.target_label):
        parser.error("--benchmark target requires both --target and --target-label")
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
                        min_conversions, benchmark_mode, cluster_targets, flat_target):
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

        rows.append({
            "campaign": name,
            "cluster": cluster,
            "channel_type": m.get("channel_type", ""),
            "status": status,
            "quadrant": quadrant or "",
            "lost_is_diagnostic": lost_is or "",
            "suggested_action": action or "",
            "cost": round(m["cost"], 2),
            "cost_change_pct": round(cost_delta, 1) if cost_delta is not None else "",
            "conversions": round(m["conversions"], 2),
            "conversions_change_pct": round(conv_delta, 1) if conv_delta is not None else "",
            "cac": round(cac, 2) if cac is not None else "",
            "cac_change_pct": round(cac_delta, 1) if cac_delta is not None else "",
            "conversions_value": round(m["conversions_value"], 2),
        })
    return rows


def main():
    args = parse_args()
    config = common.load_config(args.config)
    project_name = config.get("project_name", "project")
    clusters = config.get("clusters", [])
    primary_conversion = config.get("primary_conversion_name", "conversions")
    exclude_from_scoring = set(config.get("exclude_from_scoring", []))
    min_conversions = config.get("thresholds", {}).get("min_conversions_for_quadrant", 10)

    output_path = args.output or f"{project_name}-campaign-performance.md"
    cohort_csv_path = str(Path(output_path).with_suffix("")) + "-cohorts.csv"
    campaign_csv_path = str(Path(output_path).with_suffix("")) + "-campaigns.csv"

    period_campaigns = load_metrics_csv(args.period, "period")
    comparison_campaigns = load_metrics_csv(args.comparison, "comparison")

    period_cohorts = build_cohorts(period_campaigns, clusters, exclude_from_scoring)
    comparison_cohorts = build_cohorts(comparison_campaigns, clusters, exclude_from_scoring)

    scored_period_cohorts = {n: c for n, c in period_cohorts.items() if not c["excluded"]}
    non_excluded_period_campaigns = [m for m in period_campaigns.values()
                                      if common.match_cluster(m["campaign"], clusters) not in exclude_from_scoring]

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

        cohort_rows.append({
            "cluster": name,
            "status": status,
            "quadrant": quadrant or "",
            "lost_is_diagnostic": lost_is or "",
            "suggested_action": action or "",
            "cost": round(cohort["cost"], 2),
            "cost_change_pct": round(cost_delta, 1) if cost_delta is not None else "",
            "conversions": round(cohort["conversions"], 2),
            "conversions_change_pct": round(conv_delta, 1) if conv_delta is not None else "",
            "cac": round(cac, 2) if cac is not None else "",
            "cac_change_pct": round(cac_delta, 1) if cac_delta is not None else "",
            "conversions_value": round(cohort["conversions_value"], 2),
            "campaign_count": len(cohort["campaigns"]),
        })

    common.write_csv(cohort_csv_path, COHORT_CSV_FIELDS, cohort_rows)

    campaign_rows = classify_campaigns(
        period_campaigns, comparison_campaigns, clusters, exclude_from_scoring,
        min_conversions, args.benchmark, cluster_campaign_targets or {}, flat_campaign_target,
    )
    campaign_rows.sort(key=lambda r: -r["cost"])
    common.write_csv(campaign_csv_path, CAMPAIGN_CSV_FIELDS, campaign_rows)

    campaigns_by_cluster_for_report = {}
    for row in campaign_rows:
        campaigns_by_cluster_for_report.setdefault(row["cluster"], []).append(row)

    report = render_report(
        project_name, args, primary_conversion, min_conversions,
        target_cac, target_volume, benchmark_note, cohort_rows, campaigns_by_cluster_for_report,
    )
    Path(output_path).write_text(report, encoding="utf-8")

    print(f"Campaign performance report for {project_name}")
    print(f"  Period: {args.period_label}  vs  Comparison: {args.comparison_label}")
    print(f"  Benchmark: {args.benchmark} (target CAC={target_cac:.2f} target volume={target_volume:.1f})" if target_cac else f"  Benchmark: {args.benchmark} (no target - insufficient scored data)")
    print(f"  Cohorts: {len(cohort_rows)} ({sum(1 for r in cohort_rows if r['status']=='scored')} scored, "
          f"{sum(1 for r in cohort_rows if r['status']=='insufficient_data')} insufficient data, "
          f"{sum(1 for r in cohort_rows if r['status']=='excluded')} excluded)")
    print(f"  Campaigns: {len(campaign_rows)} ({sum(1 for r in campaign_rows if r['status']=='scored')} scored, "
          f"{sum(1 for r in campaign_rows if r['status']=='insufficient_data')} insufficient data, "
          f"{sum(1 for r in campaign_rows if r['status']=='excluded')} excluded)")
    print(f"  Report: {output_path}")
    print(f"  Cohort CSV: {cohort_csv_path}")
    print(f"  Campaign CSV: {campaign_csv_path}")


def fmt_delta(pct):
    if pct == "" or pct is None:
        return "n/a (no comparison-period data)"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


CHANNEL_ORDER = {"SEARCH": 0, "PERFORMANCE_MAX": 1}


def render_campaign_lines(campaigns, primary_conversion, indent="  "):
    """Nested campaign detail for one cohort, grouped by channel type
    (Search, then PMax, then anything else alphabetically), sorted by cost
    descending within each group."""
    lines = []
    grouped = {}
    for c in campaigns:
        grouped.setdefault(c["channel_type"] or "OTHER", []).append(c)
    for channel in sorted(grouped, key=lambda ch: (CHANNEL_ORDER.get(ch, 99), ch)):
        lines.append(f"{indent}**{channel.title().replace('_', ' ')}:**")
        for c in sorted(grouped[channel], key=lambda r: -r["cost"]):
            if c["status"] == "scored":
                tag = f" — {c['lost_is_diagnostic']}" if c["lost_is_diagnostic"] else ""
                lines.append(
                    f"{indent}- `{c['campaign']}` — **{c['quadrant']}**{tag}: cost {c['cost']} "
                    f"({fmt_delta(c['cost_change_pct'])}), {primary_conversion} {c['conversions']} "
                    f"({fmt_delta(c['conversions_change_pct'])}), CAC {c['cac']} ({fmt_delta(c['cac_change_pct'])}) "
                    f"— {c['suggested_action']}"
                )
            elif c["status"] == "insufficient_data":
                lines.append(f"{indent}- `{c['campaign']}` — insufficient data: {c['conversions']} {primary_conversion}, cost {c['cost']}")
            else:
                lines.append(f"{indent}- `{c['campaign']}` — excluded: cost {c['cost']}, {c['conversions']} {primary_conversion}")
    return lines


def render_report(project_name, args, primary_conversion, min_conversions,
                   target_cac, target_volume, benchmark_note, cohort_rows, campaigns_by_cluster):
    lines = []
    lines.append(f"# Campaign Performance Report — {project_name}")
    lines.append("")
    lines.append(f"**Period analyzed:** {args.period_label}")
    lines.append(f"**Comparison period:** {args.comparison_label}")
    lines.append(f"**Benchmark mode:** `{args.benchmark}` — {benchmark_note}")
    if target_cac is not None:
        lines.append(f"**Benchmark target:** CAC ≤ {target_cac:.2f}, volume ≥ {target_volume:.1f} {primary_conversion} conversions")
    lines.append(f"**Efficiency metric:** {primary_conversion} conversions only (other conversion actions excluded from scoring)")
    lines.append(f"**Quadrant floor:** cohorts/campaigns with fewer than {min_conversions} {primary_conversion} "
                  f"conversions in the period are \"insufficient data\", not classified either way")
    lines.append("")

    scored = [r for r in cohort_rows if r["status"] == "scored"]
    insufficient = [r for r in cohort_rows if r["status"] == "insufficient_data"]
    excluded = [r for r in cohort_rows if r["status"] == "excluded"]

    lines.append("## What changed")
    lines.append("")
    if scored:
        for r in scored:
            lines.append(
                f"- **{r['cluster']}**: cost {fmt_delta(r['cost_change_pct'])}, "
                f"{primary_conversion} conversions {fmt_delta(r['conversions_change_pct'])}, "
                f"CAC {fmt_delta(r['cac_change_pct'])} to {r['cac']}"
            )
    else:
        lines.append("- No cohorts cleared the quadrant floor this period.")
    lines.append("")

    lines.append(f"## Cohort performance (CAC × volume, benchmark = `{args.benchmark}`)")
    lines.append("")
    if scored:
        for r in scored:
            tag = f" — {r['lost_is_diagnostic']}" if r["lost_is_diagnostic"] else ""
            lines.append(f"### {r['cluster']} — {r['quadrant']}{tag}")
            lines.append(f"- Cost: {r['cost']} ({fmt_delta(r['cost_change_pct'])} vs. comparison)")
            lines.append(f"- {primary_conversion} conversions: {r['conversions']} ({fmt_delta(r['conversions_change_pct'])})")
            lines.append(f"- CAC: {r['cac']} ({fmt_delta(r['cac_change_pct'])})")
            lines.append(f"- Campaigns in cohort: {r['campaign_count']}")
            lines.append(f"- **Recommended action: {r['suggested_action']}**")
            lines.append("")
            lines.extend(render_campaign_lines(campaigns_by_cluster.get(r["cluster"], []), primary_conversion))
            lines.append("")
    else:
        lines.append("_No cohorts scored this period — see insufficient-data section below._")
        lines.append("")

    stars = [r for r in scored if r["quadrant"] == "Star"]
    if stars:
        lines.append("## What's working")
        lines.append("")
        for r in stars:
            lines.append(f"- **{r['cluster']}** is a Star: CAC {r['cac']} at {r['conversions']} conversions, both clearing benchmark.")
        lines.append("")

    lines.append("## Insufficient data")
    lines.append("")
    if insufficient:
        for r in insufficient:
            lines.append(f"- {r['cluster']}: {r['conversions']} {primary_conversion} conversions (floor is {min_conversions}), cost {r['cost']}")
            lines.extend(render_campaign_lines(campaigns_by_cluster.get(r["cluster"], []), primary_conversion))
    else:
        lines.append("_None — every cohort cleared the floor._")
    lines.append("")

    lines.append("## Excluded from scoring")
    lines.append("")
    if excluded:
        for r in excluded:
            lines.append(f"- {r['cluster']}: cost {r['cost']}, {r['conversions']} {primary_conversion} conversions (raw data only, not quadrant-classified)")
            lines.extend(render_campaign_lines(campaigns_by_cluster.get(r["cluster"], []), primary_conversion))
    else:
        lines.append("_None configured._")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()

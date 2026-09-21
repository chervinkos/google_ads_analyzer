---
description: Campaign performance narrative report - what changed, what happened, what to do next
argument-hint: <period to analyze> and what to compare it against, in plain language (e.g. "compare September to August", "this quarter vs the same quarter last year", or leave blank for the default trailing 30 vs prior 30 days)
---

Run the full campaign performance report pipeline end-to-end, then compose
the narrative yourself from the script's structured output - the script
does not generate report prose (see analyze_campaign_performance.py's
ARCHITECTURE note in its module docstring).

Config selection (per CLAUDE.md):
- If exactly one file exists under `configs/`, use it automatically.
- If multiple exist, ask the user which project to use, matching against
  each config's `project_name` and `aliases` fields (not just the
  filename) — never guess or default silently.

Pipeline:

1. **Parse the natural-language period request into real dates** — this is
   your job, not the script's; the script only accepts
   `--period-start`/`--period-end`/`--comparison-start`/`--comparison-end`
   (and `--target-start`/`--target-end` if `--benchmark target` is used),
   never free text. Common phrasings and how to resolve them:
   - "compare September to August" → period = the full calendar month of
     September in the current/most recent relevant year, comparison = the
     full calendar month of August immediately before it.
   - "this quarter vs the same quarter last year" → period = the current
     calendar quarter to date, comparison = the same quarter one year
     earlier (same start/end day-of-quarter, not just "12 months back" if
     that would misalign quarter boundaries).
   - "last 30 days vs the 30 days before that" / no period specified at
     all → default: period = trailing 30 days ending today, comparison =
     the 30 days immediately before that (same default `/find-opportunities`
     uses for its trailing/baseline windows).
   - Any other relative phrasing ("last week vs the week before", "Q1 vs
     Q2") → resolve the same way: two equal-length, non-overlapping,
     adjacent-by-default windows, unless the user's phrasing implies
     otherwise (e.g. year-over-year comparisons are same-calendar-window,
     different year, not adjacent).
   - If the phrasing is genuinely ambiguous (can't confidently resolve to
     one pair of date ranges), ask the user to confirm the exact dates
     rather than guessing.
   - NOTE: this natural-language-to-dates step is currently implemented
     ad hoc, here, for this command only. CLAUDE.md's Planned section
     flags this as a candidate for a shared helper/convention across all
     slash commands, not yet built — don't assume it exists anywhere else
     in this repo yet.
2. Resolve the client account ID from the active config's
   `client_account_id`. Via the Google Ads MCP Connector, pull campaign-
   level metrics for the period and comparison windows: cost, conversions
   (the config's `primary_conversion_name`), conv. value, clicks,
   impressions, channel type, campaign start date, and both Lost
   Impression Share fields (`metrics.search_rank_lost_impression_share`,
   `metrics.search_budget_lost_impression_share` — populated for
   Performance Max too, not just Search, per CLAUDE.md). Filter
   `metrics.impressions > 0` at the query level per CLAUDE.md's Known
   technical notes. If the user's request implies a `--benchmark target`
   comparison (a trailing historical baseline distinct from the
   comparison period), also pull a third, non-overlapping target window
   the same way.
3. Build one campaign-metrics CSV per window pulled (period, comparison,
   optionally target) — one row per campaign, with at least: Campaign,
   Cost, Conversions, Conv. value, Clicks, Impressions, Channel type,
   Campaign start date, Search lost IS (rank), Search lost IS (budget).
4. **Change-log / "what was done"**: this is gated per CLAUDE.md's Known
   technical notes ("Change-log gating") — the change_event resource's
   schema (retention, granularity, available fields) has not been
   confirmed via a live `metadata_get_resource_metadata` call. Attempt it
   if the connector is available and it hasn't been confirmed yet
   (report back what you find, the same way other gated investigations in
   this project get confirmed once); if it's still unconfirmed or the
   connector is unavailable, skip building a `--changes` CSV entirely and
   let the report's "What was done" section stay `not_available` — don't
   block the rest of the report on this.
5. Run:
   `python3 analyze_campaign_performance.py --config configs/<project>.yaml --period <period CSV> --period-start <date> --period-end <date> --comparison <comparison CSV> --comparison-start <date> --comparison-end <date> [--benchmark cluster|account|target] [--target <target CSV> --target-start <date> --target-end <date>] [--changes <changes CSV>] --output <project>-campaign-performance.json`
   using `--benchmark cluster` (the script's default) unless the user's
   request implies a different benchmark mode.
6. The script writes the structured findings JSON plus `-cohorts.csv` and
   `-campaigns.csv` raw backups (never render markdown itself - read the
   JSON, don't reimplement its math). **Read the JSON and compose the
   narrative report in this exact 3-section structure**, in prose, in your
   reply to the user (not inside the script):
   - **What was done** — plain factual list of account changes in the
     period, straight from `what_was_done.changes` if
     `what_was_done.status == "available"`; if `not_available`, say so
     plainly (change-log gate not cleared yet) rather than omitting the
     section.
   - **What happened with performance** — start with
     `performance.account` (period vs. comparison: cost, conversions,
     CR%, CAC, traffic), then walk `performance.clusters` (each cluster's
     before/after the same way), then within each cluster give a short
     campaign-vs-campaign comparison using its nested `campaigns` list
     (e.g. "Campaign A: more conversions, higher CAC" vs. "Campaign B:
     better CR%, fewer conversions"). Cross-reference `what_was_done`'s
     changes (when available) against each cluster's performance shift
     and call out a likely correlation where one plausibly exists -
     including negative correlations (e.g. a campaign still in a learning
     phase after a recent change, or a cut budget suppressing volume).
     `correlation_flags` on each cluster/campaign is currently always
     empty (the matching logic itself is gated, per CLAUDE.md - the field
     is wired in for later, not populated yet), so do this
     cross-referencing yourself, in prose, directly from the
     `what_was_done.changes` list and the before/after deltas - don't
     wait on `correlation_flags` to be non-empty. Cite each entity's
     `lost_is_diagnostic` wherever it helps explain a shift (e.g. "capped
     by budget" vs. "capped by rank/quality").
   - **Next steps** — conclusions, built from `next_steps` (scale/cut/edit
     candidates, top-by-conversions, unused-potential headroom) at both
     the cluster and campaign level: top performers, room to scale, where
     to cut, unused potential. Mention each row's `tcpa_recommendation`
     and `suggested_action` where relevant, and flag insufficient-data
     cohorts/campaigns (`status: insufficient_data`, with their
     `age_status`) separately rather than silently omitting them.
   - Do NOT reference `/find-opportunities` signals or GA4 ecommerce data
     in Next Steps yet - both are explicitly deferred, see CLAUDE.md's
     Planned section.
7. Mention the JSON/CSV file paths at the end so the user can open the raw
   data, but lead with the composed narrative, not the file paths.

If the user's request includes specific overrides (a different benchmark
mode, explicit dates instead of natural language, campaign scope), apply
those instead of the defaults above.

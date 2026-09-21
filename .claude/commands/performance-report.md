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
     full calendar month of August immediately before it. **Exception: if
     the named period is the current, still-in-progress calendar month**
     (e.g. it's 2026-09-21 and the request says "September vs August"),
     do NOT use the full month end (2026-09-30) as period-end - that
     queries days that haven't happened yet. Use month-to-date instead
     (period-start = the 1st, period-end = today), and cap the comparison
     window to the *same number of days* from August's start (comparison-
     start = August 1st, comparison-end = August 1st + (period-end -
     period-start) days) rather than the full month of August - comparing
     21 days of September against 31 days of August isn't a fair
     before/after. Confirmed live 2026-09-21: this exact phrasing, asked
     mid-September, would otherwise have queried future dates.
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
4. **Change-log / "what was done"**: the `change_event` resource's schema
   is confirmed (see CLAUDE.md's Known technical notes / this script's
   module docstring's "Change-log schema") - fields: `change_date_time`,
   `change_resource_type`, `resource_change_operation`, `campaign`,
   `ad_group`, `changed_fields`, `old_resource`, `new_resource`,
   `user_email`, `client_type`, `resource_name`. **Retention is a hard,
   rolling ~29-day window from query time** (not from the analysis
   period's dates) - before attempting the pull, check whether
   `--comparison-start` (the earlier edge of the two windows) falls within
   roughly the last 29 days of *today*. If it doesn't, the pull isn't
   possible - skip it and let "What was done" stay `not_available`, same
   as if the connector were unavailable; don't treat this as an error.
   When it's in range and the connector is available, pull `change_event`
   for `change_date_time` between `--comparison-start` and `--period-end`,
   filtered to `campaign`/`campaign_budget`/`ad_group`/`ad_group_criterion`
   `change_resource_type`s (the ones "what was done" cares about - new/
   paused campaigns, budget, bid strategy, ad group/keyword/ad changes).
   Sortable fields are only `change_date_time`, `change_resource_type`,
   `resource_change_operation`, `user_email` (not `resource_name`) - same
   pagination pattern as `search_search` (cursor on the sort key, dedupe
   by `resource_name` locally) if a pull needs more than one batch. Build
   a CSV with those columns (header names matching the API field names is
   fine - `load_change_events()` matches case-insensitively and tolerates
   underscore/space variants) and pass it via `--changes`. Note the one
   real limitation: correlation only works for campaign-scoped changes (a
   non-empty `campaign` field); ad-group/ad/keyword-scoped changes (only
   `ad_group` populated) still appear in "What was done" but aren't
   auto-matched to a cluster/campaign - call those out yourself in prose
   in step 6 if they're relevant (e.g. an ad copy edit in a cluster you're
   already discussing).
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
     plainly (either no `--changes` pull was possible this run - most
     often because the analysis period falls outside `change_event`'s
     ~29-day retention window, see step 4 - or the connector was
     unavailable) rather than omitting the section.
   - **What happened with performance** — start with
     `performance.account` (period vs. comparison: cost, conversions,
     CR%, CAC, traffic), then walk `performance.clusters` (each cluster's
     before/after the same way), then within each cluster give a short
     campaign-vs-campaign comparison using its nested `campaigns` list
     (e.g. "Campaign A: more conversions, higher CAC" vs. "Campaign B:
     better CR%, fewer conversions"). Each cluster/campaign's
     `correlation_flags` lists real, already-matched changes scoped to
     that entity (campaign-scoped changes only, within the comparison-to-
     period window) - lead with those where present, and call out a
     likely correlation where one plausibly exists, including negative
     correlations (e.g. a campaign still in a learning phase after a
     recent change, or a cut budget suppressing volume). `correlation_flags`
     does NOT cover ad-group/ad/keyword-scoped changes (see step 4) - if
     `what_was_done.changes` has one relevant to a cluster you're
     discussing (matched only by campaign name showing up in its
     description, since there's no automatic link), call it out yourself
     in prose the same way. Cite each entity's `lost_is_diagnostic`
     wherever it helps explain a shift (e.g. "capped by budget" vs.
     "capped by rank/quality").
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

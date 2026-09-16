# CLAUDE.md — Google Ads Analysis Toolkit

## Project purpose
This repo analyzes Google Ads account data — search-term waste,
campaign/cohort performance, and related reporting — for any
connected client account. Account-specific facts (IDs, thresholds,
cluster definitions) live in `configs/`, never in this file or in
the scripts themselves.

## Config selection
Each project/account has its own config file under `configs/`
(e.g. `configs/meest-post-polska.yaml`).

- If exactly **one** config file exists, use it automatically —
  no need to ask.
- If **multiple** config files exist, ask the user which project to
  use, matching against each config's `project_name` and `aliases`
  fields (not just the filename).
- Never guess or default silently when more than one config exists.

## What belongs in a config vs. what's discovered live
- **Config (business judgment, not inferable):** client account ID,
  primary conversion name, cost/click thresholds, cluster
  definitions and match rules, brand keywords.
- **Live via MCP (never hardcoded, always queried fresh):** account
  currency, timezone, campaign list, available conversion actions,
  account name.

## Access
- Google Ads data via the Google Ads MCP Connector (already connected
  in claude.ai)
- Manager (MCC) accounts cannot be queried directly for metrics —
  always resolve to the actual client account ID from the active
  config

## Output conventions
- Always save flagged/analyzed results to CSV (sorted by cost
  descending, unless a report type specifies otherwise)
- Report "insufficient data" items separately — never silently
  drop them
- Label every report with the actual date range analyzed
- For narrative-style reports: state period vs. comparison period
  explicitly, and structure output as: what changed → observed/
  expected impact → what's working → recommended next steps
  (scale / cut / edit / launch)

## Known technical notes (Google Ads data handling)
- Search-term exports may include extra header/title rows — detect
  the real header row, never assume row 0
- Handle Google's "--" placeholder as 0; strip currency symbols and
  thousands separators from numeric fields
- `clean_number()`'s locale-detection heuristic treats a lone "." or ","
  followed by exactly 3 digits as a thousands separator (genuinely
  ambiguous with real Google Ads locale formatting, e.g. "22.283" could be
  22.283 or 22,283) - this is unresolvable by the parser, so it's a
  writer-side constraint instead: any CSV this toolkit generates itself
  (e.g. from a raw MCP pull) must round numeric fields to at most 2
  decimal places before writing. A field like the API's raw conversions
  value (e.g. 22.282975) silently becomes 22283 if written with 3+ decimals
  and the rounding happens to leave exactly 3 digits after the separator
- Search terms need joining from ad_group → campaign name; some ad
  groups may be missing from an initial batch pull and require a
  follow-up fetch
- The `search_search` MCP tool has no offset/page_token — it hard-caps
  at whatever `limit` is passed, so a batch that comes back exactly at
  `limit` may not be the full result set. Paginate with a cursor built
  from the sort key itself: sort by `metrics.cost_micros DESC` (the
  resource's own `.resource_name` field is usually NOT a valid
  `ORDER BY` field per the Google Ads API — confirmed for
  `search_term_view`; use the resource's own text field, e.g.
  `search_term_view.search_term ASC`, as the secondary sort instead,
  and check `metadata_get_resource_metadata`'s `sortable` list before
  assuming any field works). After each batch, add a condition using
  the last row's cost value (`metrics.cost_micros <= <last row's
  cost_micros>`) to fetch the next page. Because `search_search`'s
  `conditions` only combine with AND (no OR), there's no single-query
  way to exclude just the tied boundary row, so successive pages will
  overlap on cost ties — dedupe locally by the row's `.resource_name`
  field (selectable even when not sortable) as pages are merged, not
  server-side. Repeat until a batch returns fewer rows than `limit`.
  Write the full merged result straight to a file as it's built —
  never print every row back into the conversation, only a summary
  (row count, cost total, a handful of standout lines). In practice,
  accounts with a long tail of high-click/near-zero-CPC terms can take
  many pages (7+ seen for one 90-day pull) — if resource constraints
  force a cutoff before reaching a final partial batch, report the
  cost floor actually reached and the total captured rather than
  silently presenting a partial pull as complete
- Cluster matching (from config) is first-match-wins, top to bottom,
  with an explicit catch-all cluster for anything unmatched
- Brand-term detection is separate from campaign-based cluster
  matching — a term can live in a non-brand campaign but still match
  brand_keywords content-wise; this feeds pattern-seeking/re-homing
  suggestions, not just campaign-based grouping
- Lost Impression Share (`metrics.search_rank_lost_impression_share`,
  `metrics.search_budget_lost_impression_share`) is populated for
  **Performance Max** campaigns too, not just Search - confirmed live
  against this account (verify per-account if metrics look suspiciously
  zero, since a channel-type gap would silently look like "no lost IS"
  rather than "not available")
- `metrics.cost_micros` cannot be selected/filtered in the same query as
  `segments.conversion_action_name` (cost isn't attributable per-
  conversion-action) - pull cost and per-action conversions as two
  separate queries and join by campaign name, not one combined query
- Topic classification (`topic_keywords` in config, `analyze_topic_alignment.py`)
  is a second, independent axis from clusters — content-based (matches the
  search term's text) rather than campaign-based (matches the campaign
  name). A topic optionally links to a cluster via `cluster:` for
  countries already served (the "core" topics); topics with no `cluster`
  are "tracked" countries with no dedicated campaign yet. Re-home
  suggestions must only ever point at a live **ENABLED** campaign — a
  PAUSED or REMOVED campaign is never a valid reassignment target, only a
  cheaper-to-reactivate signal, so campaign status must always be pulled
  fresh via MCP (never cached) before running this analysis

## Scripts
- `analyze_wasted_spend.py` — search-term waste analysis
  (`--config configs/<project>.yaml --input file.csv [--min-clicks N] [--min-cost N] [--output file.csv]`)
- `analyze_search_opportunities.py` — search-term opportunity/pattern
  discovery: new-demand, intent-pattern, underexploited high-performer,
  and rising-trend signals
  (`--config configs/<project>.yaml --trailing file.csv --baseline file.csv --keywords file.csv [--output file.csv]`)
- `analyze_topic_alignment.py` — content-based topic (country/destination)
  classification vs. campaign-based clusters: flags mismatches (a term's
  content says one country, its campaign says another) with a re-home
  suggestion, and surfaces real demand for tracked countries with no
  dedicated campaign yet
  (`--config configs/<project>.yaml --input file.csv --campaigns file.csv [--output file.csv]`)
- `analyze_campaign_performance.py` — campaign performance narrative report
  (v1, Ads-only): cohort (destination cluster) and nested campaign-level
  CAC × volume quadrant classification against a switchable benchmark
  (cluster/account/target), with Lost Impression Share as a separate
  diagnostic tag, output as a markdown narrative + CSV backups
  (`--config configs/<project>.yaml --period file.csv --period-label "..." --comparison file.csv --comparison-label "..." [--benchmark cluster|account|target] [--target file.csv --target-label "..."] [--output file.md]`)
- `ads_common.py` — shared helpers (header-row detection, numeric
  cleanup, cluster/brand/intent/topic/quadrant matching) used by the
  scripts above
- (additional analysis scripts to be added here as they're built)

## Slash commands
- `/analyze-waste` — runs the full waste-analysis pipeline end-to-end
  using the active config's defaults, with inline overrides supported
- `/find-opportunities` — runs the full opportunity-discovery pipeline
  end-to-end using the active config's defaults, with inline overrides
  supported

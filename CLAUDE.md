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

## Scripts
- `analyze_wasted_spend.py` — search-term waste analysis
  (`--config configs/<project>.yaml --input file.csv [--min-clicks N] [--min-cost N] [--output file.csv]`)
- `analyze_search_opportunities.py` — search-term opportunity/pattern
  discovery: new-demand, intent-pattern, underexploited high-performer,
  and rising-trend signals
  (`--config configs/<project>.yaml --trailing file.csv --baseline file.csv --keywords file.csv [--output file.csv]`)
- `ads_common.py` — shared helpers (header-row detection, numeric
  cleanup, cluster/brand/intent matching) used by the scripts above
- (additional analysis scripts to be added here as they're built)

## Slash commands
- `/analyze-waste` — runs the full waste-analysis pipeline end-to-end
  using the active config's defaults, with inline overrides supported
- `/find-opportunities` — runs the full opportunity-discovery pipeline
  end-to-end using the active config's defaults, with inline overrides
  supported

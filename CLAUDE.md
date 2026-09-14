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
  from the sort key itself: sort by `metrics.cost_micros DESC` plus a
  unique tiebreaker (`search_term_view.resource_name` ASC) so row
  order is deterministic, then after each batch add a condition using
  the last row's cost value (`metrics.cost_micros <= <last row's
  cost_micros>`, with a strictly-less-than comparison on the
  tiebreaker field to exclude the row itself when costs are tied) to
  fetch the next page. Repeat until a batch returns fewer rows than
  `limit`. Write the full merged result straight to a file as it's
  built — never print every row back into the conversation, only a
  summary (row count, cost total, a handful of standout lines)
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

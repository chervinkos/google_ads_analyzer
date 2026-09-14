Run the full search-term opportunity/pattern discovery pipeline end-to-end.

Config selection (per CLAUDE.md):
- If exactly one file exists under `configs/`, use it automatically.
- If multiple exist, ask the user which project to use, matching against
  each config's `project_name` and `aliases` fields (not just the
  filename) — never guess or default silently.

Pipeline:
1. Resolve the client account ID from the active config's
   `client_account_id`. Via the Google Ads MCP Connector, pull:
   - a trailing-window search-term report (default: last 30 days, unless
     the user specifies a different window)
   - a baseline-window search-term report immediately preceding it
     (default: the 30 days before the trailing window)
   - the current active keyword list for the account

   `search_search` has no offset/page_token — paginate per CLAUDE.md's
   "Known technical notes" (cursor on `metrics.cost_micros` plus a
   tiebreaker) rather than assuming one batch is the full result.
   TODO: same PMax gap as `/analyze-waste` — `search_term_view` doesn't
   cover Performance Max campaigns; PMax term-level data comes from
   `campaign_search_term_view` instead (see `/analyze-waste` for the
   resource details) and still needs merging in here too, for both the
   trailing and baseline pulls. Until then, opportunity signals from
   PMax traffic are missing; flag this in the summary rather than
   silently omitting it.
2. Map ad groups to their campaign names for both search-term pulls
   (fetch any ad groups missing from the initial batch pull).
3. Build three CSVs from the pulled data: trailing search terms, baseline
   search terms (each with Search term, Campaign, Ad group, Clicks, Cost,
   Conversions), and the active keyword list (with a Keyword column).
4. Run:
   `python3 analyze_search_opportunities.py --config configs/<project>.yaml --trailing <trailing CSV> --baseline <baseline CSV> --keywords <keyword-list CSV>`
   using script defaults, unless the user's request specifies different
   thresholds (`--min-conversions`, `--max-cac-ratio`, `--min-trend-clicks`,
   `--trend-growth-pct`).
5. The script writes one CSV (sorted by cost descending) with every
   flagged term tagged by signal type — `new_demand`, `intent_pattern`,
   `underexploited`, `rising_trend` (a term can carry more than one) —
   and a suggested action per row.
6. Present the output to the user with a summary grouped by signal type:
   counts per signal, and 3-5 standout terms per signal worth a manual
   look. Structure the narrative as: what changed → observed/expected
   impact → what's working → recommended next steps (scale / cut / edit
   / launch), per CLAUDE.md's narrative-report convention.

If the user's request includes specific overrides (different date
ranges, thresholds, or campaign scope), apply those instead of the
defaults.

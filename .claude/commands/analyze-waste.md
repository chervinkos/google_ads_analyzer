Run the full search-term waste analysis pipeline end-to-end.

Config selection (per CLAUDE.md):
- If exactly one file exists under `configs/`, use it automatically.
- If multiple exist, ask the user which project to use, matching against
  each config's `project_name` and `aliases` fields (not just the
  filename) — never guess or default silently.

Pipeline:
1. Resolve the client account ID from the active config's
   `client_account_id`. Pull campaign and search-term data for that
   account via the Google Ads MCP Connector, using the default 90-day
   baseline window (unless the user specifies a different window in
   their request).
2. Map ad groups to their campaign names (fetch any ad groups missing
   from the initial batch pull — some may not come back in the first
   pull and need a follow-up fetch).
3. Build a CSV of the pulled data with at least these columns: Search
   term, Campaign, Ad group, Clicks, Cost, Conversions.
4. Run:
   `python3 analyze_wasted_spend.py --config configs/<project>.yaml --input <built CSV>`
   using the config's `thresholds.min_clicks` / `thresholds.min_cost`
   defaults, unless the user's request specifies overrides (pass those
   as `--min-clicks` / `--min-cost`).
5. The script writes the flagged CSV (sorted by cost descending, cluster-
   and brand-tagged) and a separate `*-insufficient-data.csv` for rows
   that couldn't be analyzed (missing search term or campaign) — never
   silently drop those, report them alongside the main results.
6. Present the output file(s) to the user with a brief summary: total
   flagged terms, total wasted spend, insufficient-data count, and 3-5
   standout line items worth a manual look (call out any that are also
   `brand_term: True`, since those may warrant separate handling from
   destination-based waste).

If the user's request includes specific overrides (different date range,
thresholds, or campaign), apply those instead of the defaults.

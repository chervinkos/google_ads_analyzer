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
   their request). `search_search` has no offset/page_token — paginate
   per CLAUDE.md's "Known technical notes" (cursor on `metrics.cost_micros`
   plus a tiebreaker) rather than assuming one batch is the full result.
   TODO: Performance Max campaigns are not covered by `search_term_view`
   at all — it excludes PMax by design. PMax term-level data comes from
   `campaign_search_term_view` instead (confirmed against the Search
   terms report UI: real individual query text, "Performance Max" match
   type, full cost/click/impression metrics — same shape as
   `search_term_view`, not the separate category-aggregated
   `campaign_search_term_insight` resource). Merging it in is a
   straightforward union with the Search-campaign pull — same
   term/cost/conversion shape, just sourced from two resources depending
   on campaign type, no category-label handling needed. Still confirm
   the exact field names on `campaign_search_term_view` with
   `metadata_get_resource_metadata` before wiring up the fetch, the same
   way cluster patterns were checked against real campaign names rather
   than assumed. Until this merge is implemented, PMax campaigns are
   absent from waste analysis even though they may carry real spend —
   flag this explicitly in the summary presented to the user rather than
   silently omitting them.
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

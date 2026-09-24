Run the full search-term opportunity/pattern discovery pipeline end-to-end.

**Standalone and opt-in.** Run this only when explicitly requested — by
name, or by a request specifically about new-demand/underexploited/
rising-trend signals. It is not part of any default or combined
analysis: a general "run the analysis" or "give me a report" request
means `/analyze-waste` alone, never this command bundled in on top,
unless the user separately asks for opportunity discovery too.

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
   `campaign_search_term_view` instead (see `/analyze-waste` and
   CLAUDE.md's "Known technical notes" for the resource details and the
   required `advertising_channel_type = 'PERFORMANCE_MAX'` campaign
   filter — this resource is not PMax-exclusive and will double-count
   Search-campaign rows if merged in unfiltered) and still needs merging
   in here too, for both the trailing and baseline pulls. Until then,
   opportunity signals from PMax traffic are missing; flag this in the
   summary rather than silently omitting it. When this merge is built,
   also pull `segments.search_term_targeting_status` alongside it — the
   PMax path's confirmed Added/Excluded equivalent (see step 3 below and
   CLAUDE.md's "Known technical notes"; `campaign_search_term_view.status`
   does not exist as a field, don't reach for it).
2. Map ad groups to their campaign names for both search-term pulls
   (fetch any ad groups missing from the initial batch pull).
3. Build three CSVs from the pulled data: trailing search terms, baseline
   search terms (each with Search term, Campaign, Ad group, Clicks, Cost,
   Conversions), and the active keyword list (with a Keyword column).
   Also include the term's Added/Excluded status, confirmed available on
   both paths (see CLAUDE.md's "Known technical notes"): pull
   `search_term_view.status` for Search rows and (once the PMax merge
   above is built) `segments.search_term_targeting_status` for PMax rows
   — same underlying enum, both map to `term_status` via
   `ads_common.normalize_term_status()`. Give the column a header
   `resolve_columns()` recognizes (e.g. "Search term status" for Search
   rows, "Search term targeting status" for PMax rows — see
   `ads_common.COLUMN_CANDIDATES`'s `term_status` entry) so it resolves
   the same way regardless of which path a row came from. One thing to
   keep in mind when eyeballing results (see CLAUDE.md for the full
   writeup): a PMax row normalizing to "added" or "added_excluded" would
   be unexpected (PMax has no keywords) and worth a second look.
4. Run:
   `python3 analyze_search_opportunities.py --config configs/<project>.yaml --trailing <trailing CSV> --baseline <baseline CSV> --keywords <keyword-list CSV>`
   using script defaults, unless the user's request specifies different
   thresholds (`--min-conversions`, `--max-cac-ratio`, `--min-trend-clicks`,
   `--trend-growth-pct`).
5. The script writes one CSV (sorted by cost descending) with every
   flagged term tagged by signal type — `new_demand`, `underexploited`,
   `rising_trend` (a term can carry more than one) — and a suggested
   action per row. Added/Excluded status genuinely filters
   `underexploited` only (a term already Added isn't underexploited by
   definition); `new_demand`/`rising_trend` are never filtered by it. The
   output's `in_account` column is a plain boolean — True if the term is
   Added anywhere across any campaign it appears in, matching exactly
   what the `underexploited` filter itself checks — rather than a
   per-campaign status string.
6. Present the output to the user with a summary grouped by signal type:
   counts per signal, and 3-5 standout terms per signal worth a manual
   look. Structure the narrative as: what changed → observed/expected
   impact → what's working → recommended next steps (scale / cut / edit
   / launch), per CLAUDE.md's narrative-report convention.

If the user's request includes specific overrides (different date
ranges, thresholds, or campaign scope), apply those instead of the
defaults.

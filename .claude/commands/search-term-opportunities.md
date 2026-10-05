Run the full search-term opportunity/pattern discovery pipeline end-to-end.

**Standalone and opt-in.** Run this only when explicitly requested — by
name, or by a request specifically about new-demand/underexploited
signals. It is not part of any default or combined
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
   - the current active keyword list for the account
   - the live campaign list (`campaign.name`, `campaign.status`) — every
     status, every channel type (PMax included), pulled fresh this run and
     never cached. `new_demand` needs it to know which topics have an
     ENABLED campaign serving them; the script filters to ENABLED itself.
     This is a deliberate exception to CLAUDE.md's "filter by
     `metrics.impressions > 0`" rule: coverage depends on a campaign being
     live, not on it having had impressions in the window, so don't select
     metrics or filter on impressions for this query.

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
   in here too. Until then,
   opportunity signals from PMax traffic are missing; flag this in the
   summary rather than silently omitting it. When this merge is built,
   also pull `segments.search_term_targeting_status` alongside it — the
   PMax path's confirmed Added/Excluded equivalent (see step 3 below and
   CLAUDE.md's "Known technical notes"; `campaign_search_term_view.status`
   does not exist as a field, don't reach for it).
2. Map ad groups to their campaign names for the search-term pull
   (fetch any ad groups missing from the initial batch pull).
3. Build three CSVs from the pulled data: trailing search terms (with
   Search term, Campaign, Ad group, Clicks, Cost, Conversions), the
   active keyword list (with a Keyword column), and the live campaign list
   (Campaign, Status columns).
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
   `python3 analyze_search_opportunities.py --config configs/<project>.yaml --trailing <trailing CSV> --keywords <keyword-list CSV> --campaigns <campaign-list CSV>`
   using script defaults, unless the user's request specifies different
   thresholds (`--min-conversions`, `--max-cac-ratio`).
5. The script writes one CSV (sorted by cost descending) with every
   flagged term tagged by signal type — `new_demand`, `underexploited`
   (a term can carry both) — and a suggested action per row. `new_demand`
   is content-based, the same engine as
   `analyze_topic_alignment.py` (`ads_common.classify_topic`): it fires only when the term's text
   matches exactly one `topic_keywords` topic that has no ENABLED campaign
   serving it (`classify_topic()` = `tracked_no_campaign`). Terms that
   match no topic (`none`), conflicting topics (`ambiguous`), or a topic
   with a live campaign (`single` — a wrong-campaign term is
   `analyze_topic_alignment.py`'s re-home signal, not new demand) are not flagged.
   It does not depend on which campaign(s) the term ran in. The script
   prints a per-label tally and warns on stderr if the campaign list looks
   empty or stale — surface that warning to the user, don't ignore it.
   Added/Excluded status genuinely filters
   `underexploited` only (a term already Added isn't underexploited by
   definition); `new_demand` is never filtered by it. The
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

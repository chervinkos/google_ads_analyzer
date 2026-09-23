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
- Always filter by `metrics.impressions > 0` at the query level (not
  post-processing) for campaign, ad group, keyword, and search-term
  pulls — unless a task specifically needs zero-activity entities (e.g.
  auditing paused/never-served keywords). This keeps pagination cursors
  moving through real active-period data instead of burning pages/
  connector calls on placeholder or inactive rows. Search-term reports
  already require impressions > 0 implicitly (a term can't appear
  without at least one impression), so this rule mainly matters for
  other entity types — `campaign`, `ad_group`, `ad_group_criterion`,
  `keyword_view`, etc.
- `campaign_search_term_view` (used to backfill Performance Max search
  terms, since `search_term_view` excludes PMax by design) is **not
  PMax-exclusive** — it returns search-term rows for every campaign
  type, Search included. Confirmed on a live pull (2026-09-15): 959 of
  2,919 rows from an unfiltered `campaign_search_term_view` query were
  duplicate Search-campaign data already covered by `search_term_view`.
  The resource has no `campaign.advertising_channel_type` field of its
  own — `metadata_get_resource_metadata` shows only
  `campaign_search_term_view.*`, `metrics.*`, and `segments.*` as
  selectable/filterable — so the channel-type filter can't happen in
  the same query. Two-step fix: first query the `campaign` resource for
  `campaign.advertising_channel_type = 'PERFORMANCE_MAX'` to get the
  account's PMax campaign resource names, then filter
  `campaign_search_term_view.campaign IN (<those resource names>)`
  before merging into a Search-campaign pull. Never merge
  `campaign_search_term_view` rows in unfiltered — it double-counts
  Search-campaign spend.
- PMax click-tail sizing (2026-09-15 investigation, meest-post-polska):
  a live, complete (non-truncated — no batch hit the query `limit`)
  pull of `campaign_search_term_view` for this account's 39 PMax
  campaigns over a 90-day window (2026-06-17–2026-09-14, filtered per
  the gotcha above) found 3,610 rows with clicks 1–3, totaling
  13,125.71 PLN, vs. 475 rows / 17,754.38 PLN for clicks ≥4 (30,880.09
  PLN combined). The 1–3-click tail is ~88% of rows but ~43% of PMax
  search-term cost in this window — a real fraction, not noise.
  Individual row cost in this tail ranged 0.00–79.10 PLN. Treat any
  partial/paginated PMax pull's reported total as a **floor**, not a
  final number: the actual gap depends on where that pull's pagination
  stopped, and could run as high as the full tail total above if the
  cutoff landed before reaching this low-cost range. Don't assert a
  click-tail gap is immaterial without recomputing it this way — confirm
  pagination actually reached a final partial batch (one returning fewer
  rows than `limit`, per the pagination note below) before treating a
  PMax total as complete.
- Search-term exports may include extra header/title rows — detect
  the real header row, never assume row 0
- Handle Google's "--" placeholder as 0; strip currency symbols and
  thousands separators from numeric fields
- `clean_number()`'s locale-detection heuristic treats a lone "." or ","
  followed by exactly 3 digits as a thousands separator (genuinely
  ambiguous *for a single cell in isolation* with real Google Ads locale
  formatting, e.g. "22.283" could be 22.283 or 22,283 - a field like the
  API's raw conversions value, e.g. 22.282975, becomes 22283 if this is
  guessed wrong). This is resolved at the column level instead of the
  cell level: `infer_decimal_style()` scans a whole column of raw values
  for any one unambiguous cell (a real export's number formatting is
  consistent within a column), and `resolve_decimal_styles()` does this
  for several columns at once - call it right after `resolve_columns()`
  and pass the result into every `clean_number()` call for that column
  (all four scripts' loaders and `aggregate_campaign_metrics()` already do
  this). Only when a column has *no* unambiguous value anywhere (rare -
  e.g. every row happens to land on an exact 3-digit tail) does
  `clean_number()` fall back to its old per-cell guess - as a residual
  safety net for that case, still round numeric fields to at most 2
  decimal places when hand-building a CSV from a raw MCP pull
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
- PL-UA/US/UK/DE/IT/CZ/DK's `topic_keywords` patterns (Russian Cyrillic
  forms, richer US/UK phrasing, the Denmark/Czech false-positive fixes -
  see the detailed comments in `configs/meest-post-polska.yaml`) were
  ported 2026-09-21 from a separate branch, then **live-verified 2026-09-21
  against this account's real search-term data** (a 60-day trailing+
  baseline pull run through `analyze_topic_alignment.py`, ~3,740 unique
  terms). Denmark's `\bdani[aei]\b` fix and Czech's lookbehind pattern both
  held up exactly as designed on real data (45 and 39 real matches
  respectively, no false positives on the documented collision risks).
  Three real bugs were found and fixed in that same pass:
    - PL-IT's bare `"ital"` false-positived on `"digital"` (confirmed live
      against this account's own Etsy campaigns - "digital products on
      etsy" etc. wrongly tagged PL-IT; also risked "capital"/"hospital").
      Fixed with a lookbehind boundary: `(?<![A-Za-z])ital`.
    - PL-UK had no bare "uk" pattern at all (deliberately omitted - see
      below), which was a **material** false negative: 121 unique
      unclassified real terms containing plain "uk" (~973 cost / 439
      clicks over 60 days), including two confirmed real mismatches
      ("dhl uk to poland" sitting in Search-Etsy, "paczki z uk do polski
      inpost" sitting in Search-Międzynarodowe) that the tool couldn't
      flag with no pattern to match against. Fixed by adding
      `(?<![a-ząćęłńóśźż])uk(?![a-ząćęłńóśźż])` - a Polish-diacritic-
      excluding lookaround (same technique as PL-CZ's pattern) that
      resolves the original "us"/"uk" collision risk (confirmed live to
      reject "usługa"/"ukąszenie" while still matching "do uk"/"uk to
      poland"). "us" has the same theoretical risk but showed zero real
      coverage gap in this account's query mix (consistently "usa", not
      bare "us") - left unfixed as a documented candidate, not applied
      speculatively.
    - PL-DE was missing the German-language spelling "deutschland" (minor,
      one real example found: "meest post deutschland" unclassified).
      Added to the pattern list.
- `analyze_campaign_performance.py`'s quadrant-eligibility floor
  (`thresholds.min_conversions_per_30_days` in config) is **proportional
  to the selected period, not a flat count** — 10 Parcels conversions over
  90 days is a real low-rate problem, not "just needs more data" the way
  10 over 2 weeks would be. The script derives actual date ranges from
  `--period-start`/`--period-end` (required, `YYYY-MM-DD`) rather than a
  free-text label, computes `period_days` from them, and scales:
  `floor = max(MIN_QUADRANT_FLOOR_ABSOLUTE, round(min_conversions_per_30_days
  * period_days / 30))` (hard floor of 3, so a very short period still
  needs a few real data points). A campaign under the floor is split by
  `campaign_age_status()` into **new** (started within
  `thresholds.new_campaign_window_days` of the period's *end* date — low
  volume here is upside potential, not a problem), **stagnant** (older,
  still below floor), or **unknown** (no usable `start_date` column in the
  input CSV) — cohorts aren't split this way since a cohort mixes
  campaigns of different ages, but its nested campaign lines are. Same
  --target-start/--target-end pattern applies to `--benchmark target`'s
  trailing window, and now that all three windows are real dates, an
  overlap between --target and --comparison is a hard error, not just a
  documented caution
- `analyze_campaign_performance.py`'s quadrant classification stays
  exactly 4 categories (Star/Efficient/Review/Underperformer) — conversion
  rate (vs. the account's own average for the period) and Lost Impression
  Share never enter that formula. They only feed `causal_note()`, a short
  qualitative annotation shown *alongside* the quadrant label explaining
  the likely "why" (e.g. "Underperformer — conv. rate 1.2% (below average)
  → likely a traffic quality/offer issue, not budget" vs. "conv. rate 8%
  (above average), Lost IS (budget): 34% → traffic is efficient but capped
  by budget; increase budget rather than cutting" — same CAC-based
  Underperformer label, very different fix). Do not read this as a hidden
  8-cell quadrant; it's prose, not a second axis
- `analyze_campaign_performance.py`'s tCPA candidacy recommendation
  (`tcpa_recommendation()`) is a separate, per-campaign-only suggestion
  built from the same CAC/volume data, using
  `thresholds.tcpa_min_conversions_per_30_days` (default 30 — Google's own
  general guidance for stable Target CPA performance, scaled to the
  period the same way the quadrant floor is) as the volume bar, plus a
  period-over-comparison CAC swing beyond `thresholds.tcpa_cac_stability_pct`
  as a stability proxy (a two-period comparison can't measure true
  variance, just flag a swing big enough to be a caution). Both thresholds
  are explicitly adjustable assumptions, not hard rules — state that when
  presenting a tCPA recommendation, don't repeat it as settled guidance
- **Added/Excluded search-term status (`term_status`) — LIVE-VERIFIED
  2026-09-21.** `search_term_view.status` (Search path) is selectable/
  filterable/sortable and returns real ADDED/EXCLUDED/NONE values
  (confirmed via a 2,000+-row live pull: ADDED/EXCLUDED/NONE all present).
  The PMax path does **not** have `campaign_search_term_view.status` (that
  field does not exist on the resource per `metadata_get_resource_metadata`)
  - its equivalent is `segments.search_term_targeting_status`, confirmed
  live with the same three commonly-seen values (ADDED/EXCLUDED/NONE).
  Semantic equivalence (not just matching label strings) was then confirmed
  2026-09-21 against Google's own API reference: both `search_term_view.status`
  and `segments.search_term_targeting_status` are typed as the exact same
  enum, `SearchTermTargetingStatusEnum.SearchTermTargetingStatus`, with the
  identical description ("whether the search term is currently one of your
  targeted or excluded keywords") on both fields - not a coincidental label
  match, the same enum by design. Full value set (both paths, per Google's
  reference): `ADDED`, `EXCLUDED`, `ADDED_EXCLUDED`, `NONE`, plus the
  protocol meta-values `UNKNOWN`/`UNSPECIFIED` (return-only/request-only,
  not real data). Two practical caveats confirmed 2026-09-23, both
  documented rather than assumed:
    - **On the PMax path, `ADDED` and `ADDED_EXCLUDED` should be treated
      as "can't occur"** - Performance Max has no keywords, so nothing on
      that path can be directly marked as Added; only `EXCLUDED` and
      `NONE` are practically meaningful there. `normalize_term_status()`
      still recognizes all four values on both paths (it has no path-
      awareness and shouldn't need any - the enum is genuinely shared),
      but a PMax row normalizing to "added"/"added_excluded" would be
      unexpected and worth a second look, not routine.
    - **`EXCLUDED` counts are not comparable as totals between the Search
      and PMax paths** - the two resources aggregate at different levels
      (`search_term_view` at ad group, `campaign_search_term_view`/
      `segments.search_term_targeting_status` at campaign), so summing
      "N excluded terms" on one path and comparing it to the other's sum
      compares different units, not the same fact measured twice.
      `term_status` should only be compared or reasoned about per-row,
      never as a summed count across paths.
    - `ADDED_EXCLUDED` and `UNKNOWN` were never observed on either path in
      a live 90-day window (only `ADDED`/`EXCLUDED`/`NONE` showed up in
      practice) - noted as an open edge case this account's data hasn't
      exercised, not as evidence the code handles it correctly. Re-check
      if either value turns up in a future pull, rather than assuming
      today's untested handling is right.
  **Status: code wired, not yet re-verified together.** These two
  caveats and the `segments.search_term_targeting_status` PMax pull
  wiring (see the Scripts/slash-command entries) were added 2026-09-23
  without a live MCP session (none was available); the underlying enum
  equivalence itself was already live-confirmed 2026-09-21 (above), but
  a fresh session with live access still needs to re-run `term_status`
  checks across both Search and PMax paths *together*, with this wiring
  and these caveats in place, before this item is fully closed.
  `ads_common.normalize_term_status()` already handled
  `ADDED_EXCLUDED` correctly (substring-matches both "add" and "exclud")
  before this was confirmed - no code change needed there. `ads_common.py`'s
  `COLUMN_CANDIDATES` now includes a "search term targeting status" alias
  for `term_status` so a PMax-sourced CSV resolves correctly (this was a
  real gap, confirmed by testing `resolve_columns()` against that literal
  header text before the fix - it silently failed to resolve, degrading to
  "none" for every PMax row). Ran a real 30-day search-term pull through both
  `analyze_topic_alignment.py` and `analyze_search_opportunities.py`:
  `term_status` flows through with real values in both output CSVs (not
  blank), "already excluded here" and "already present as a keyword in"
  both fire correctly on real Excluded/Added rows, `underexploited`
  correctly excludes every Added-anywhere term (verified against a real
  high-conversion Added term that would otherwise obviously qualify), and
  `new_demand`/`rising_trend` correctly keep Excluded terms as context
  instead of filtering them. `ads_common.normalize_term_status()`'s
  tolerant substring matching ("add"/"exclud") is no longer a hedge
  against an unconfirmed field - it stays as reasonable defensive parsing
  regardless. Handling differs by script/signal, not a blanket filter:
    - `analyze_wasted_spend.py`: deliberately ignores it entirely - a
      term burning budget without conversions is worth flagging whether
      or not it's technically already Excluded (exclusion may not be
      working, or may not be live yet).
    - `analyze_topic_alignment.py`'s mismatch signal: if a term is
      already Excluded in the (wrong) campaign it's flagged in, the
      suggestion becomes "already excluded here" instead of repeating
      a re-home ask that's no longer urgent (Google's already
      suppressing it there). If it's already Added as a keyword in one
      of the re-home target campaigns, that's noted alongside the
      suggestion ("already present as a keyword in: ...").
    - `analyze_search_opportunities.py`: a real filter for
      `underexploited` only (a term already Added, in ANY campaign it
      appears in, isn't underexploited by definition - excluded from
      that signal entirely). Never filters `new_demand` or
      `rising_trend` - a rising-trend term that's currently Excluded is
      a "reconsider this decision" signal worth surfacing, not noise to
      hide, so it's included as a context column there instead.
- `intent_pattern` (a signal in `analyze_search_opportunities.py`) and
  its supporting config section (`intent_keywords`) and helper
  (`ads_common.match_intents`) were removed 2026-09-20 - it never had a
  clearly defined classification rule, no real business use case
  surfaced across this project, and its distinction from `new_demand`
  was never clear. The remaining three signals (`new_demand`,
  `underexploited`, `rising_trend`) are considered sufficient
- **`change_event` (the Google Ads change-log resource) — confirmed live
  2026-09-21** via `metadata_get_resource_metadata` plus live pulls
  against this account, ahead of any script in this repo actually using
  it (see `claude/performance-report-v2`, not yet merged here, for the
  consumer). Selectable/filterable fields: `change_date_time`,
  `change_resource_type`, `resource_change_operation`, `campaign`,
  `ad_group`, `changed_fields`, `old_resource`, `new_resource`,
  `user_email`, `client_type`, `resource_name`. Sortable: only
  `change_date_time`, `change_resource_type`, `resource_change_operation`,
  `user_email` — **not** `resource_name`, the same pagination gotcha as
  `search_term_view` (cursor on `change_date_time DESC`, dedupe by
  `resource_name` on ties). Retention is a rolling **~29-day lookback from
  query time** — a start date exactly 30 days back is rejected
  ("requested start date is too old") — independent of whatever analysis
  period is being examined, so a change-log pull is only ever possible
  when that period overlaps roughly the trailing month; this is the
  retrospective/forward-only answer: retrospective, but hard-capped to
  that trailing window, never further back. `LIMIT` must be ≤ 10000, per
  the connector's own tool hint. Granularity: `change_event.campaign`
  links a campaign-scoped change; `change_event.ad_group` links an
  ad-group-scoped one (ad/ad_group/ad_group_ad/ad_group_criterion-level
  changes) — there is no separate keyword/criterion identifier field, so
  a specific keyword change is only distinguishable via
  `changed_fields`/`old_resource`/`new_resource` on an
  `AD_GROUP_CRITERION`-typed event, not a dedicated column. Confirmed
  live on this account: `ASSET` CREATE events dominate day-to-day (no
  `campaign`/`ad_group` link), alongside real `CAMPAIGN` (status),
  `CAMPAIGN_BUDGET` (amountMicros), etc. changes with those links
  populated.

## Scripts
- `analyze_wasted_spend.py` — search-term waste analysis
  (`--config configs/<project>.yaml --input file.csv [--min-clicks N] [--min-cost N] [--output file.csv]`)
- `analyze_search_opportunities.py` — search-term opportunity/pattern
  discovery: new-demand, underexploited high-performer, and rising-trend
  signals, Added/Excluded-aware per signal (see Known technical notes)
  (`--config configs/<project>.yaml --trailing file.csv --baseline file.csv --keywords file.csv [--output file.csv]`)
- `analyze_topic_alignment.py` — content-based topic (country/destination)
  classification vs. campaign-based clusters: flags mismatches (a term's
  content says one country, its campaign says another) with a re-home
  suggestion (Added/Excluded-aware, see Known technical notes), and
  surfaces real demand for tracked countries with no dedicated campaign yet
  (`--config configs/<project>.yaml --input file.csv --campaigns file.csv [--output file.csv]`)
- `analyze_campaign_performance.py` — campaign performance narrative report
  (v1, Ads-only): cohort (destination cluster) and nested campaign-level
  CAC × volume quadrant classification against a switchable benchmark
  (cluster/account/target), a period-proportional insufficient-data floor
  that splits low-volume campaigns into new/stagnant/unknown-age, a
  causal-note layer (conversion rate vs. account average + Lost IS)
  explaining *why* alongside each quadrant label, and a per-campaign tCPA
  candidacy recommendation - output as a markdown narrative + CSV backups
  (`--config configs/<project>.yaml --period file.csv --period-start YYYY-MM-DD --period-end YYYY-MM-DD --comparison file.csv --comparison-start YYYY-MM-DD --comparison-end YYYY-MM-DD [--benchmark cluster|account|target] [--target file.csv --target-start YYYY-MM-DD --target-end YYYY-MM-DD] [--output file.md]`)
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

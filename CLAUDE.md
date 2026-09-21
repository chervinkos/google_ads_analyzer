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
  (scale / cut / edit / launch). Exception: `analyze_campaign_performance.py`
  (v2) uses its own 3-section structure instead — What was done / What
  happened with performance / Next steps — see the Scripts entry below and
  its module docstring

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
  ported 2026-09-21 from a separate branch's independently live-verified
  findings, not re-verified against this account's real data directly in
  that session (the Google Ads MCP Connector wasn't available). Confirmed
  by regex-level testing only (each pattern checked directly against its
  claimed collision/target strings, including confirming why two of the
  source branch's patterns - bare boundary-anchored "us"/"uk" - were
  deliberately NOT ported: both still false-positive on real Polish words
  with a diacritic immediately adjacent, e.g. "us" inside "usługa"). Run
  a real search-term pull through `analyze_topic_alignment.py` and check
  its unclassified/ambiguous output before treating these as fully
  verified on this account, the same way the original topic_keywords
  patterns were checked
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
- **Added/Excluded search-term status (`term_status`) is implemented but
  NOT YET LIVE-VERIFIED** — built 2026-09-20 against `search_term_view`'s
  documented `status` field (ADDED/EXCLUDED/ADDED_EXCLUDED/NONE per the
  Google Ads API), but the Google Ads MCP Connector wasn't available in
  that session to confirm it's actually selectable via
  `metadata_get_resource_metadata`, or that `campaign_search_term_view`
  (the PMax path) carries anything equivalent - **confirm this before
  relying on it in a real run**. `ads_common.normalize_term_status()`
  is deliberately tolerant of exact wording (substring match on "add"/
  "exclud") for this reason. Until verified and wired into a pull,
  `term_status` is simply absent from input CSVs and every dependent
  script falls back to its prior (pre-term_status) behavior exactly -
  this is safe to leave unverified for a while, not a blocker.
  Handling differs by script/signal, not a blanket filter:
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
- **`analyze_campaign_performance.py` v2 architecture (2026-09-21)**: the
  script no longer renders a markdown narrative itself - it emits one
  structured JSON file (`what_was_done` / `performance` / `next_steps`,
  mirroring the report's 3-section structure) plus the same
  `-cohorts.csv`/`-campaigns.csv` backups as before. The deterministic,
  reproducible math (quadrant classification, before/after deltas,
  account/cohort/campaign rollups) stays in Python; the narrative prose
  itself is composed by Claude Code reading that JSON during the
  `/performance-report` session, not generated inside the script via an
  embedded LLM call. `performance.account` is a new addition (v1 only had
  cohort/campaign levels) and is always a true account total, including
  `exclude_from_scoring` clusters - unlike the scored cohort/campaign peer
  groups, which exclude them. Two real bugs found and fixed 2026-09-21 in
  a parallel live-verification session (details reported back, applied
  here since that session's own uncommitted edits lived only in its local
  worktree and were never pushed): (1) `campaign.start_date_time` comes
  back as a full datetime ("2025-06-26 14:12:39"), not a bare date -
  `parse_iso_date()` now takes the date part before parsing, where it
  used to reject every real value and silently push every campaign into
  `campaign_age_status()`'s "unknown" bucket; (2) see "Change-log schema"
  below for the `change_event` wiring. STILL NOT LIVE-VERIFIED end-to-end
  from *this* session: exercised only against hand-built synthetic CSVs
  (all three benchmark modes, single-campaign-cohort fallback, the fixed
  `--changes`/correlation-flags path with the confirmed schema) - the
  parallel session did report a live PASS on the pre-schema-fix version
  (all 3 benchmark modes, account rollup, CAC/quadrant/causal-note/tCPA
  math, hand-verified against a real pull), but the change_event wiring
  and the start_date_time fix specifically have only been synthetic-
  tested here, not run against a live pull yet - confirm both once MCP
  access lines up with a fresh run of this exact code.
- **Change-log schema (`change_event`, confirmed 2026-09-21)**: confirmed
  live via `metadata_get_resource_metadata` + real pulls in a parallel
  verification session (this session's own Google Ads MCP Connector was
  unavailable throughout, consistent with this project's one-session-at-
  a-time MCP auth). Retrospective, but hard-capped to a **rolling ~29-day
  window from query time** (a start date exactly 30 days back is
  rejected: "requested start date is too old") - independent of whatever
  `--period-start`/`--period-end` a report passes, so a change-log pull
  is only possible when the analysis period overlaps roughly the
  trailing month. Fields: `change_date_time`, `change_resource_type`,
  `resource_change_operation`, `campaign`, `ad_group`, `changed_fields`,
  `old_resource`, `new_resource`, `user_email`, `client_type`,
  `resource_name`. Sortable only on `change_date_time`/
  `change_resource_type`/`resource_change_operation`/`user_email` - not
  `resource_name`, same pagination gotcha as `search_term_view`.
  Granularity: campaign-scoped changes carry a non-empty `campaign`
  field; ad-group/ad/keyword-scoped changes carry only `ad_group` (no
  campaign field) - there's no dedicated keyword/criterion field, so a
  specific keyword change is only identifiable via `changed_fields`/
  `old_resource`/`new_resource` on an `AD_GROUP_CRITERION`-typed event.
  `analyze_campaign_performance.py`'s `build_correlation_flags()` now
  does real matching (previously a stub) - `load_change_events()` reads
  this confirmed schema directly (previously a generic column guess) and
  `changes_in_window()` filters to `[--comparison-start, --period-end]`
  before matching. **Real, documented limitation, not an oversight**:
  correlation only covers campaign-scoped changes - ad-group/ad/keyword-
  scoped changes still appear in `what_was_done`'s raw change list but
  aren't matched to any cluster/campaign, since this script has no
  ad_group → campaign mapping wired in (a different join than the
  search-term scripts already do, for a different reason). `--changes`
  is still how the script receives change data (it has no MCP access
  itself, same as every other script here) - `/performance-report`'s job
  is to do the live pull and hand it a CSV in the confirmed shape; that
  live-pull wiring in the command itself is documented but not yet run
  end-to-end (see the v2-architecture bullet above).

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
- `analyze_campaign_performance.py` — campaign performance report (v2,
  Ads-only, structured-findings architecture - see Known technical notes):
  account-level, cohort (destination cluster), and nested campaign-level
  before/after rollups; CAC × volume quadrant classification against a
  switchable benchmark (cluster/account/target); a period-proportional
  insufficient-data floor that splits low-volume campaigns into
  new/stagnant/unknown-age; a causal-note layer (conversion rate vs.
  account average + Lost IS) explaining *why* alongside each quadrant
  label; a per-campaign tCPA candidacy recommendation; and scale/cut/edit/
  top-by-metric next-step rankings. Emits one structured JSON file (no
  markdown narrative - that's composed by Claude Code from the JSON, see
  `/performance-report`) plus `-cohorts.csv`/`-campaigns.csv` backups. An
  optional `--changes` CSV (confirmed `change_event` schema) populates
  the "what was done" section and drives real `correlation_flags`
  matching for campaign-scoped changes - see Known technical notes'
  "Change-log schema" for the field list and the ad-group-scope
  limitation
  (`--config configs/<project>.yaml --period file.csv --period-start YYYY-MM-DD --period-end YYYY-MM-DD --comparison file.csv --comparison-start YYYY-MM-DD --comparison-end YYYY-MM-DD [--benchmark cluster|account|target] [--target file.csv --target-start YYYY-MM-DD --target-end YYYY-MM-DD] [--changes file.csv] [--output file.json]`)
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
- `/performance-report` — runs the full campaign performance report
  pipeline end-to-end and composes the narrative from the script's
  structured JSON output. Takes the period to analyze and what to
  compare it against in **plain language** (e.g. "compare September to
  August", "this quarter vs the same quarter last year"), not raw
  `--period-start`/`--period-end` flags - the command parses that into
  real dates itself before invoking the script (see its own file for the
  exact resolution rules and defaults). See Planned section below re: a
  shared natural-language-to-dates helper across commands.

## Planned / known gaps
Deliberately deferred work, tracked here so it isn't lost or silently
reattempted from scratch:

- **Natural-language period parsing, currently per-command**:
  `/performance-report` parses phrases like "September vs August" into
  date-flag arguments itself, ad hoc, in its own command file. This same
  pattern would benefit `/analyze-waste` and `/find-opportunities` too
  (both currently take a window only via inline override text in the
  request, parsed less formally). Not yet extracted into a shared
  helper/convention - flagged 2026-09-21, not built. If a third command
  needs the same parsing, extract it then rather than duplicating a third
  time.
- **`/performance-report`'s "Next steps" section should eventually
  reference `/find-opportunities` signals** (new_demand, underexploited,
  rising_trend) — e.g. a cluster flagged for scaling that also has
  underexploited search-term headroom is a stronger signal than either
  alone. Deferred until `/find-opportunities`' output is trusted enough
  (its own signals are themselves partly gated on unverified term_status
  - see Known technical notes) to build on with confidence.
- **GA4 ecommerce item-level data cross-reference**: cross-referencing
  GA4 ecommerce items (tagged by direction — PL-UA, PL-UK, PL-PL, etc.)
  against search-demand potential, to strengthen `/performance-report`'s
  scaling conclusions with actual revenue/margin signal instead of Ads-
  only conversion count. Not started - no GA4 MCP access confirmed yet,
  no schema investigation done.
- **`change_event` live-pull wiring into `/performance-report` itself**:
  the resource's schema is confirmed and `analyze_campaign_performance.py`
  consumes it correctly (see Known technical notes' "Change-log schema"),
  but `/performance-report`'s step 4 (checking the ~29-day retention
  window, running the actual MCP pull, building the `--changes` CSV) is
  documented, not yet exercised end-to-end against a live account - do
  that before treating "What was done" as fully working, not just its
  schema as confirmed.
- **Ad-group/ad/keyword-scoped change correlation**: `correlation_flags`
  only matches campaign-scoped `change_event` rows (see "Change-log
  schema") - an ad-group→campaign mapping would extend real correlation
  coverage to ad copy edits and keyword-level changes, the way the
  search-term scripts already join ad_group→campaign for a different
  reason. Not built - these changes currently only surface as unmatched
  rows in `what_was_done`'s raw list, for Claude Code to notice in prose.

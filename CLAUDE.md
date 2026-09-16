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
  rows than `limit`, per the pagination note above) before treating a
  PMax total as complete.
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
- `topic_keywords` (content-based, matched against search-term text) is
  a distinct axis from `clusters` (campaign-name-based). Give a topic
  the SAME `name` as its corresponding `clusters` entry when one exists
  ("core" topics — PL-UA/US/UK/DE/krajowe for meest-post-polska) so
  `ads_common.classify_topic()`'s output is directly comparable to
  `match_cluster()`'s. Topics with no corresponding cluster ("tracked"
  topics — known destinations the account doesn't run a dedicated
  campaign/cluster for yet) use a fresh name and are reported
  separately (`tracked_no_campaign` outcome), never as a re-home/negative
  suggestion. Unlike `intent_keywords`' plain substring match,
  `topic_keywords` patterns are **regex** (`re.search`, same convention
  as `clusters`' `match_campaign_name`) — despite sharing the `match:`
  key name with `intent_keywords` for config-authoring consistency, they
  go through `matches_any_keyword()`, not the substring path, because
  geography patterns need real regex features (inflection wildcards like
  `\w*`, word-boundary lookarounds).
- `classify_topic()` outcome taxonomy: `single` (exactly one topic
  matched, cluster+campaign already exists — comparable to
  `match_cluster`'s result), `tracked_no_campaign` (exactly one topic
  matched, no cluster covers it), `ambiguous` (2+ conflicting topic
  matches — deliberately its own bucket, not first-match-wins like
  `match_cluster` and not multi-label like `match_intents`), `none` (no
  topic keyword matched at all). A carrier/brand-based inference layer
  (mapping carrier mentions like InPost/DHL/Meest Post to a topic or to
  two further outcomes — `irrelevant_import_direction` for
  service-directions this account doesn't serve, e.g. USPS inbound-only;
  `carrier_direction_unspecified` for internationally-ambiguous carriers
  with no stated destination, e.g. FedEx/UPS — plus an orthogonal
  `marketplace` tag for seller/business-model markers like Amazon/Etsy)
  is a planned but **not yet implemented** phase 2, gated on reviewing
  real unclassified-term data first rather than authoring speculative
  carrier patterns.
- Geography keyword patterns need real per-language verification, not
  just Polish: this account has substantial Russian-language query
  volume in addition to Polish/Ukrainian/English, and Russian and
  Ukrainian spellings of the same country can differ by a single
  Cyrillic letter (e.g. Ukraine: Ukrainian "україн" vs. Russian
  "украин" — missing the Russian form left ~150 real high-volume terms
  unclassified in one verification pass). Also watch for a short
  geography root colliding with an unrelated, common domain word in the
  *same* language — found live: bare `dani[ae]` (Denmark) matched inside
  "nadanie"/"podanie"/"oddanie" (Polish shipping-domain words for
  "dispatch"), and bare `czech` (Czech Republic) matched inside
  "niemczech" (Polish locative "in Germany", from a *different* topic's
  own pattern) — both fixed with `\b`/lookbehind anchoring. Verify new
  geography patterns against a real pull's actual search-term text
  before trusting them, the same way `clusters`' patterns were verified
  against real campaign names.

## Scripts
- `analyze_wasted_spend.py` — search-term waste analysis
  (`--config configs/<project>.yaml --input file.csv [--min-clicks N] [--min-cost N] [--output file.csv]`)
- `analyze_search_opportunities.py` — search-term opportunity/pattern
  discovery: new-demand, intent-pattern, underexploited high-performer,
  and rising-trend signals
  (`--config configs/<project>.yaml --trailing file.csv --baseline file.csv --keywords file.csv [--output file.csv]`)
- `analyze_topic_alignment.py` — content-derived topic vs. actual
  campaign mismatch, cross-campaign duplication, and tracked-but-
  unserved-country reporting (all three from the same merged
  search-term CSV used by the scripts above — no new API pull)
  (`--config configs/<project>.yaml --input file.csv [--campaigns file.csv] [--mismatch-output file.csv] [--duplication-output file.csv] [--tracked-output file.csv] [--unclassified-output file.csv]`)
- `ads_common.py` — shared helpers (header-row detection, numeric
  cleanup, cluster/brand/intent/topic matching) used by the scripts above
- (additional analysis scripts to be added here as they're built)

## Slash commands
- `/analyze-waste` — runs the full waste-analysis pipeline end-to-end
  using the active config's defaults, with inline overrides supported
- `/find-opportunities` — runs the full opportunity-discovery pipeline
  end-to-end using the active config's defaults, with inline overrides
  supported

"""Shared helpers for the Google Ads analysis scripts.

Centralizes the CLAUDE.md "known technical notes" that both scripts need:
header-row detection, numeric cleanup, and cluster/brand/intent matching
against a project config.
"""
import csv
import re
import sys
from pathlib import Path

import yaml

PLACEHOLDER = "--"

# Column labels used to locate the real header row in a Google Ads export,
# which may be preceded by a report-title / date-range row.
HEADER_HINTS = {
    "search term", "campaign", "campaign name", "ad group", "ad group name",
    "clicks", "cost", "conversions", "impressions", "keyword",
    "keyword text", "avg. cpc", "ctr", "status", "campaign status",
    "channel type", "conv. value", "start date", "campaign start date",
    "added/excluded",
}

# Candidate header labels per logical field, matched case-insensitively.
# Google Ads export column text varies slightly by report/locale/currency.
COLUMN_CANDIDATES = {
    "search_term": ["search term", "search terms"],
    "campaign": ["campaign", "campaign name"],
    "ad_group": ["ad group", "ad group name"],
    "clicks": ["clicks"],
    "cost": ["cost", "cost (usd)", "cost (pln)", "cost (eur)"],
    "conversions": ["conversions", "conv.", "all conv."],
    "impressions": ["impressions", "impr."],
    "keyword": ["keyword", "keyword text"],
    "status": ["campaign status", "status"],
    "channel_type": ["channel type", "campaign type", "advertising channel type"],
    "conversions_value": ["conv. value", "all conv. value", "conversion value", "conversions value"],
    # Impression-share fields are written as fractions (0.102, not "10.2%")
    # by this toolkit's own MCP-pull CSVs, to avoid the ambiguity of a "%"
    # string that clean_number() would strip without rescaling. A raw
    # Google Ads UI export using "%" strings would need conversion first.
    "search_impression_share": ["search impr. share", "search impression share"],
    "lost_is_rank": ["search lost is (rank)", "search rank lost impression share", "lost is (rank)"],
    "lost_is_budget": ["search lost is (budget)", "search budget lost impression share", "lost is (budget)"],
    "start_date": ["campaign start date", "start date"],
    # Live-verified 2026-09-21 (see CLAUDE.md's Known technical notes):
    # search_term_view.status is the Search-path field; the PMax path has
    # no campaign_search_term_view.status (that field doesn't exist) - its
    # equivalent is segments.search_term_targeting_status, hence the
    # "search term targeting status" alias below. A distinct logical field
    # from "status" (campaign ENABLED/PAUSED/REMOVED) on purpose, so
    # resolving one never collides with the other even though "status" is
    # included here as a plausible raw header text too.
    "term_status": ["added/excluded", "search term status", "search term targeting status", "keyword status", "status"],
}


def load_config(config_path):
    path = Path(config_path)
    if not path.exists():
        sys.exit(f"Config not found: {config_path}")
    with path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not config:
        sys.exit(f"Config is empty or invalid: {config_path}")
    return config


def read_csv_rows(path):
    if not Path(path).exists():
        sys.exit(f"Input file not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def find_header_row(rows, max_scan=15):
    """Return the index of the real header row within the first rows.

    Google Ads exports often prepend a report title and date-range row
    before the actual column headers, so row 0 can't be assumed.
    """
    for i, row in enumerate(rows[:max_scan]):
        cells = {c.strip().lower() for c in row if c and c.strip()}
        if len(cells & HEADER_HINTS) >= 2:
            return i
    raise ValueError(f"Could not detect a header row in the first {max_scan} rows")


def load_table(path):
    """Load a Google Ads CSV export, skipping any rows before the real header."""
    raw_rows = read_csv_rows(path)
    header_idx = find_header_row(raw_rows)
    header = [h.strip() for h in raw_rows[header_idx]]
    records = []
    for row in raw_rows[header_idx + 1:]:
        if not any(cell.strip() for cell in row):
            continue  # blank separator/footer row
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        records.append(dict(zip(header, row)))
    return records


def resolve_columns(fieldnames, wanted):
    """Map logical field names to actual header text in this export."""
    lower_map = {fn.strip().lower(): fn for fn in fieldnames}
    resolved = {}
    for field in wanted:
        for candidate in COLUMN_CANDIDATES.get(field, [field]):
            if candidate in lower_map:
                resolved[field] = lower_map[candidate]
                break
    return resolved


_NUMERIC_KEEP = re.compile(r"[^\d.,\-]")


def _normalize_decimal_separator(text, decimal_style=None):
    """Resolve which of ',' or '.' is the decimal point vs. a thousands
    separator, since Google Ads exports vary by locale (e.g. Polish
    exports use ',' as the decimal separator: "25,00" is 25.00, not 2500).

    decimal_style, when given (from infer_decimal_style() - see there for
    why a single cell can't always resolve this on its own), is trusted
    outright instead of guessing from this one cell: it's the separator
    character already confirmed to be the decimal point for this cell's
    whole column, so the *other* character can only be a thousands
    separator anywhere in that column, including here.
    """
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")

    if last_comma != -1 and last_dot != -1:
        decimal_char = "," if last_comma > last_dot else "."
        thousands_char = "." if decimal_char == "," else ","
        text = text.replace(thousands_char, "")
        return text.replace(decimal_char, ".")

    sep_char = "," if last_comma != -1 else "." if last_dot != -1 else None
    if sep_char is None:
        return text

    if decimal_style is not None:
        return text.replace(sep_char, ".") if sep_char == decimal_style else text.replace(sep_char, "")

    last_idx = text.rfind(sep_char)
    frac_len = len(text) - last_idx - 1
    # A real thousands separator always groups digits in 3s, so only a
    # *3-digit* tail after a single lone separator is truly ambiguous with
    # grouping. Any other tail length (1-2 = cents, 4+ = the sub-cent/
    # fractional-conversion precision the Google Ads API returns, e.g.
    # 73.3006 conversions) is unambiguously a decimal point - treating
    # every 3+ digit tail as "thousands" (the old rule) silently mangled
    # any field with more than 2 decimal digits into a huge integer. This
    # per-cell guess is only reached when the caller has no column-wide
    # decimal_style to hand us (see infer_decimal_style()) - prefer that
    # whenever a whole column of raw values is available, since it can
    # actually resolve what a single cell fundamentally cannot.
    if frac_len != 3 and text.count(sep_char) == 1:
        return text.replace(sep_char, ".")
    # Otherwise (a 3-digit tail, or repeated separators) it's thousands grouping.
    return text.replace(sep_char, "")


def infer_decimal_style(raw_values):
    """Infer the decimal-separator convention for a whole column of raw
    numeric-looking strings, by scanning for any one value that's
    unambiguous on its own (see _normalize_decimal_separator: only a lone
    separator with an exact 3-digit tail is ambiguous). A real export's
    numeric formatting is consistent within one column, so a single
    unambiguous row resolves every ambiguous row alongside it - this is
    what makes the ambiguity fixable in code rather than left to a
    writer-side rounding convention nothing enforces.

    Returns '.' or ',' (the decimal separator character) once a signal is
    found, or None when the column has no unambiguous value anywhere (e.g.
    every value is a plain integer, or every value happens to land on an
    exact 3-digit tail) - callers should treat None as "fall back to
    clean_number()'s per-cell guess", which is the best available answer
    in that rare case.
    """
    for raw in raw_values:
        text = str(raw if raw is not None else "").strip()
        if text == "" or text == PLACEHOLDER:
            continue
        text = text.replace("%", "")
        text = _NUMERIC_KEEP.sub("", text)

        last_comma = text.rfind(",")
        last_dot = text.rfind(".")
        if last_comma != -1 and last_dot != -1:
            return "," if last_comma > last_dot else "."

        sep_char = "," if last_comma != -1 else "." if last_dot != -1 else None
        if sep_char is None or text.count(sep_char) != 1:
            continue  # no separator, or repeated (thousands-only) - no decimal signal in this cell
        frac_len = len(text) - text.rfind(sep_char) - 1
        if frac_len != 3:
            return sep_char
    return None


def resolve_decimal_styles(records, cols, fields):
    """infer_decimal_style() for several columns at once - {field:
    decimal_style_or_None}, for every field in `fields` that's present in
    `cols` (the output of resolve_columns()). Scans the whole column once
    per field, so callers should call this once per file, not per row."""
    return {
        field: infer_decimal_style(row.get(cols[field], "") for row in records)
        for field in fields if field in cols
    }


def clean_number(value, decimal_style=None):
    """Parse a Google Ads numeric cell.

    Treats the '--' placeholder (and blanks) as 0, and strips currency
    symbols and percent signs while resolving locale-specific decimal
    vs. thousands separators.

    decimal_style, when given, skips this cell's own (sometimes genuinely
    ambiguous) per-cell guess in favor of a convention already resolved
    for this cell's whole column - see infer_decimal_style().
    """
    if value is None:
        return 0.0
    text = str(value).strip()
    if text == "" or text == PLACEHOLDER:
        return 0.0
    text = text.replace("%", "")
    text = _NUMERIC_KEEP.sub("", text)
    text = _normalize_decimal_separator(text, decimal_style)
    if text in ("", "-", "."):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def is_missing(value):
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text == PLACEHOLDER


def normalize_term_status(raw):
    """Normalize a raw Added/Excluded cell into one of "added", "excluded",
    "added_excluded", "none".

    Tolerant of exact wording (substring match on "add"/"exclud") rather
    than an exact-value lookup, since the real export format hasn't been
    live-verified yet (see CLAUDE.md's Known technical notes) - this is
    deliberately permissive so whatever the actual column text turns out
    to be, common variants ("Added", "Excluded", "Added/Excluded") all
    resolve correctly without needing a code change once verified.
    """
    text = (raw or "").strip().lower()
    if not text or text == PLACEHOLDER:
        return "none"
    has_added = "add" in text
    has_excluded = "exclud" in text
    if has_added and has_excluded:
        return "added_excluded"
    if has_added:
        return "added"
    if has_excluded:
        return "excluded"
    return "none"


def match_cluster(campaign_name, clusters):
    """First-match-wins cluster assignment by campaign-name regex match,
    falling back to the config's catch-all cluster.

    Patterns are regex (re.search, case-insensitive), so plain-word patterns
    already in configs keep working unchanged (a plain word is a valid regex
    that matches itself as a substring), while patterns can now also use
    real regex features like \\b word boundaries and character classes.
    """
    name = campaign_name or ""
    catch_all = None
    for cluster in clusters:
        match = cluster.get("match_campaign_name")
        if match == "catch-all":
            catch_all = cluster["name"]
            continue
        patterns = match if isinstance(match, list) else [match]
        for pattern in patterns:
            if pattern and re.search(pattern, name, re.IGNORECASE):
                return cluster["name"]
    return catch_all or "unclustered"


def matches_any_keyword(text, keywords):
    text = text or ""
    return any(kw and re.search(kw, text, re.IGNORECASE) for kw in keywords)


def flattened_cluster_keywords(clusters):
    """All non-catch-all clusters' match_campaign_name patterns, flattened,
    for content-based (not campaign-based) matching against search terms."""
    keywords = []
    for cluster in clusters:
        match = cluster.get("match_campaign_name")
        if match == "catch-all":
            continue
        keywords.extend(match if isinstance(match, list) else [match])
    return [kw for kw in keywords if kw]


def match_topics(text, topic_keywords):
    """Content-based topic detection: which topics' patterns appear in this
    text (typically a search term), independent of which cluster the term's
    *campaign* belongs to. Unlike match_cluster, this is not first-match-
    wins - every topic is checked, so callers can detect ambiguous terms
    that match 2+ conflicting topics.

    Returns a list of topic names (possibly empty, single, or multiple).
    """
    text = text or ""
    matched = []
    for topic in topic_keywords or []:
        patterns = topic.get("match") or []
        if any(pattern and re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            matched.append(topic["name"])
    return matched


def campaigns_by_cluster(campaigns, clusters, active_statuses=("ENABLED",)):
    """Group live campaigns by their clusters[] assignment.

    campaigns: iterable of dicts with at least 'name' and 'status' keys
    (status values as returned by the Google Ads API, e.g. "ENABLED",
    "PAUSED", "REMOVED").

    Only campaigns whose status is in active_statuses are included, so a
    REMOVED (or, by default, PAUSED) campaign is never returned as a
    re-home target. Callers that specifically need dormant campaigns (e.g.
    to add an "evaluate/reactivate" caveat) should call this again with an
    explicit wider active_statuses tuple.
    """
    grouped = {}
    for campaign in campaigns:
        if campaign.get("status") not in active_statuses:
            continue
        cluster_name = match_cluster(campaign.get("name"), clusters)
        grouped.setdefault(cluster_name, []).append(campaign)
    return grouped


def campaigns_by_topic(campaigns, clusters, topic_keywords, active_statuses=("ENABLED",)):
    """Group live campaigns by topic, via each topic's linked cluster.

    Topics with no `cluster` field (the tracked countries) never appear
    here, since nothing in the account is classified as serving them yet -
    that's the whole point of tracking them.
    """
    by_cluster = campaigns_by_cluster(campaigns, clusters, active_statuses=active_statuses)
    grouped = {}
    for topic in topic_keywords or []:
        cluster_name = topic.get("cluster")
        if cluster_name and by_cluster.get(cluster_name):
            grouped[topic["name"]] = by_cluster[cluster_name]
    return grouped


def campaigns_matching_topic_name(campaigns, topic):
    """Campaigns whose *name* matches this topic's own content patterns,
    at any status - used for "tracked" topics (no `cluster` link, so
    campaigns_by_topic() never finds them) to discover prior/dormant
    campaigns for that country independent of live-campaign status.

    Callers filter the returned list by status themselves (e.g. to split
    into active vs. dormant) since this doesn't apply an active_statuses
    filter the way campaigns_by_cluster/campaigns_by_topic do.
    """
    patterns = topic.get("match") or []
    return [
        campaign for campaign in campaigns
        if any(p and re.search(p, campaign.get("name") or "", re.IGNORECASE) for p in patterns)
    ]


def classify_topic(text, topic_keywords, topic_campaigns):
    """Classify a search term's country/topic signal, independent of the
    campaign it's currently running in.

    topic_campaigns: result of campaigns_by_topic() - topic name -> list of
    live, active campaigns eligible as a re-home target.

    Returns (classification, matched_topics):
      - "none": no topic pattern matched the text
      - "single": exactly one topic matched, and it has a live active
        campaign to re-home to
      - "tracked_no_campaign": exactly one topic matched, but no active
        campaign exists for it (a tracked country, or a core topic whose
        cluster's campaigns are all currently paused/removed)
      - "ambiguous": 2+ distinct topics matched - conflicting signals
    """
    matched = match_topics(text, topic_keywords)
    if not matched:
        return "none", matched
    if len(matched) > 1:
        return "ambiguous", matched
    topic = matched[0]
    if topic_campaigns.get(topic):
        return "single", matched
    return "tracked_no_campaign", matched


def aggregate_campaign_metrics(records, cols):
    """Group campaign-metrics rows by campaign name.

    Additive metrics (impressions/clicks/cost/conversions/conversions_value)
    are summed. Impression-share metrics are ratios, not additive, so they're
    taken from whichever row has the highest cost - matches the "top_cost"
    attribution pattern already used for search-term aggregation. In
    practice each campaign is expected to appear as a single row (already
    aggregated over the query's date range by the Google Ads API), so this
    only matters as a defensive dedup. start_date (if the column is
    present) is constant per campaign, not a metric - the first non-empty
    value seen is kept as-is (a raw ISO-ish date string; parsing/validating
    it is the caller's job).
    """
    numeric_fields = ["impressions", "clicks", "cost", "conversions", "conversions_value",
                       "search_impression_share", "lost_is_rank", "lost_is_budget"]
    decimal_styles = resolve_decimal_styles(records, cols, numeric_fields)

    campaigns = {}
    for row in records:
        name = row.get(cols.get("campaign", ""), "").strip()
        if not name:
            continue
        cost = clean_number(row.get(cols.get("cost", ""), ""), decimal_styles.get("cost"))
        agg = campaigns.setdefault(name, {
            "campaign": name, "channel_type": "", "start_date": "",
            "impressions": 0.0, "clicks": 0.0, "cost": 0.0,
            "conversions": 0.0, "conversions_value": 0.0,
            "search_impression_share": None, "lost_is_rank": None, "lost_is_budget": None,
            "_top_cost": -1.0,
        })
        agg["impressions"] += clean_number(row.get(cols.get("impressions", ""), ""), decimal_styles.get("impressions"))
        agg["clicks"] += clean_number(row.get(cols.get("clicks", ""), ""), decimal_styles.get("clicks"))
        agg["cost"] += cost
        agg["conversions"] += clean_number(row.get(cols.get("conversions", ""), ""), decimal_styles.get("conversions"))
        if "conversions_value" in cols:
            agg["conversions_value"] += clean_number(row.get(cols["conversions_value"], ""), decimal_styles.get("conversions_value"))
        if not agg["start_date"] and "start_date" in cols:
            # Constant per campaign (not a metric to aggregate) - take the
            # first non-empty value seen, same as any other row would give.
            agg["start_date"] = row.get(cols["start_date"], "").strip()
        if cost >= agg["_top_cost"]:
            agg["_top_cost"] = cost
            channel_type = row.get(cols.get("channel_type", ""), "").strip()
            if channel_type:
                agg["channel_type"] = channel_type
            for field in ("search_impression_share", "lost_is_rank", "lost_is_budget"):
                if field in cols:
                    agg[field] = clean_number(row.get(cols[field], ""), decimal_styles.get(field))
    for agg in campaigns.values():
        agg.pop("_top_cost", None)
    return campaigns


def compute_cac(cost, conversions):
    """Cost per acquisition. None (not 0) when there are no conversions to
    divide by - a zero-conversion campaign's CAC is undefined, not free."""
    if conversions and conversions > 0:
        return cost / conversions
    return None


QUADRANT_ACTIONS = {
    "Star": "Scale - increase budget/bids, protect what's working",
    "Efficient (underexploited)": "Scale/Launch - efficient at current spend, has room to grow",
    "Review (high spend, high volume)": "Edit - meaningful volume but inefficient, needs optimization before scaling further",
    "Underperformer": "Cut or fundamentally edit - low volume and inefficient",
}


def classify_quadrant(cac, volume, target_cac, target_volume):
    """CAC x volume quadrant, relative to a (target_cac, target_volume)
    benchmark pair - resolving which benchmark (cluster/target/account mode)
    to use is the caller's job; this only compares against what it's given.

    Returns None when classification isn't possible (e.g. no conversions,
    or no benchmark available).
    """
    if cac is None or target_cac is None or target_volume is None:
        return None
    efficient = cac <= target_cac
    high_volume = volume >= target_volume
    if efficient and high_volume:
        return "Star"
    if efficient and not high_volume:
        return "Efficient (underexploited)"
    if not efficient and high_volume:
        return "Review (high spend, high volume)"
    return "Underperformer"


LOST_IS_FLAG_THRESHOLD = 0.10  # 10 percentage points - below this, treat as noise


def lost_is_diagnostic(lost_is_rank, lost_is_budget, threshold=LOST_IS_FLAG_THRESHOLD):
    """Separate diagnostic tag, never folded into the quadrant score - which
    Lost Impression Share driver(s), if any, are large enough to matter.
    Both tags are included when both clear the threshold (sorted, larger
    first), so the reader sees the primary lever (rank/quality vs. budget)
    without losing a secondary one."""
    tags = []
    if lost_is_rank is not None and lost_is_rank >= threshold:
        tags.append(("rank", lost_is_rank))
    if lost_is_budget is not None and lost_is_budget >= threshold:
        tags.append(("budget", lost_is_budget))
    if not tags:
        return None
    tags.sort(key=lambda t: -t[1])
    return "; ".join(f"Lost IS ({kind}): {pct * 100:.0f}%" for kind, pct in tags)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

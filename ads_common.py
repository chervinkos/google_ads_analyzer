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
    "keyword text", "avg. cpc", "ctr",
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


def _normalize_decimal_separator(text):
    """Resolve which of ',' or '.' is the decimal point vs. a thousands
    separator, since Google Ads exports vary by locale (e.g. Polish
    exports use ',' as the decimal separator: "25,00" is 25.00, not 2500)."""
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

    last_idx = text.rfind(sep_char)
    frac_len = len(text) - last_idx - 1
    # A real thousands grouping always leaves exactly 3 digits in the last
    # group (e.g. "1.234" = 1234). Any other length past the final separator
    # is a decimal fraction, not a thousands group - this matters for fields
    # like metrics.conversions, which can carry many fractional digits
    # (fractional attribution, e.g. "16.348877") well past the 1-2 digits
    # typical of currency amounts.
    if frac_len != 3 and text.count(sep_char) == 1:
        return text.replace(sep_char, ".")
    # Otherwise it's a thousands separator (possibly repeated) - drop it.
    return text.replace(sep_char, "")


def clean_number(value):
    """Parse a Google Ads numeric cell.

    Treats the '--' placeholder (and blanks) as 0, and strips currency
    symbols and percent signs while resolving locale-specific decimal
    vs. thousands separators.
    """
    if value is None:
        return 0.0
    text = str(value).strip()
    if text == "" or text == PLACEHOLDER:
        return 0.0
    text = text.replace("%", "")
    text = _NUMERIC_KEEP.sub("", text)
    text = _normalize_decimal_separator(text)
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


def match_intents(text, intent_keywords):
    """Independent axis from cluster matching: a term can match multiple
    intents (or none), regardless of its geographic cluster."""
    text_lower = (text or "").lower()
    matched = []
    for intent in intent_keywords or []:
        if any(kw.lower() in text_lower for kw in intent.get("match", [])):
            matched.append(intent["name"])
    return matched


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
    """Content-based topic classification, independent from campaign-name
    based match_cluster. Returns ALL topic names whose patterns match
    text (order of topic_keywords) - analogous to match_intents's shape,
    but classify_topic() applies an exclusive split instead of treating
    this as multi-label.

    Unlike match_intents' plain substring match, topic_keywords patterns
    are regex (re.search, case-insensitive) - same convention as
    match_cluster/matches_any_keyword - since geography patterns need
    real regex features (inflection wildcards like \\w*, word-boundary
    lookarounds)."""
    text = text or ""
    return [
        t["name"] for t in topic_keywords or []
        if matches_any_keyword(text, t.get("match", []))
    ]


def classify_topic(text, topic_keywords, core_topic_names):
    """Topic outcome per search term, deliberately NOT first-match-wins
    (unlike match_cluster) and NOT multi-label (unlike match_intents).

    core_topic_names: the set of topic names that already have a
    same-named entry in `clusters` (a real cluster+campaign exists) -
    distinguishes "single" (comparable to an existing cluster) from
    "tracked_no_campaign" (matched, but no cluster covers it).

    Returns (topic, outcome):
      "single"              -> topic is the one matched name, and it's
                                in core_topic_names
      "tracked_no_campaign" -> topic is the one matched name, and it's
                                NOT in core_topic_names
      "ambiguous"           -> topic is the matched names, sorted and
                                "; "-joined (2+ matches, regardless of
                                whether they're core or tracked)
      "none"                -> topic is None (no topic keyword matched -
                                a future carrier-inference layer would
                                try more rules before falling all the
                                way to this; not implemented yet)
    """
    matches = match_topics(text, topic_keywords)
    if len(matches) == 1:
        topic = matches[0]
        return topic, ("single" if topic in core_topic_names else "tracked_no_campaign")
    if not matches:
        return None, "none"
    return "; ".join(sorted(matches)), "ambiguous"


def campaigns_by_cluster(campaign_names, clusters):
    """Map each cluster name to the set of actual campaign names
    assigned to it via match_cluster, from the account's known campaign
    list. Powers the structural "does a campaign already exist for this
    core topic" check (re-home vs. negative-keyword suggestion)."""
    mapping = {}
    for name in campaign_names:
        cluster = match_cluster(name, clusters)
        mapping.setdefault(cluster, set()).add(name)
    return mapping


def campaigns_by_topic(campaign_names, topic_keywords):
    """Map each topic name to the set of actual campaign names whose
    NAME TEXT matches that topic's own patterns (via match_topics on the
    campaign name, not the cluster it was assigned to). This is
    independent of `clusters` entirely - it's what lets
    tracked_no_campaign correctly tell "no campaign exists for this
    country at all" apart from "a campaign exists but isn't clustered"
    (e.g. a Czech-Republic-named campaign that match_cluster would
    otherwise silently fold into the `international` catch-all)."""
    mapping = {}
    for name in campaign_names:
        for topic in match_topics(name, topic_keywords):
            mapping.setdefault(topic, set()).add(name)
    return mapping


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

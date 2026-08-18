#!/usr/bin/env python3
"""Flag wasted spend in a Google Ads Search Terms report.

A search term is flagged as a negative-keyword candidate when it has spent
money with zero conversions, and either its cost or its click count clears
a configurable threshold. Flagged terms are sorted by cost descending, with
a total wasted-spend figure at the bottom.

Expects a Search Terms report covering roughly a 90-day window. If the CSV
has no per-day date column, --date-range must be supplied so the report can
be labeled with the window it covers.
"""

import argparse
import csv
import re
import sys
from datetime import datetime

COLUMN_ALIASES = {
    "search_term": ["search term", "search terms"],
    "campaign": ["campaign"],
    "clicks": ["clicks"],
    "cost": ["cost", "cost usd", "cost gbp", "cost eur"],
    "conversions": ["conversions", "conv", "conversions "],
    "date": ["day", "date", "week"],
}

REQUIRED_FIELDS = ["search_term", "campaign", "clicks", "cost", "conversions"]

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y")


def normalize_header(name):
    return re.sub(r"[^a-z0-9]+", " ", name.strip().lower()).strip()


def find_column_map(header_row):
    normalized = {normalize_header(h): h for h in header_row}
    column_map = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                column_map[field] = normalized[alias]
                break
    return column_map


def find_header_row(raw_rows):
    for i, row in enumerate(raw_rows):
        column_map = find_column_map(row)
        if all(field in column_map for field in REQUIRED_FIELDS):
            return i, column_map
    return None, None


def parse_number(value):
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value):
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def load_terms(input_csv):
    """Read the CSV, locate the real header row, and return aggregated
    per-(search term, campaign) totals plus the parsed date range (if any).
    """
    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        raw_rows = list(csv.reader(f))

    header_idx, column_map = find_header_row(raw_rows)
    if header_idx is None:
        missing = ", ".join(REQUIRED_FIELDS)
        raise SystemExit(
            f"Could not find a header row containing all required columns "
            f"({missing}). Checked {len(raw_rows)} row(s) for a match."
        )

    has_date_column = "date" in column_map
    aggregated = {}
    dates_seen = []

    for row in raw_rows[header_idx + 1 :]:
        if not row or all(not cell.strip() for cell in row):
            continue

        record = dict(zip(raw_rows[header_idx], row))
        search_term = record.get(column_map["search_term"], "").strip()
        if not search_term or search_term.lower().startswith("total"):
            continue

        campaign = record.get(column_map["campaign"], "").strip()
        clicks = parse_number(record.get(column_map["clicks"]))
        cost = parse_number(record.get(column_map["cost"]))
        conversions = parse_number(record.get(column_map["conversions"]))
        if clicks is None or cost is None or conversions is None:
            continue

        if has_date_column:
            row_date = parse_date(record.get(column_map["date"]))
            if row_date is not None:
                dates_seen.append(row_date)

        key = (search_term, campaign)
        if key not in aggregated:
            aggregated[key] = {
                "search_term": search_term,
                "campaign": campaign,
                "clicks": 0,
                "cost": 0.0,
                "conversions": 0.0,
            }
        aggregated[key]["clicks"] += int(clicks)
        aggregated[key]["cost"] += cost
        aggregated[key]["conversions"] += conversions

    date_range = (min(dates_seen), max(dates_seen)) if dates_seen else None
    return list(aggregated.values()), has_date_column, date_range


def resolve_report_window(has_date_column, date_range, date_range_arg):
    if has_date_column and date_range is not None:
        start, end = date_range
        days = (end - start).days + 1
        return f"{start} to {end} ({days} days, from CSV date column)"

    if not date_range_arg:
        raise SystemExit(
            "The CSV has no usable date column, so --date-range is required "
            'to label the report, e.g. --date-range "2026-05-20:2026-08-18".'
        )

    try:
        start_str, end_str = date_range_arg.split(":")
        start = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
        end = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(
            f'Could not parse --date-range "{date_range_arg}". '
            'Expected format: "YYYY-MM-DD:YYYY-MM-DD".'
        )

    days = (end - start).days + 1
    return f"{start} to {end} ({days} days, via --date-range)"


def classify(term, min_clicks, min_cost):
    """Return 'cost', 'clicks', 'insufficient', or None (not waste)."""
    if term["conversions"] > 0:
        return None
    if term["cost"] <= 0:
        return None
    if term["cost"] >= min_cost:
        return "cost"
    if term["clicks"] < min_clicks:
        return "insufficient"
    return "clicks"


def format_currency(amount):
    return f"${amount:,.2f}"


def print_report(flagged, insufficient, window_label, min_clicks, min_cost):
    print(f"Report window: {window_label}")
    print(
        f"Wasted Spend Report (min clicks: {min_clicks}, "
        f"min cost: {format_currency(min_cost)} "
        f"[placeholder default - tune to your account's CPC/CPA])"
    )

    if not flagged:
        print("\nNo wasted spend found with the current thresholds.")
    else:
        term_w = max(11, max(len(r["search_term"]) for r in flagged))
        camp_w = max(8, max(len(r["campaign"]) for r in flagged))
        cost_w = max(4, max(len(format_currency(r["cost"])) for r in flagged))

        rule = "-" * (term_w + camp_w + cost_w + 24)
        print("=" * len(rule))
        print(
            f"{'Search Term':<{term_w}}  {'Campaign':<{camp_w}}  "
            f"{'Cost':>{cost_w}}  {'Clicks':>6}  Reason"
        )
        print(rule)
        for r in flagged:
            print(
                f"{r['search_term']:<{term_w}}  {r['campaign']:<{camp_w}}  "
                f"{format_currency(r['cost']):>{cost_w}}  {r['clicks']:>6}  "
                f"{r['reason']}"
            )
        print(rule)
        total = sum(r["cost"] for r in flagged)
        print(f"{len(flagged)} term(s) flagged | Total wasted spend: {format_currency(total)}")

    if insufficient:
        total_insufficient = sum(r["cost"] for r in insufficient)
        print(
            f"\nInsufficient data (excluded): {len(insufficient)} term(s), "
            f"{format_currency(total_insufficient)} combined cost"
        )


def write_output_csv(path, flagged):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Search Term", "Campaign", "Cost", "Clicks", "Conversions", "Reason"])
        for r in flagged:
            writer.writerow(
                [r["search_term"], r["campaign"], f"{r['cost']:.2f}", r["clicks"], r["conversions"], r["reason"]]
            )


def main():
    parser = argparse.ArgumentParser(
        description="Flag wasted spend (cost with zero conversions) in a Google Ads Search Terms report.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_csv", help="Path to the Search Terms report CSV (~90-day export).")
    parser.add_argument(
        "--min-clicks",
        type=int,
        default=3,
        help="Data-sufficiency floor and click-based flagging threshold. Zero-conversion "
        "terms below this click count are reported as insufficient data instead of flagged, "
        "unless --min-cost is cleared.",
    )
    parser.add_argument(
        "--min-cost",
        type=float,
        default=20.0,
        help="Cost-based flagging threshold. UNVALIDATED PLACEHOLDER - tune this to your "
        "account's actual average CPC/CPA before relying on it. A zero-conversion term at or "
        "above this cost is flagged regardless of click count, overriding --min-clicks.",
    )
    parser.add_argument(
        "--date-range",
        default=None,
        help='Report window as "YYYY-MM-DD:YYYY-MM-DD". Required only if the CSV has no '
        "per-day date column; used purely to label the report.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write flagged rows as a CSV (negative-keyword candidates).",
    )
    args = parser.parse_args()

    terms, has_date_column, date_range = load_terms(args.input_csv)
    window_label = resolve_report_window(has_date_column, date_range, args.date_range)

    flagged = []
    insufficient = []
    for term in terms:
        reason = classify(term, args.min_clicks, args.min_cost)
        if reason == "insufficient":
            insufficient.append(term)
        elif reason is not None:
            term["reason"] = reason
            flagged.append(term)

    flagged.sort(key=lambda r: r["cost"], reverse=True)
    insufficient.sort(key=lambda r: r["cost"], reverse=True)

    print_report(flagged, insufficient, window_label, args.min_clicks, args.min_cost)

    if args.output:
        write_output_csv(args.output, flagged)
        print(f"\nFlagged rows written to {args.output}")


if __name__ == "__main__":
    main()

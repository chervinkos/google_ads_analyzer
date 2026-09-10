# CLAUDE.md — Meest Google Ads Waste Analyzer

## Project purpose
This repo analyzes Google Ads search-term data to flag wasted spend
(high cost / low or zero conversions) and outputs actionable results.

## Account context
- Client account ID: 2732587797 (meestpost.com)
- Access via: Google Ads MCP Connector (already connected in claude.ai)
- MCC ID: 9372514694 (do not query directly — metrics only work on client accounts)

## Default parameters
- Baseline window: 90 days
- Trailing/flagging window: 30 days (minus ~7 days for conversion lag)
- --min-clicks: 2 (default, configurable via CLI flag)
- --min-cost: $20 (placeholder — not yet validated against real account
  average CPC/CPA; treat as tunable, not fixed)
- Cost-based flags override the click floor (a term can be flagged on
  cost alone even with fewer clicks than --min-clicks)

## Output conventions
- Always save flagged results to CSV (sorted by cost descending)
- Report "insufficient data" terms (below click floor, not cost-flagged)
  separately — never silently drop them
- Label every report with the actual date range analyzed

## Known technical notes
- Google Ads search-term exports may have extra header/title rows —
  detect the real header row, don't assume row 0
- Handle Google's "--" placeholder as 0, strip currency symbols/commas
- Search terms need joining from ad_group → campaign name; some ad
  groups may not appear in the initial batch pull and need a follow-up
  fetch

## Workflow
1. Pull campaign + search-term data for the client account via MCP
2. Map ad groups to campaign names
3. Build CSV in the format the analyzer expects
4. Run `analyze_wasted_spend.py` on it
5. Save flagged output as CSV, present it to the user

## Script reference
- Main analyzer: `analyze_wasted_spend.py`
- CLI shape: `python analyze_wasted_spend.py --input search_terms.csv --min-clicks 2 --min-cost 20 [--output flagged.csv]`

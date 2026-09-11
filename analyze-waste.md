Run the full Meest Google Ads waste analysis pipeline end-to-end:

1. Pull campaign and search-term data for client account 2732587797
   (meestpost.com) via the Google Ads MCP Connector, using the default
   90-day baseline window from CLAUDE.md (unless the user specifies a
   different window in their request).
2. Map ad groups to their campaign names (fetch any ad groups missing
   from the initial batch pull).
3. Build the CSV in the format analyze_wasted_spend.py expects.
4. Run analyze_wasted_spend.py using the default thresholds from
   CLAUDE.md (--min-clicks 2, --min-cost 20), unless the user specifies
   different values in their request.
5. Save the flagged results as a CSV, sorted by cost descending, and
   report insufficient-data terms separately (never silently dropped).
6. Present the output file to the user with a brief summary: total
   flagged terms, total wasted spend, and 3-5 standout line items
   worth a manual look.

If the user's request includes specific overrides (different date
range, thresholds, or campaign), apply those instead of the defaults.

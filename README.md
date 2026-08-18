# Claude-code-first-sandbox

## Google Ads Search Terms Waste Analyzer

`google_ads_waste_analyzer.py` flags search terms from a Google Ads Search Terms report that have spent money but earned zero conversions, and reports total wasted spend. See `python3 google_ads_waste_analyzer.py --help` for full usage.

```
python3 google_ads_waste_analyzer.py <input.csv> --min-cost <value> [--min-clicks N] [--date-range "YYYY-MM-DD:YYYY-MM-DD"] [--output flagged.csv]
```

### Choosing `--min-cost`

`--min-cost` is required with no default — there's no single agreed threshold in PPC practice for "how much zero-conversion spend is too much," so the flag forces you to pick a philosophy deliberately rather than trust an arbitrary number:

- **Aggressive** (~1/3 of your target CPA): catches waste earlier; accepts more false positives — may flag terms that would've converted given more time or data.
- **Conservative** (~2-3x your target CPA): lets terms accumulate more data before flagging; fewer false positives, but waste sits longer before being caught.

Example: a non-branded target CPA of 77.6 works out to roughly 25-30 aggressive vs. 150-230 conservative. These are illustrative reference points, not defaults — base yours on your own account's CPA.
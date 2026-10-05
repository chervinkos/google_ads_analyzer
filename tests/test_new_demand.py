"""Tests for analyze_search_opportunities.py's new_demand signal.

new_demand is content-based: it is True exactly when ads_common.classify_topic()
returns "tracked_no_campaign" (one topic matched, no ENABLED campaign serves
it). These tests exercise classify_topic()'s four labels directly, then run the
real script end to end to confirm new_demand follows topic coverage, not which
campaign happened to spend the most.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ads_common as common  # noqa: E402
import analyze_search_opportunities as opp  # noqa: E402

SCRIPT = ROOT / "analyze_search_opportunities.py"

# Hermetic synthetic config - deliberately NOT the live account's config.
# Campaign-name patterns (clusters) and term-content patterns (topics) are
# different on purpose, e.g. "deutschland" is a topic pattern no cluster knows.
CLUSTERS = [
    {"name": "brand", "match_campaign_name": ["brand"]},
    {"name": "PL-DE", "match_campaign_name": ["niem"]},
    {"name": "PL-UA", "match_campaign_name": ["ukrain"]},
    {"name": "international", "match_campaign_name": "catch-all"},
]
TOPICS = [
    {"name": "PL-DE", "cluster": "PL-DE", "match": ["niemc", "deutschland"]},  # core
    {"name": "PL-UA", "cluster": "PL-UA", "match": ["ukrain"]},                # core
    {"name": "PL-IT", "match": ["włoch"]},                                     # tracked
    {"name": "PL-CZ", "match": ["czech"]},                                     # tracked
]
LIVE = [
    {"name": "Search-Niemcy", "status": "ENABLED"},
    {"name": "Search-Ukraina", "status": "ENABLED"},
    {"name": "Search-International", "status": "ENABLED"},
]


def topic_campaigns(campaigns):
    return common.campaigns_by_topic(campaigns, CLUSTERS, TOPICS, active_statuses=("ENABLED",))


class ClassifyLabelsTest(unittest.TestCase):
    """classify_topic()'s four labels, and which of them count as new_demand."""

    def check(self, term, campaigns, expected_label, expected_new_demand):
        is_nd, label, _ = opp.classify_new_demand(term, TOPICS, topic_campaigns(campaigns))
        self.assertEqual(label, expected_label)
        self.assertEqual(is_nd, expected_new_demand)

    def test_single_is_not_new_demand(self):
        # Germany has a live campaign -> covered, even though this term's text
        # matches no *cluster* pattern (the old logic would have flagged it).
        self.check("meest deutschland", LIVE, "single", False)

    def test_tracked_no_campaign_is_new_demand(self):
        self.check("wysyłka do włoch", LIVE, "tracked_no_campaign", True)

    def test_ambiguous_is_not_new_demand(self):
        self.check("paczka niemcy ukraina", LIVE, "ambiguous", False)

    def test_none_is_not_new_demand(self):
        self.check("kurier tanio", LIVE, "none", False)

    def test_core_topic_with_only_paused_or_removed_campaigns_is_new_demand(self):
        dormant = [
            {"name": "Search-Niemcy", "status": "PAUSED"},
            {"name": "Old-Niemcy", "status": "REMOVED"},
            {"name": "Search-Ukraina", "status": "ENABLED"},
        ]
        self.check("meest deutschland", dormant, "tracked_no_campaign", True)

    def test_same_term_flips_when_an_enabled_campaign_appears(self):
        paused = [{"name": "Search-Niemcy", "status": "PAUSED"}]
        enabled = [{"name": "Search-Niemcy", "status": "ENABLED"}]
        self.check("meest deutschland", paused, "tracked_no_campaign", True)
        self.check("meest deutschland", enabled, "single", False)

    def test_empty_campaign_dict_mislabels_core_topics_and_is_caught(self):
        # The known trap: with {} a core topic comes back tracked_no_campaign.
        is_nd, label, _ = opp.classify_new_demand("meest deutschland", TOPICS, {})
        self.assertEqual((label, is_nd), ("tracked_no_campaign", True))
        # ...so the script warns instead of letting it pass silently.
        self.assertIn("core topic", opp.coverage_warning(TOPICS, {}, LIVE))

    def test_coverage_warning_cases(self):
        self.assertIsNone(opp.coverage_warning(TOPICS, topic_campaigns(LIVE), LIVE))
        paused_only = [{"name": "Search-Niemcy", "status": "PAUSED"}]
        self.assertIn("no ENABLED campaigns", opp.coverage_warning(TOPICS, topic_campaigns(paused_only), paused_only))

    def test_action_text_names_the_topic(self):
        self.assertIn("PL-IT", opp.new_demand_action(["PL-IT"]))


class EndToEndTest(unittest.TestCase):
    """Run the real script against synthetic CSVs."""

    TERM_HEADER = ["Search term", "Campaign", "Clicks", "Cost", "Conversions", "Search term status"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write_csv(self, name, header, rows):
        path = self.dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return path

    def run_script(self, term_rows, campaigns=LIVE, topics=TOPICS, with_campaigns=True):
        cfg = self.dir / "cfg.yaml"
        cfg.write_text(yaml.safe_dump({
            "project_name": "synthetic", "clusters": CLUSTERS, "topic_keywords": topics,
            "brand_keywords": [],
        }, allow_unicode=True), encoding="utf-8")
        terms = self.write_csv("terms.csv", self.TERM_HEADER, term_rows)
        kws = self.write_csv("kw.csv", ["Keyword", "Clicks"], [["unrelated", 1]])
        camps = self.write_csv("camps.csv", ["Campaign", "Status"], [[c["name"], c["status"]] for c in campaigns])
        out = self.dir / "out.csv"
        cmd = [sys.executable, str(SCRIPT), "--config", str(cfg), "--trailing", str(terms),
               "--keywords", str(kws), "--output", str(out)]
        if with_campaigns:
            cmd += ["--campaigns", str(camps)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                              env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
        signals = {}
        if out.exists():
            with open(out, newline="", encoding="utf-8") as f:
                signals = {r["search_term"]: r["signals"] for r in csv.DictReader(f)}
        return proc, signals

    def test_active_topic_in_wrong_campaign_is_not_new_demand(self):
        # Old logic: top-cost campaign is catch-all + text matches no cluster
        # pattern -> flagged. New logic: Germany is covered -> not flagged.
        _, sig = self.run_script([["meest deutschland", "Search-International", 10, "20.00", 0, ""]])
        self.assertNotIn("meest deutschland", sig)

    def test_tracked_topic_flagged_even_if_top_cost_campaign_is_not_catch_all(self):
        # Old logic: top-cost campaign is PL-UA (not catch-all) -> not flagged.
        _, sig = self.run_script([["wysyłka do włoch", "Search-Ukraina", 10, "50.00", 0, ""]])
        self.assertEqual(sig.get("wysyłka do włoch"), "new_demand")

    def test_result_is_independent_of_which_campaign_has_top_cost(self):
        a = [["wysyłka do włoch", "Search-Ukraina", 10, "80.00", 0, ""],
             ["wysyłka do włoch", "Search-International", 5, "20.00", 0, ""]]
        b = [["wysyłka do włoch", "Search-Ukraina", 10, "20.00", 0, ""],
             ["wysyłka do włoch", "Search-International", 5, "80.00", 0, ""]]
        _, sig_a = self.run_script(a)
        _, sig_b = self.run_script(b)
        self.assertEqual(sig_a, sig_b)
        self.assertEqual(sig_a.get("wysyłka do włoch"), "new_demand")

    def test_ambiguous_and_unclassified_terms_are_not_flagged(self):
        _, sig = self.run_script([
            ["paczka niemcy ukraina", "Search-International", 5, "10.00", 0, ""],
            ["kurier tanio", "Search-International", 5, "10.00", 0, ""],
        ])
        self.assertEqual(sig, {})

    def test_paused_only_core_topic_is_flagged_and_enabled_is_not(self):
        row = [["meest deutschland", "Search-International", 5, "10.00", 0, ""]]
        paused = [{"name": "Search-Niemcy", "status": "PAUSED"}, {"name": "Search-Ukraina", "status": "ENABLED"}]
        _, sig_paused = self.run_script(row, campaigns=paused)
        _, sig_live = self.run_script(row, campaigns=LIVE)
        self.assertEqual(sig_paused.get("meest deutschland"), "new_demand")
        self.assertNotIn("meest deutschland", sig_live)

    def test_underexploited_is_untouched_by_the_topic_logic(self):
        converters = [
            ["expensive converter", "Search-International", 10, "200.00", 10, ""],  # lifts the average CPA
            ["winner tanio", "Search-International", 20, "10.00", 5, ""],            # no topic
            ["winner ukraina", "Search-Ukraina", 20, "10.00", 5, ""],                # covered topic
            ["added winner", "Search-International", 20, "10.00", 5, "Added"],       # already in account
        ]
        _, sig = self.run_script(converters)
        self.assertEqual(sig.get("winner tanio"), "underexploited")
        self.assertEqual(sig.get("winner ukraina"), "underexploited")
        self.assertNotIn("added winner", sig)
        self.assertNotIn("new_demand", " ".join(sig.values()))

    def test_campaigns_argument_is_required(self):
        proc, _ = self.run_script([["wysyłka do włoch", "Search-Ukraina", 10, "50.00", 0, ""]], with_campaigns=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--campaigns", proc.stderr)

    def test_missing_topic_keywords_is_a_hard_error(self):
        proc, _ = self.run_script([["wysyłka do włoch", "Search-Ukraina", 10, "50.00", 0, ""]], topics=[])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("topic_keywords", proc.stderr)

    def test_stale_empty_campaign_list_warns_loudly(self):
        paused_only = [{"name": "Search-Niemcy", "status": "PAUSED"}]
        proc, _ = self.run_script([["meest deutschland", "Search-International", 5, "10.00", 0, ""]],
                                  campaigns=paused_only)
        self.assertIn("WARNING", proc.stderr)


if __name__ == "__main__":
    unittest.main()

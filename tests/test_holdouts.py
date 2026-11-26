import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from oasb.api import Error
from oasb.holdouts import split_holdouts, score_holdouts
from oasb.store import Run


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dev = Run(Path(tmp.name) / "builder")
        self.rev = Run(Path(tmp.name) / "reviewer")
        for run in (self.dev, self.rev):
            run.init("Question", ["Criterion"], "standard")
        ids = ["W" + str(i) for i in range(1, 9)]
        batch = self.rev.snapshot("discovery", {"route": "neighbors", "rows": [{"id": wid} for wid in ids]})
        self.rev.put("records", {wid: {"role": "relevant", "work": {"id": wid}} for wid in ids})
        self.rev.put("screening", {wid: {"work_id": wid, "discovery": batch, "decision": "include"} for wid in ids})
        self.dev.snapshot("discovery", {"route": "sample", "rows": [{"id": "W1"}, {"id": "W2"}]})

    def test_split_and_score_without_leaking(self):
        result = split_holdouts(self.rev, self.dev.root)
        sealed = self.rev.get("blind-holdouts")
        held = set(sealed["validation_ids"])
        self.assertEqual(len(held), 3)
        self.assertEqual(len(result["development_ids"]), 3)
        self.assertFalse(held & set(result["development_ids"]))
        self.assertFalse(held & {"W1", "W2"})
        self.assertNotIn("validation_ids", result)
        self.assertFalse((self.dev.root / "blind-holdouts.json").exists())
        with self.assertRaises(Error):
            split_holdouts(self.rev, self.dev.root)
        seen, hit, miss = sorted(held)
        self.dev.snapshot("discovery", {"route": "sample", "rows": [{"id": seen}]})
        query = {"get_rows": "works"}
        report = self.dev.root / "reports" / "test-report"
        self.dev.put("reports/test-report/final-query", query)
        self.dev.put("reports/test-report/validation", {
            "evaluation_id": "test-report", "scope": {"protocol": self.dev.get("protocol")},
            "records": {}, "evaluation": {"at": sealed["allocated_at"],
                "final": {"oqo": query, "count": 20}, "pulled": {"include_pulled": False}}})
        api = Mock()
        api.resolve.side_effect = lambda wid: (wid, {"id": wid})
        api.members.return_value = {hit}
        score = score_holdouts(self.rev, api, report)
        self.assertEqual(score["contaminated_excluded"], 1)
        self.assertEqual(score["relative_recall"], 0.5)
        api.members.assert_called_once_with(query, {hit, miss}, include_pulled=False)
        self.assertNotIn("hits", score)
        self.assertFalse((self.dev.root / "blind-score.json").exists())
        with self.assertRaises(Error):
            score_holdouts(self.rev, api, report)

    def test_reject_same_or_nested_workspaces(self):
        with self.assertRaises(Error):
            split_holdouts(self.rev, self.rev.root)
        nested = Run(self.rev.root / "nested")
        nested.init("Question", ["Criterion"], "standard")
        with self.assertRaises(Error):
            split_holdouts(self.rev, nested.root)


if __name__ == "__main__":
    unittest.main()

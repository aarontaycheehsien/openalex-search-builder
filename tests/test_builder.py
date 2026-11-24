import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))
import bench
from oasb import cli
from oasb.api import API, Error, identifier, normalize_query
from oasb.store import Run, write
from oasb.strategy import check


class Index:
    """Two tiny concepts with one matching paper each and one shared paper."""
    credits = 0
    cost_usd = 0.0
    unpriced = 0

    def validate(self, q):
        return {"oqo": {"get_rows": "works", "filter_rows": [{"value": q}]}, "oql": q,
                "validation": {"valid": True, "warnings": []}}

    def run(self, q, size=1, fields="id"):
        if isinstance(q, dict):
            return {"oqo": q, "oql": "pilot", "rows": [{"id": f"W{i}", "display_name": f"title {i}"} for i in (1, 2, 3)], "count": 3}
        oq = self.validate(q)["oqo"]
        return {"oqo": oq, "oql": q, "rows": [], "count": len(self.members(oq, ["W1", "W2", "W3"]))}

    def members(self, obj, ids):
        q = obj["filter_rows"][0]["value"]
        hits = set(ids)
        if "telemedicine" in q:
            hits &= {"W1", "W2"}
        if "rural" in q:
            hits &= {"W1", "W3"}
        if "hospital" in q:
            hits &= {"W2"}
        return hits

    def resolve(self, raw):
        key = identifier(raw)
        if key == "pmid:404":
            return key, None
        wid = "W1" if key.startswith(("doi:", "pmid:")) else key
        return key, {"id": wid, "display_name": "Rural healthcare " + wid}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Run(Path(tmp.name) / "review")
        self.api = Index()
        self.call("init", "--question", "Rural remote care?")
        self.strategy = json.loads((ROOT / "examples/telehealth.json").read_text())
        self.run.put("strategy", self.strategy)
        self.call("scope", "--confirm", "--note", "fixture approval")
        for key, role in [("W1", "seed"), ("W2", "seed"), ("W3", "validation"), ("404", "benchmark")]:
            self.call("resolve", key, "--role", role, "--source", "fixture")

    def call(self, *args):
        a = cli.parser().parse_args(["-w", str(self.run.root), *args])
        return cli.dispatch(self.run, self.api, a)

    def attach_critique(self):
        file = self.run.root / "critique.json"
        write(file, dict.fromkeys(("scope", "vocabulary", "boolean_and_fields", "limits", "retrieval", "limitations"), "Reviewed synthetic test case.") | {"decision": "ready"})
        return self.call("review", str(file))

    def test_diagnoses_revision_and_revalidation(self):
        data = self.call("eval")
        self.assertEqual(data["misses"], {"W2": ["block/setting"]})
        self.assertEqual(data["sets"]["seed"]["relative_recall"], 0.5)
        self.assertNotIn("validation", data["sets"])
        self.assertEqual(data["lines"]["without/setting"]["count"], 2)
        self.attach_critique()
        deliver = self.call("report")
        folder = Path(deliver["report"])
        archive = json.loads((folder / "validation.json").read_text())
        final = archive["evaluation"]["final"]
        self.assertEqual((folder / "final-query.oql").read_text().strip(), final["oql"])
        self.assertEqual(json.loads((folder / "final-query.json").read_text()), final["oqo"])
        self.assertEqual(archive["evaluation"]["sets"]["benchmark"]["relative_recall"], None)
        self.assertIn("pmid:404", (folder / "audit.md").read_text())
        self.strategy["blocks"][1]["role"] = "optional"
        self.run.put("strategy", self.strategy)
        with self.assertRaises(Error):
            self.call("report")
        self.call("scope", "--confirm", "--note", "Revise setting")
        revised = self.call("eval")
        self.assertEqual(revised["change"]["gained"], ["W2"])
        self.assertEqual(revised["lines"]["with/setting"]["development_hits"], ["W1"])

    def test_live_drift_requires_another_review(self):
        self.call("eval")
        self.attach_critique()
        with patch.object(self.api, "members", return_value=set()):
            with self.assertRaisesRegex(Error, "changed"):
                self.call("report")
        self.assertFalse((self.run.root / "reports").exists())

    def test_protected_works_stay_out_of_mining_and_screening(self):
        self.assertEqual({r["id"] for r in self.call("terms")["records"]}, {"W1", "W2"})
        d = self.call("sample", "--oql", "get works")
        self.assertEqual({r["id"] for r in d["candidates"]}, {"W1", "W2"})
        with self.assertRaises(Error):
            self.call("screen", d["discovery"], "W3", "include", "--reason", "held out")
        with self.assertRaises(Error):
            self.call("neighbors", "W3", "--direction", "references")
        self.call("develop", "W3", "--reason", "Used this work for revision")
        self.assertIn("W3", {r["id"] for r in self.call("terms")["records"]})

    def test_aliases_and_unresolved_records(self):
        self.call("resolve", "10.1234/abc", "--role", "seed", "--source", "DOI")
        self.assertEqual(len(self.run.get("records")), 4)
        self.assertEqual(self.run.get("records")["W1"]["aliases"], ["W1", "doi:10.1234/abc"])
        with self.assertRaises(Error):
            self.call("resolve", "W1", "--role", "benchmark", "--source", "overlap")

    def test_failed_evaluation_cannot_publish(self):
        self.call("eval")
        self.attach_critique()
        with patch.object(self.api, "run", side_effect=Error("OpenAlex HTTP 429")):
            with self.assertRaises(Error):
                self.call("report")
        self.assertFalse((self.run.root / "reports").exists())

    def test_template_and_refused_unknowns(self):
        for limits in ({"year": 2000}, {"from_date": "2020-01-31", "to_date": "2000-01-31"}):
            with self.assertRaises(Error):
                check(dict(self.strategy, limits=limits))
        self.strategy["blocks"][0]["terms"] = ["rural OR unrelated"]
        with self.assertRaises(Error):
            check(self.strategy)


class ClientTests(unittest.TestCase):
    def test_canonical_boolean_order(self):
        a, b = {"column_id": "type", "value": "article"}, {"column_id": "language", "value": "en"}
        self.assertEqual(normalize_query({"filter_rows": [a, b]}), normalize_query({"filter_rows": [b, a]}))
        self.assertNotEqual(normalize_query({"sort_by": [a, b]}), normalize_query({"sort_by": [b, a]}))
        self.assertNotEqual(normalize_query({"join": "and", "filters": [a,b]}), normalize_query({"join": "or", "filters": [a,b]}))

    def test_identifiers(self):
        for raw, key in [("https://doi.org/10.1234/A(B)", "doi:10.1234/a(b)"),
                         ("https://pubmed.ncbi.nlm.nih.gov/1234/", "pmid:1234"),
                         ("https://openalex.org/w123", "W123")]:
            self.assertEqual(identifier(raw), key)
        with self.assertRaises(Error):
            identifier("unknown")

    def test_membership_batches_and_consistency(self):
        api = API()
        def reply(query, size):
            group = query["filter_rows"][-1]
            leaves = group.get("filters", [group])
            rows = [{"id": x["value"]} for x in leaves]
            return {"count": len(rows), "rows": rows}
        ids = {f"W{i}" for i in range(1, 62)}
        with patch.object(api, "run", side_effect=reply) as req:
            self.assertEqual(api.members({"get_rows": "works"}, ids), ids)
            self.assertEqual(req.call_count, 3)
        with patch.object(api, "run", return_value={"rows": [], "count": 1}):
            with self.assertRaisesRegex(Error, "intersection"):
                api.members({"get_rows": "works"}, ["W1"])

    def test_cost_limit_does_not_become_a_miss_or_retry(self):
        error = HTTPError("https://api.openalex.org", 429, "budget", {}, io.BytesIO(b'{"error":"not_enough_credits"}'))
        with patch("oasb.api.urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(Error, "429"):
                API().resolve("W1")
            self.assertEqual(request.call_count, 1)

    def test_post_for_long_execution_and_redacted_log(self):
        api = API()
        response = {"meta": {"count": 0, "x_query": {"oqo": {"get_rows": "works"}, "oql": "get works"}}, "results": []}
        with patch.object(api, "request", return_value=response) as req:
            api.run("x" * 7000)
            self.assertIn("body", req.call_args.kwargs)
        events = []
        res = MagicMock()
        res.__enter__.return_value = res
        res.read.return_value = b'{"ok":true}'
        res.headers = {}
        res.status = 200
        with patch.dict(os.environ, {"OPENALEX_API_KEY":"fake-test-key"}), patch("oasb.api.urlopen", return_value=res) as req:
            API(events.append).request("/query", body={"oql":"get works"})
            self.assertEqual(req.call_args.args[0].get_header("Authorization"), "Bearer fake-test-key")
        self.assertNotIn("fake-test-key", json.dumps(events))

    def test_benchmark_hides_key_and_has_repeatable_seeds(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in bench.CASES:
                case = json.loads((ROOT / f"evals/fixtures/{name}.json").read_text())
                for cond in ("seedless", "seeded"):
                    folder = Path(temp) / (name+cond)
                    bench.prepare(case, cond, folder)
                    given = json.loads((folder/"intake.json").read_text())
                    self.assertEqual(len(given["seeds"]), 3 if cond == "seeded" else 0)
                    self.assertNotIn("gold", given)
                    self.assertFalse((folder/"skill/evals").exists())
                    self.assertEqual(given["seeds"], bench.assignment(case, cond))


if __name__ == "__main__":
    unittest.main()

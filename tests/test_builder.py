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


import re

from oasb.api import leaves
from oasb.export import export
from oasb.strategy import final_query, parse_combine, failing

TERMS = {"telemedicine": {"W1", "W2"}, "rural": {"W1", "W3"}, "hospital": {"W2"}}
CLAUSE = re.compile(r'(not )?(?:full text|[\w-]+) has \(((?:[^()]|\([^()]*\))*)\)')
LIMIT = re.compile(r'publication_date [<>]=? \S+|(?:language|type) is \([^)]*\)')


class Index:
    """Evaluates compiled OQL over three works: clauses, NOT, AND/OR nesting and limits."""
    credits = 0
    cost_usd = 0.0
    unpriced = 0

    def validate(self, q):
        rows = []
        for m in CLAUSE.finditer(q):
            rows += [{"column_id": "x.search", "value": t, "operator": "has", **({"is_negated": True} if m[1] else {})}
                     for t in m[2].split(" OR ")]
        for m in LIMIT.finditer(q):
            values = re.findall(r"\(([^)]*)\)", m[0])
            rows += [{"column_id": "limit", "value": v} for v in (values[0].split(" or ") if values else [m[0]])]
        oqo = {"get_rows": "works", "filter_rows": rows}
        corpus = re.search(r"\((all corpora|expansion corpus)\)", q)
        if corpus:
            oqo["corpus"] = corpus[1].split()[0]
        self.source[json.dumps(oqo, sort_keys=True)] = q
        return {"oqo": oqo, "oql": q, "validation": {"valid": True, "warnings": []}}

    def __init__(self):
        self.source = {}

    def run(self, q, size=1, fields="id"):
        if isinstance(q, dict):
            return {"oqo": q, "oql": "pilot", "rows": [{"id": f"W{i}", "display_name": f"title {i}"} for i in (1, 2, 3)], "count": 3}
        oq = self.validate(q)["oqo"]
        return {"oqo": oq, "oql": q, "rows": [], "count": len(self.members(oq, ["W1", "W2", "W3"]))}

    def members(self, obj, ids):
        where = self.source[json.dumps(obj, sort_keys=True)].partition(" where ")[2]
        hits = set()
        for wid in ids:
            def clause(m):
                found = any(wid in TERMS.get(t, set()) for t in m[2].split(" OR "))
                return str(found != bool(m[1]))
            expr = LIMIT.sub("True", CLAUSE.sub(clause, where)) or "True"
            if eval(expr, {"__builtins__": {}}):
                hits.add(wid)
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

    def reconfirm(self, strategy):
        self.run.put("strategy", strategy)
        self.call("scope", "--confirm", "--note", "revised fixture")

    def test_exclude_and_combine_evaluation(self):
        s = dict(self.strategy, combine="delivery or setting")
        s["blocks"] = [*s["blocks"], {"id": "hosp", "role": "exclude", "field": "title", "terms": ["hospital"],
                                      "rationale": "Exclude inpatient settings"}]
        self.reconfirm(s)
        data = self.call("eval")
        self.assertIn(" or ", data["oql"])
        self.assertIn("not title has (hospital)", data["oql"])
        self.assertEqual(data["count"], 2)                         # W1, W3; W2 removed by the exclusion
        self.assertEqual(data["misses"], {"W2": ["exclude/hosp"]})
        self.assertEqual(data["lines"]["without/hosp"]["development_hits"], ["W1", "W2"])
        self.assertNotIn("without/delivery", data["lines"])
        self.assertIn("not assessed", data["ablation"])

    def test_combine_miss_explanation(self):
        tree = parse_combine("(a and b) or c", ["a", "b", "c"])
        self.assertEqual(failing(tree, {"a": True, "b": False, "c": False}), ["b", "c"])
        self.assertEqual(failing(tree, {"a": True, "b": True, "c": False}), [])
        for bad in ("a and not b or c", "a and (b or c", "a and b", "a and zz or b or c", "a b c"):
            with self.assertRaises(Error):
                parse_combine(bad, ["a", "b", "c"])

    def test_translation_guard_refuses_dropped_clause(self):
        real = self.api.validate
        def drop_last(q):
            out = real(q)
            out["oqo"]["filter_rows"] = out["oqo"]["filter_rows"][:-1]
            return out
        with patch.object(self.api, "validate", side_effect=drop_last):
            with self.assertRaisesRegex(Error, "kept"):
                self.call("eval")
            with self.assertRaisesRegex(Error, "kept"):
                self.call("validate")
        corpus_lost = lambda q: {**real(q), "oqo": {k: v for k, v in real(q)["oqo"].items() if k != "corpus"}}
        self.reconfirm(dict(self.strategy, corpus="all"))
        with patch.object(self.api, "validate", side_effect=corpus_lost):
            with self.assertRaisesRegex(Error, "corpus"):
                self.call("validate")

    def test_corpus_requires_scope_and_reaches_neighbors(self):
        self.run.put("strategy", dict(self.strategy, corpus="all"))
        with self.assertRaisesRegex(Error, "Confirm"):
            self.call("eval")
        self.call("scope", "--confirm", "--note", "Include expansion corpus")
        self.assertIn("(all corpora)", self.call("eval")["oql"])
        with patch.object(self.api, "validate", wraps=self.api.validate) as v:
            self.call("neighbors", "W1", "--direction", "citing")
            self.assertEqual(v.call_args.args[0], "get works (all corpora) citing [](W1)")

    def test_miss_details_are_development_only(self):
        data = self.call("eval")
        self.assertEqual(set(data["miss_details"]), {"W2"})
        self.assertFalse(data["miss_details"]["W2"]["has_abstract"])
        self.assertIn("No abstract", data["miss_details"]["W2"]["note"])
        self.assertEqual(data["miss_details"]["W2"]["failed"], ["block/setting"])

    def deliver(self):
        self.call("eval")
        self.attach_critique()
        return Path(self.call("report")["report"])

    def test_export_resumes_and_writes_formats(self):
        records = [{"id": f"https://openalex.org/W{i}", "display_name": t, "doi": f"https://doi.org/10.1/{i}",
                    "publication_year": 2020, "type": "article", "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{i}"},
                    "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                    "abstract_inverted_index": {"Remote": [0], "care": [1]}}
                   for i, t in [(1, "=HYPERLINK(evil)"), (2, "Second"), (3, "Third")]]
        pages = {"*": (records[:2], "c2"), "c2": (records[1:], None)}   # overlap tests de-duplication
        calls = []
        def page(oqo, cursor="*", size=100):
            calls.append(cursor)
            if cursor == "c2" and calls.count("c2") == 1:
                raise Error("OpenAlex HTTP 429: not_enough_credits")
            rows, nxt = pages[cursor]
            return {"count": 3, "rows": rows, "next_cursor": nxt}
        self.api.page = page
        report = self.deliver()
        first = self.call("export")
        self.assertFalse(first["complete"])
        self.assertIn("429", first["error"])
        folder = Path(first["directory"])
        self.assertTrue((folder / "results.partial.csv").exists())
        second = self.call("export")
        self.assertTrue(second["complete"])
        self.assertEqual(calls, ["*", "c2", "c2"])                # resumed from the saved cursor
        self.assertEqual(second["unique_records"], 3)
        self.assertTrue(second["count_discrepancy"])              # validated count was 1
        text = (folder / "results.csv").read_text()
        self.assertIn("'=HYPERLINK(evil)", text)
        self.assertIn("=HYPERLINK(evil)", (folder / "results.jsonl").read_text().splitlines()[0])
        ris = (folder / "results.ris").read_text()
        self.assertEqual(ris.count("ER  - "), 3)
        self.assertIn("AN  - PMID:1", ris)
        self.assertIn("AB  - Remote care", ris)
        self.assertEqual(json.loads((folder / "export-manifest.json").read_text())["report"], report.name)
        (report / "final-query.oql").write_text("get works\n")
        with self.assertRaisesRegex(Error, "differ"):
            self.call("export")

    def test_export_page_limit(self):
        self.api.page = lambda oqo, cursor="*", size=100: {"count": 2, "rows": [{"id": "W" + str(len(cursor))}],
                                                           "next_cursor": cursor + "x" if len(cursor) < 3 else None}
        self.deliver()
        out = self.call("export", "--max-pages", "1")
        self.assertFalse(out["complete"])
        self.assertIn("max-pages", out["error"])
        self.assertTrue(self.call("export")["complete"])


class StrategyTests(unittest.TestCase):
    def block(self, terms, role="search", field="title-abstract", id="a"):
        return {"id": id, "role": role, "field": field, "terms": terms, "rationale": "r"}

    def test_compiles_supported_forms(self):
        s = {"corpus": "all", "blocks": [
            self.block([{"near": ["rural", "clinic"], "within": 3}, {"near": ["remote care", "patient*"], "within": 5, "exact": True}]),
            self.block(["telemedicine"], field="fulltext", id="b"),
            self.block(["veterinary", "\"animal health\""], role="exclude", id="x")],
            "limits": {"language": ["en", "fr"]}}
        check(s)
        q, n = final_query(s)
        self.assertEqual(q, 'get works (all corpora) where title-abstract has ((rural and clinic within 3 words of each other) OR '
                            '("remote care" and "patient*" within 5 words of each other)) and full text has (telemedicine) and '
                            'not title-abstract has (veterinary OR "animal health") and language is (en or fr)')
        self.assertEqual(n, 7)
        self.assertEqual(final_query({"blocks": [self.block([{"near": ["a1x", "b2x", "c3x"], "within": 2}])]})[0],
                         "get works where title-abstract has ((a1x, b2x, and c3x within 2 words of each other))")

    def test_rejects_unsafe_forms(self):
        bad = [{"near": ["remote care", "x1"], "within": 3}, {"near": ["one"], "within": 3},
               {"near": ["ab*", "cd"], "within": 2, "exact": True}, {"near": ["a1", "b1"], "within": 0},
               {"near": ["\"a1\"", "b1"], "within": 2}, "\"climate change\"~3"]
        for term in bad:
            with self.assertRaises(Error, msg=term):
                check({"blocks": [self.block([term])]})
        for strategy in ({"blocks": [self.block(["a1"])], "corpus": "xpac"},
                         {"blocks": [self.block(["a1"], role="exclude")]},
                         {"blocks": [self.block(["a1"]), self.block(["b1"], id="b")], "combine": "a and not b"}):
            with self.assertRaises(Error):
                check(strategy)

    def test_leaf_counter(self):
        oqo = {"filter_rows": [{"join": "or", "filters": [{"column_id": "a"}, {"join": "and", "filters": [{"column_id": "b"}, {"column_id": "c"}]}]},
                               {"column_id": "d"}]}
        self.assertEqual(leaves(oqo["filter_rows"]), 4)


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

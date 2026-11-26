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
from oasb.api import API, Error, check_translation, identifier, leaves, normalize_query, normalize_warnings, tokens
from oasb.store import Run, digest, write
from oasb.strategy import check, lint
import re
from oasb.export import export
from oasb.strategy import assign, core, excluded, expected_tokens, final_query, parse_combine, failing, rescue_expectation, rescue_oql
from oasb.records import wilson

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
        self.include_pulled = False
        self.pulled_ids = set()      # works OpenAlex marks as pulled in this synthetic index

    def visible(self, include_pulled):
        return self.include_pulled if include_pulled is None else include_pulled

    def pulled(self, ids):
        return set(ids) & self.pulled_ids

    links = {}   # per-work overrides of matched-reference and citation counts
    reference_line_data = {}   # per-work printed reference lines (/works/{id}/references)
    lookup_index = {}          # work ID -> {"has_abstract": bool, "year": int} for ID lookups

    def reference_lines(self, wid):
        return self.reference_line_data.get(wid, [])

    def lookup(self, ids, filters=(), corpus="core", select="id"):
        out = []
        for wid in ids:
            w = self.lookup_index.get(wid)
            if w is None:
                continue
            if "has_abstract:false" in filters and w["has_abstract"]:
                continue
            if any(f.startswith("to_publication_date:") and w["year"] > int(f.split(":")[1][:4]) for f in filters):
                continue
            out.append({"id": f"https://openalex.org/{wid}", "display_name": f"Rescued {wid}"})
        return out

    def link_counts(self, wid):
        return self.links.get(wid, {"referenced_works_count": 5, "cited_by_count": 5})

    def run(self, q, size=1, fields="id", include_pulled=None):
        if isinstance(q, dict):
            return {"oqo": q, "oql": "pilot", "rows": [{"id": f"W{i}", "display_name": f"title {i}"} for i in (1, 2, 3)], "count": 3}
        oq = self.validate(q)["oqo"]
        return {"oqo": oq, "oql": q, "rows": [], "count": len(self.members(oq, ["W1", "W2", "W3"], include_pulled))}

    def page(self, oqo, cursor="*", size=100, select=None):
        rows = [{"id": f"W{i}", "display_name": f"title {i}"} for i in (1, 2, 3)][:size]
        return {"count": 3, "rows": rows, "next_cursor": None, "oqo": oqo, "oql": "neighbors"}

    def members(self, obj, ids, include_pulled=None):
        where = self.source[json.dumps(obj, sort_keys=True)].partition(" where ")[2]
        hits = set()
        for wid in ids:
            if wid in self.pulled_ids and not self.visible(include_pulled):
                continue
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


def search(value):
    return {"column_id": "title_and_abstract.search", "value": value, "operator": "has"}


def rescue_oqo(nested_corpus=None, terms=("telemedicine", "telehealth", "rural", "remote")):
    """Rescue query object for examples/telehealth.json, shaped like OpenAlex's live translation."""
    nested = {"get_rows": "works", "filter_rows": [
        {"join": "or", "filters": [search(t) for t in terms[:2]]},
        {"join": "or", "filters": [search(t) for t in terms[2:]]}]}
    if nested_corpus:
        nested["corpus"] = nested_corpus
    return {"get_rows": "works", "corpus": "core", "filter_rows": [
        {"column_id": "has_abstract", "value": False},
        {"join": "or", "filters": [search(t) for t in ("telemedicine", "telehealth", "rural", "remote")]},
        {"column_id": "cited_by", "operator": "in", "value": nested}]}


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

    def test_report_tolerates_warning_field_order_flip(self):
        real = self.api.validate
        w_eval = [{"type": "url_not_representable", "location": None,
                   "message": "OR across different fields cannot be expressed in URL format. Fields involved: a.search, a.search.exact"}]
        w_report = [{"type": "url_not_representable", "location": None,
                     "message": "OR across different fields cannot be expressed in URL format. Fields involved: a.search.exact, a.search"}]
        with patch.object(self.api, "validate", side_effect=lambda q: {**real(q), "validation": {"valid": True, "warnings": w_eval}}):
            self.call("eval")
        self.attach_critique()
        with patch.object(self.api, "validate", side_effect=lambda q: {**real(q), "validation": {"valid": True, "warnings": w_report}}):
            deliver = self.call("report")
        self.assertTrue((Path(deliver["report"]) / "validation.json").is_file())

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

    def test_screen_can_allocate_local_validation_directly(self):
        uid = self.run.snapshot("discovery", {"route": "neighbors", "rows": [{"id": "W50"}]})
        result = self.call("screen", uid, "W50", "include", "--role", "validation", "--reason", "Eligible")
        self.assertEqual(result["role"], "validation")
        self.assertEqual(self.run.get("records")["W50"]["role"], "validation")
        self.assertNotIn("W50", {r["id"] for r in self.call("terms")["records"]})
        with self.assertRaises(Error):
            self.call("screen", uid, "W50", "include", "--role", "relevant", "--reason", "Change role")
        with self.assertRaises(Error):
            self.call("screen", uid, "W50", "exclude", "--role", "validation", "--reason", "Invalid")

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
        self.assertNotIn("without/delivery", data["lines"])     # relaxing an OR member drops the whole OR
        self.assertEqual(data["ablation_notes"], ["without/delivery not assessed: relaxing it leaves no search block.",
                                                  "without/setting not assessed: relaxing it leaves no search block."])
        self.assertEqual(data["lines"]["drop-route/delivery"]["development_hits"], ["W1"])   # setting alone, minus hospital
        self.assertEqual(data["lines"]["drop-route/setting"]["development_hits"], ["W1"])

    def test_combine_ablation_simplifies_the_expression(self):
        tree = parse_combine("(delivery or apps) and setting", ["delivery", "apps", "setting"])
        self.assertEqual(assign(tree, "delivery", True), "setting")                       # without/delivery
        self.assertEqual(assign(tree, "delivery", False), ("and", ["apps", "setting"]))   # drop-route/delivery
        self.assertEqual(assign(tree, "setting", True), ("or", ["delivery", "apps"]))     # without/setting
        self.assertIs(assign(tree, "setting", False), False)                             # no drop-route for AND blocks
        self.assertIs(assign("a", "a", True), True)
        deep = parse_combine("(a and (b or c)) or d", ["a", "b", "c", "d"])
        self.assertEqual(assign(deep, "b", False), ("or", [("and", ["a", "c"]), "d"]))
        self.assertIs(assign(deep, "d", True), True)

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
        with self.assertRaisesRegex(Error, r"scope changed since it was confirmed \(corpus\)"):
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
                    "primary_location": {"source": {"display_name": "AJN", "issn_l": "0002-936X", "issn": ["0002-936X", "1538-7488"],
                                                    "host_organization_name": "Lippincott Williams & Wilkins"}},
                    "keywords": [{"display_name": "telemedicine"}, {"display_name": "rural health care"}],
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
        for line in ("SN  - 0002-936X", "SN  - 1538-7488", "PB  - Lippincott Williams & Wilkins",
                     "KW  - telemedicine", "KW  - rural health care", "AU  - Ada Lovelace"):
            self.assertIn(line + "\n", ris)
        row = next(__import__("csv").DictReader(open(folder / "results.csv", encoding="utf-8")))
        self.assertEqual((row["issn"], row["publisher"], row["keywords"]),
                         ("0002-936X; 1538-7488", "Lippincott Williams & Wilkins", "telemedicine; rural health care"))
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

    def test_vocab_cli_dispatch(self):
        self.api.vocabulary = lambda entity, query, size=10: {"entity": entity, "query": query, "size": size, "results": []}
        out = self.call("vocab", "keywords", "telemedicine", "--size", "3")
        self.assertEqual(out, {"entity": "keywords", "query": "telemedicine", "size": 3, "results": []})

    def test_neighbors_paging_all_cursor_and_related(self):
        # 250 synthetic works split into 3 cursor pages (100, 100, 50), plus protected W3 on page 1.
        pages = {
            "*": ([{"id": "W3", "display_name": "Protected"}] + [{"id": f"W{i}", "display_name": f"N{i}"} for i in range(100, 199)], "c2"),
            "c2": ([{"id": f"W{i}", "display_name": f"N{i}"} for i in range(199, 299)], "c3"),
            "c3": ([{"id": f"W{i}", "display_name": f"N{i}"} for i in range(299, 349)], None),
        }
        seen = []
        def fake_page(oqo, cursor="*", size=100, select=None):
            seen.append((cursor, size, select))
            rows, nxt = pages[cursor]
            return {"count": 250, "rows": rows[:size], "next_cursor": nxt, "oqo": oqo, "oql": "neighbors"}
        self.api.page = fake_page

        # --size > 100 pages automatically and filters out protected W3
        out = self.call("neighbors", "W1", "--direction", "citing", "--size", "150")
        self.assertEqual(len(out["candidates"]), 150)
        self.assertNotIn("W3", {c["id"] for c in out["candidates"]})
        self.assertEqual(out["pages"], 2)
        self.assertFalse(out["complete"])
        self.assertEqual(out["next_cursor"], "c3")

        # --cursor resumes from saved next_cursor to finish the remaining page
        rest = self.call("neighbors", "W1", "--direction", "citing", "--all", "--cursor", out["next_cursor"])
        self.assertEqual(len(rest["candidates"]), 50)
        self.assertTrue(rest["complete"])
        self.assertIsNone(rest["next_cursor"])

        # --all with --max-pages bounds the number of pages fetched
        bounded = self.call("neighbors", "W1", "--direction", "citing", "--all", "--max-pages", "2")
        self.assertEqual(bounded["pages"], 2)
        self.assertFalse(bounded["complete"])
        self.assertEqual(bounded["next_cursor"], "c3")

        # --direction related emits the OQL related-to clause
        with patch.object(self.api, "validate", wraps=self.api.validate) as v:
            self.call("neighbors", "W1", "--direction", "related", "--max-pages", "1")
            self.assertEqual(v.call_args.args[0], "get works where it's related to ([](W1))")

        # Repeated cursor raises rather than looping forever
        self.api.page = lambda oqo, cursor="*", size=100, select=None: {"count": 2, "rows": [{"id": "W99"}], "next_cursor": cursor}
        with self.assertRaisesRegex(Error, "Repeated cursor"):
            self.call("neighbors", "W1", "--direction", "citing", "--all")

    def test_multi_seed_neighbors_sample_from_strategy_and_eval_irrelevant(self):
        # 1. Multi-seed neighbors ranks co-linked candidates first, excludes seeds & protected W3
        per_seed = {
            "W1": [{"id": "W50", "display_name": "Shared", "publication_year": 2018},
                   {"id": "W51", "display_name": "Only W1", "publication_year": 2020},
                   {"id": "W3", "display_name": "Protected", "publication_year": 2020},
                   {"id": "W2", "display_name": "Other seed", "publication_year": 2020}],
            "W2": [{"id": "W50", "display_name": "Shared", "publication_year": 2018},
                   {"id": "W52", "display_name": "Only W2", "publication_year": 2019}],
        }
        order = ["W1", "W2"]
        def fake_page(oqo, cursor="*", size=100, select=None):
            rows = per_seed[order.pop(0)]
            return {"count": len(rows), "rows": rows[:size], "next_cursor": None, "oqo": oqo, "oql": "neighbors"}
        self.api.page = fake_page
        ms = self.call("neighbors", "W1", "W2", "--direction", "references", "--size", "5")
        self.assertEqual(ms["seeds"], ["W1", "W2"])
        self.assertEqual(ms["candidates"][0]["id"], "W50")
        self.assertEqual(ms["candidates"][0]["seed_count"], 2)
        self.assertEqual(ms["candidates"][0]["matched_seeds"], ["W1", "W2"])
        cids = {c["id"] for c in ms["candidates"]}
        self.assertNotIn("W3", cids)
        self.assertNotIn("W2", cids)
        with self.assertRaisesRegex(Error, "--cursor"):
            self.call("neighbors", "W1", "W2", "--direction", "references", "--cursor", "c1")

        # 2. sample --from-strategy compiles strategy.json and refuses invalid flag combos
        samp = self.call("sample", "--from-strategy", "--size", "5")
        self.assertTrue(samp["discovery"])
        self.assertIsInstance(samp["candidates"], list)
        with self.assertRaisesRegex(Error, "exactly one of --oql, --from-strategy or --added"):
            self.call("sample", "--size", "5")
        with self.assertRaisesRegex(Error, "exactly one of --oql, --from-strategy or --added"):
            self.call("sample", "--oql", "get works", "--from-strategy", "--size", "5")

        # 3. eval reports matched screened-out irrelevant works from screening.json
        self.call("screen", ms["discovery"], "W50", "exclude", "--reason", "off-topic")
        self.call("screen", ms["discovery"], "W51", "exclude", "--reason", "wrong population")
        real_members = self.api.members
        def fake_members(obj, ids, include_pulled=None):
            if set(ids) == {"W50", "W51"}:
                return {"W50"}
            return real_members(obj, ids, include_pulled)
        with patch.object(self.api, "members", side_effect=fake_members):
            ev = self.call("eval")
        self.assertEqual(ev["irrelevant"], {
            "screened_excluded": 2,
            "matched": 1,
            "match_rate": 0.5,
            "hits": ["W50"],
        })

    def test_lint_and_draft_offline(self):
        # Strategy exercising every local lint heuristic
        s = {
            "corpus": "all",
            "blocks": [
                {"id": "a", "role": "search", "field": "title-abstract",
                 "terms": ["source-content", "telemedicine", "Telemedicine"], "rationale": ""},
                {"id": "b", "role": "search", "field": "title-abstract", "terms": ["rural"], "rationale": "r"},
                {"id": "c", "role": "search", "field": "title-abstract", "terms": ["clinic"], "rationale": "r"},
                {"id": "d", "role": "search", "field": "title-abstract", "terms": ["care"], "rationale": "r"},
                {"id": "x", "role": "exclude", "field": "title", "terms": ["veterinary"], "rationale": "r"},
            ],
            "limits": {"from_date": "2015-01-01"},
        }
        self.run.put("strategy", s)
        # Ensure neither lint nor draft touches the network/API
        with patch.object(self.api, "validate", side_effect=AssertionError("offline!")), \
             patch.object(self.api, "run", side_effect=AssertionError("offline!")):
            checked = self.call("lint")
            self.assertFalse(checked["validated_against_openalex"])
            codes = {w["code"] for w in checked["warnings"]}
            self.assertEqual(codes, {"missing_rationale", "duplicate_term", "unquoted_hyphen",
                                     "many_and_blocks", "exclude_blocks"})
            for bad, reason in [("ab*", "three leading characters"), ('"te* care"', "three leading characters"),
                                ('stemmed "tele* care"', 'Drop "stemmed"')]:
                with self.assertRaisesRegex(Error, reason):
                    lint({"blocks": [{"id": "a", "role": "search", "terms": [bad], "rationale": "r"}]})
            lint({"blocks": [{"id": "a", "role": "search", "terms": ["tel*", '"tele* care"'], "rationale": "r"}]})
            out = self.call("draft")
        self.assertFalse(out["validated_against_openalex"])
        folder = Path(out["draft"])
        self.assertEqual((folder / "draft-query.oql").read_text().strip(), checked["oql"])
        self.assertIn("Untested draft — not validated against OpenAlex", (folder / "draft-notes.md").read_text())
        self.assertFalse(json.loads((folder / "draft-manifest.json").read_text())["validated_against_openalex"])
        # Draft cannot be exported as a validated report
        with self.assertRaisesRegex(Error, "No delivered report"):
            self.call("export")

    def test_rescue_and_rescue_export(self):
        report = self.deliver()
        # Mock rescue validation/run/members: rescue catches missed dev W2 and protected W3
        real_val = self.api.validate
        def fake_val(q):
            if "doesn't have an abstract" in q:
                return {"oqo": rescue_oqo(), "oql": q, "validation": {"valid": True, "warnings": []}}
            return real_val(q)
        def fake_run(q, size=1, fields="id", include_pulled=None):
            if isinstance(q, dict):
                return {"oqo": q, "oql": "no_abs", "rows": [], "count": 1}
            if "doesn't have an abstract" in q:
                oq = fake_val(q)["oqo"]
                return {"oqo": oq, "oql": q, "rows": [], "count": 2}
            return Index.run(self.api, q, size, fields, include_pulled)
        def fake_members(obj, ids):
            if any(f.get("column_id") == "has_abstract" for f in obj.get("filter_rows", [])):
                return set(ids) & {"W2", "W3"}
            return Index.members(self.api, obj, ids)
        with patch.object(self.api, "validate", side_effect=fake_val), \
             patch.object(self.api, "run", side_effect=fake_run), \
             patch.object(self.api, "members", side_effect=fake_members):
            out = self.call("rescue", "--direction", "cited-by", "--method", "walk")
        self.assertEqual(out["report"], report.name)
        self.assertEqual(out["gate"], "any-block")
        self.assertEqual(out["rescue_count"], 2)
        self.assertEqual(out["development_rescued"], ["W2"])
        self.assertNotIn("W3", out["development_rescued"])  # protected record not leaked
        rdir = Path(out["rescue_dir"])
        self.assertTrue((rdir / "rescue-query-cited-by.oql").is_file())
        manifest = json.loads((rdir / "rescue-manifest.json").read_text())
        self.assertEqual(manifest["rescued_all_sets"], ["W2", "W3"])
        # Export --rescue pages the rescue query into exports/<report>-rescue/
        self.api.page = lambda oqo, cursor="*", size=100: {"count": 2, "rows": [{"id": "https://openalex.org/W2", "display_name": "Rescued"}], "next_cursor": None}
        exp = self.call("export", "--rescue")
        self.assertTrue(exp["complete"])
        self.assertTrue(exp["rescue"])
        self.assertTrue(exp["directory"].endswith(report.name + "-rescue"))
        self.assertEqual(exp["unique_records"], 1)

    def test_rescue_refuses_term_dropped_inside_nested_set(self):
        self.deliver()
        dropped = rescue_oqo(terms=("telemedicine", "telehealth", "rural"))   # 'remote' lost inside the set
        real_val = self.api.validate
        def fake_val(q):
            if "doesn't have an abstract" in q:
                return {"oqo": dropped, "oql": q, "validation": {"valid": True, "warnings": []}}
            return real_val(q)
        with patch.object(self.api, "validate", side_effect=fake_val):
            with self.assertRaisesRegex(Error, "kept 8 of 9"):
                self.call("rescue", "--direction", "cited-by", "--method", "walk")

    def test_relative_recall_interval_in_eval_and_audit(self):
        sets = self.call("eval")["sets"]
        self.assertEqual(sets["seed"]["relative_recall"], 0.5)
        self.assertEqual(sets["seed"]["relative_recall_ci95"], wilson(1, 2))
        self.attach_critique()
        audit = (Path(self.call("report")["report"]) / "audit.md").read_text()
        self.assertIn("| seed | 2 | 2 | 1 | 50% (95% CI ", audit)
        self.assertIn("| benchmark | 1 | 0 | 0 | no estimate |", audit)

    def test_holdout_exposure_labels_later_reports(self):
        first = self.deliver()
        archive = json.loads((first / "validation.json").read_text())
        self.assertIsNone(archive["post_exposure"])
        self.assertIn("No validation or benchmark result had been shown", (first / "audit.md").read_text())
        # Recording exposure must not invalidate the delivered evidence.
        self.assertFalse(self.call("export", "--estimate")["translation_changed_since_report"])
        self.assertEqual(self.run.fingerprint(), archive["evaluation"]["input_hash"])
        self.assertEqual(self.call("eval")["holdout_exposure"]["exposed"], 1)
        # A holdout registered after the first report stays unexposed.
        self.api.resolve = lambda raw: (identifier(raw), {"id": "W4", "display_name": "Later holdout"})
        self.call("resolve", "W4", "--role", "validation", "--source", "added later")
        self.call("eval")
        self.attach_critique()
        second = self.call("report")
        self.assertTrue(second["post_exposure"])
        self.assertEqual(second["exposed_holdouts"], ["W3"])
        archive = json.loads((Path(second["report"]) / "validation.json").read_text())
        post = archive["post_exposure"]
        self.assertEqual(post["earlier_reports"], [first.name])
        self.assertEqual(post["unexposed_sets"]["validation"]["resolved"], 1)   # only W4
        self.assertIn("**Post-exposure delivery.**", (Path(second["report"]) / "audit.md").read_text())
        events = self.run.get("exposure")["events"]
        self.assertEqual([e["ids"] for e in events], [["W3"], ["W3", "W4"]])

    def test_export_estimate_is_free_and_tracks_progress(self):
        report = self.deliver()
        count = json.loads((report / "validation.json").read_text())["evaluation"]["final"]["count"]
        est = self.call("export", "--estimate")
        self.assertEqual((est["pages"], est["credits_per_page"]), (1, 10))   # text search: 10 credits a page
        self.assertIn("documented price", est["basis"])
        self.assertEqual(est["validated_count"], count)
        self.assertFalse((self.run.root / "exports").exists())                # nothing downloaded
        real_val = self.api.validate
        with patch.object(self.api, "validate", side_effect=lambda q: dict(real_val(q), check={"cost": {"credits": 13}})):
            est = self.call("export", "--estimate")
        self.assertEqual((est["credits_per_page"], est["estimated_credits"], est["estimated_usd"]), (13, 13, 0.0013))
        self.call("export")
        self.assertEqual(self.call("export", "--estimate")["remaining_pages"], 0)

    def test_pulled_known_work_is_a_miss_not_a_hit(self):
        self.api.pulled_ids = {"W1"}            # W1 is the only seed the search otherwise retrieves
        ev = self.call("eval")
        self.assertEqual(ev["sets"]["seed"]["hits"], [])
        self.assertEqual(ev["misses"]["W1"], ["pulled"])
        self.assertEqual(ev["pulled"], {"view": "exclude", "final_pulled_works": 1, "development_pulled": ["W1"]})
        self.attach_critique()
        audit = (Path(self.call("report")["report"]) / "audit.md").read_text()
        self.assertIn("Pulled works: excluded (request parameter include_pulled=false", audit)
        self.assertIn("1 retracted, withdrawn or removed works matching this search are left out", audit)
        self.assertIn("Known works OpenAlex marks as pulled: W1.", audit)
        self.assertIn("- W1 — pulled", audit)

    def test_including_pulled_is_a_scope_decision(self):
        self.api.pulled_ids = {"W1"}
        self.run.put("strategy", dict(self.strategy, pulled="include"))
        with self.assertRaisesRegex(Error, r"scope changed since it was confirmed \(pulled-works setting\).*scope --confirm"):
            self.call("eval")
        self.call("scope", "--confirm", "--note", "Review must identify retracted studies")
        ev = self.call("eval")
        self.assertEqual(ev["sets"]["seed"]["hits"], ["W1"])
        self.assertEqual(ev["pulled"]["view"], "include")
        self.assertEqual(ev["pulled"]["final_pulled_works"], 1)
        self.attach_critique()
        report = Path(self.call("report")["report"])
        self.assertIn("Pulled works: included", (report / "audit.md").read_text())
        self.api.page = lambda oqo, cursor="*", size=100: {"count": 1, "rows": [
            {"id": "https://openalex.org/W1", "display_name": "Pulled", "is_pulled": True, "is_retracted": True}], "next_cursor": None}
        self.api.include_pulled = False          # export must set the delivered view itself
        out = self.call("export")
        self.assertTrue(out["include_pulled"] and self.api.include_pulled)
        rows = list(__import__("csv").DictReader(open(Path(out["directory"]) / "results.csv", encoding="utf-8")))
        self.assertEqual((rows[0]["is_pulled"], rows[0]["is_retracted"]), ("True", "True"))
        with self.assertRaises(Error):
            check(dict(self.strategy, pulled="hide"))

    def patch_rescue(self, counts, slow=()):
        """Rescue validation/run/members fakes: per-direction counts, hits and optional timeouts."""
        real_val, real_run = self.api.validate, self.api.run
        def direction(q):
            return "citing" if "it cites a work" in q else "cited-by"
        def fake_val(q):
            if "doesn't have an abstract" not in q:
                return real_val(q)
            out = {"oqo": rescue_oqo(), "oql": q, "validation": {"valid": True, "warnings": []},
                   "check": {"estimate": {"seconds": 9.0 if direction(q) in slow else 2.0, "budget_seconds": 10.0}}}
            out["oqo"] = dict(out["oqo"], tag=direction(q))   # distinct query objects per direction
            return out
        def fake_run(q, size=1, fields="id", include_pulled=None):
            if isinstance(q, dict):
                return {"oqo": q, "oql": "no_abs", "rows": [], "count": 1}
            if "doesn't have an abstract" in q:
                if counts[direction(q)] is None:
                    raise Error('OpenAlex HTTP 500: {"errors":[{"type":"es_error","message":"Connection timed out"}]}')
                return {"oqo": fake_val(q)["oqo"], "oql": q, "rows": [], "count": counts[direction(q)]}
            return real_run(q, size, fields, include_pulled)
        def fake_members(obj, ids, include_pulled=None):
            if obj.get("tag"):
                return set(ids) & ({"W2"} if obj["tag"] == "cited-by" else {"W2", "W3"})
            return Index.members(self.api, obj, ids, include_pulled)
        return (patch.object(self.api, "validate", side_effect=fake_val), patch.object(self.api, "run", side_effect=fake_run),
                patch.object(self.api, "members", side_effect=fake_members))

    def test_rescue_both_runs_two_queries_and_export_deduplicates(self):
        report = self.deliver()
        a, b, c = self.patch_rescue({"cited-by": 2, "citing": 3}, slow={"citing"})
        with a, b, c:
            self.assertEqual(self.call("rescue", "--method", "walk")["direction"], "cited-by")   # default direction
            out = self.call("rescue", "--direction", "both", "--method", "walk")
        self.assertEqual(out["counts_by_direction"], {"cited-by": 2, "citing": 3})
        self.assertIsNone(out["rescue_count"])
        self.assertEqual(out["rescue_count_upper_bound"], 5)
        self.assertTrue(any("close to its time budget" in n for n in out["notes"]))
        rdir = Path(out["rescue_dir"])
        self.assertEqual(sorted(p.name for p in rdir.glob("rescue-query*")),
                         ["rescue-query-cited-by.json", "rescue-query-cited-by.oql",
                          "rescue-query-citing.json", "rescue-query-citing.oql"])
        pages = {"cited-by": [{"id": "https://openalex.org/W2"}, {"id": "https://openalex.org/W7"}],
                 "citing": [{"id": "https://openalex.org/W7"}, {"id": "https://openalex.org/W8"}, {"id": "https://openalex.org/W9"}]}
        seen = []
        def page(oqo, cursor="*", size=100):
            seen.append(oqo["tag"])
            return {"count": len(pages[oqo["tag"]]), "rows": pages[oqo["tag"]], "next_cursor": None}
        self.api.page = page
        est = self.call("export", "--rescue", "--estimate")
        self.assertEqual((est["validated_count"], est["pages"], len(est["queries"])), (5, 2, 2))
        exp = self.call("export", "--rescue", "--max-pages", "1")            # stop between the two queries
        self.assertFalse(exp["complete"])
        exp = self.call("export", "--rescue")                                # resume with the citing query
        self.assertEqual(seen, ["cited-by", "citing"])
        self.assertTrue(exp["complete"])
        self.assertEqual((exp["received"], exp["unique_records"], exp["duplicates_across_queries"]), (5, 4, 1))
        self.assertEqual(self.call("export", "--rescue", "--estimate")["remaining_pages"], 0)
        # A rescue rerun in another direction must not resume the old checkpoint.
        a, b, c = self.patch_rescue({"cited-by": 2, "citing": 3})
        with a, b, c:
            self.call("rescue", "--direction", "citing")
        with self.assertRaisesRegex(Error, "checkpoint for a different query"):
            self.call("export", "--rescue")
        # Older single-query rescues stay exportable.
        legacy = json.loads((rdir / "rescue-manifest.json").read_text())
        q = legacy.pop("queries")[0]
        legacy.update(oql=q["oql"], oqo=q["oqo"], rescue_count=q["count"])
        write(rdir / "rescue-manifest.json", legacy)
        (rdir / "rescue-query.oql").write_text(q["oql"] + "\n")
        write(rdir / "rescue-query.json", q["oqo"])
        self.assertEqual(self.call("export", "--rescue", "--estimate")["validated_count"], 3)

    def test_rescue_timeout_gives_actionable_advice(self):
        self.deliver()
        a, b, c = self.patch_rescue({"cited-by": 2, "citing": None}, slow={"citing"})
        with a, b, c:
            with self.assertRaisesRegex(Error, r"citing rescue query timed out.*9.0s of a 10.0s budget.*"
                                               r"cited-by direction completed.*--direction cited-by.*does not shorten it.*neighbors"):
                self.call("rescue", "--direction", "both", "--method", "walk")

    def test_term_counts_flag_stemming_and_hyphen_phrases(self):
        self.run.put("strategy", {"blocks": [
            {"id": "diet", "role": "search", "field": "title", "terms": ["fasting", "ration"], "rationale": "r"},
            {"id": "beh", "role": "search", "field": "title", "terms": ['"open-field"', '"COVID-19"', 'stemmed "risk-prone"'], "rationale": "r"},
            {"id": "notes", "role": "screen", "terms": [], "rationale": "r"}], "limits": {"to_date": "2020-12-31"}})
        live = {'fasting': 471555, '"fasting"': 33935, 'ration': 16830, '"ration"': 9400, '"open-field"': 10487,
                '"open field"': 4872, '"COVID-19"': 968014, '"COVID 19"': 967735, 'stemmed "risk-prone"': 158}
        queries = []
        def fake_run(q, size=1, fields="id", include_pulled=None):
            term = re.search(r"title has \((.*)\) and publication_date", q)[1]
            queries.append(q)
            return {"oqo": self.api.validate(q)["oqo"], "oql": q, "rows": [], "count": live[term]}
        with patch.object(self.api, "run", side_effect=fake_run):
            out = self.call("term-counts")
            self.assertEqual(len(queries), len(live))            # one count per distinct query
            self.assertTrue(all("publication_date <= 2020-12-31" in q for q in queries))   # limits kept
            self.assertEqual([(f["term"], f["flag"]) for f in out["flagged"]],
                             [("fasting", "stemming_expands"), ('"open-field"', "hyphen_not_phrase")])
            self.assertEqual(out["terms"][2]["phrase_count"], 4872)
            self.assertEqual(len(self.call("term-counts", "--block", "beh")["terms"]), 3)
            with self.assertRaisesRegex(Error, "No block with terms"):
                self.call("term-counts", "--block", "notes")
        self.assertTrue(list((self.run.root / "term-counts").glob("*.json")))

    def test_revision_history_follows_timestamps_not_file_times(self):
        self.call("eval")
        self.strategy["blocks"][1]["terms"].append("hospital")
        self.run.put("strategy", self.strategy)
        self.call("scope", "--confirm", "--note", "added hospital")
        self.call("eval")
        files = list((self.run.root / "evaluations").glob("*.json"))
        first = next(p for p in files if "change" not in json.loads(p.read_text()))
        second = next(p for p in files if p != first)
        for path, at in [(first, "2000-01-01T00:00:00+00:00"), (second, "2000-01-02T00:00:00+00:00")]:
            data = json.loads(path.read_text()); data["at"] = at; write(path, data)
        os.utime(first, ns=(4 * 10**18, 4 * 10**18))   # a copy can give the older file the newer time
        os.utime(second, ns=(10**18, 10**18))
        self.attach_critique()
        audit = (Path(self.call("report")["report"]) / "audit.md").read_text()
        history = audit.split("## Revision history")[1].split("##")[0]
        lines = [l for l in history.splitlines() if l.startswith("- ")]
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("- 2000-01-01") and "Known works gained: none" in lines[0])
        self.assertTrue(lines[1].startswith("- 2000-01-02") and "Known works gained: W2" in lines[1])

    def test_readable_errors(self):
        with self.assertRaisesRegex(Error, "Run eval first"):
            self.call("review", str(self.run.root / "critique.json"))
        self.call("eval")
        with self.assertRaisesRegex(Error, "Attach a critique with the review command first"):
            self.call("report")
        (self.run.root / "records.json").write_text("{broken")
        with self.assertRaisesRegex(Error, "records.json is not valid JSON"):
            self.call("eval")
        err = io.StringIO()
        with patch.object(sys, "argv", ["oasb", "-w", str(self.run.root), "lint"]), \
                patch.object(cli, "dispatch", side_effect=KeyError("hits")), patch.object(sys, "stderr", err):
            self.assertEqual(cli.main(), 1)
        self.assertIn("lint: a workspace file or OpenAlex response lacks the expected field 'hits'", err.getvalue())

    def test_report_drafts_prisma_s_items(self):
        self.strategy["limits"] = {"from_date": "2010-01-01", "language": ["en"]}
        self.reconfirm(self.strategy)
        self.call("neighbors", "W1", "--direction", "citing", "--size", "2")
        self.call("neighbors", "W1", "--direction", "references", "--size", "2")
        report = self.deliver()
        data = json.loads((report / "prisma-s.json").read_text())
        self.assertIn("10.1186/s13643-020-01542-z", data["guideline"])
        topics = ["Database name", "Multi-database searching", "Study registries", "Online resources and browsing",
                  "Citation searching", "Contacts", "Other methods", "Full search strategies", "Limits and restrictions",
                  "Search filters", "Prior work", "Updates", "Dates of searches", "Peer review", "Total records",
                  "Deduplication"]
        self.assertEqual([(i["item"], i["topic"]) for i in data["items"]], list(enumerate(topics, 1)))
        by = {i["topic"]: i for i in data["items"]}
        self.assertIn("2 batch(es) of OpenAlex citation links (1 citing, 1 references)", by["Citation searching"]["text"])
        self.assertEqual(by["Limits and restrictions"]["status"], "partly filled")
        self.assertIn("publication date 2010-01-01 to any; language in (en)", by["Limits and restrictions"]["text"])
        self.assertIn('"revised fixture"', by["Limits and restrictions"]["text"])
        self.assertEqual(by["Study registries"]["status"], "reviewer to complete")
        count = json.loads((report / "validation.json").read_text())["evaluation"]["final"]["count"]
        self.assertIn(f"OpenAlex: {count:,} records", by["Total records"]["text"])
        audit = (report / "audit.md").read_text()
        self.assertIn("## PRISMA-S search reporting checklist (draft)", audit)
        self.assertEqual(audit.count("\n| 1 | Database name | filled |"), 1)
        self.assertRegex(audit, r"last searched on \d{4}-\d\d-\d\d \(at \d\d:\d\d:\d\d UTC\)")

    def test_rationale_edits_keep_the_scope_and_real_changes_name_themselves(self):
        self.strategy["blocks"][0]["rationale"] = "Reworded rationale"
        self.run.put("strategy", self.strategy)
        self.call("eval")                                       # no re-confirmation needed
        self.strategy["blocks"][1]["role"] = "optional"
        self.strategy["blocks"].append({"id": "extra", "role": "screen", "terms": [], "rationale": "r"})
        self.run.put("strategy", self.strategy)
        with self.assertRaisesRegex(Error, r"blocks added: extra; roles changed: setting search→optional.*scope --confirm"):
            self.call("eval")
        # A scope confirmed before this change (hash included rationale, no stored key) stays valid.
        self.strategy["blocks"][1]["role"] = "search"
        self.strategy["blocks"].pop()
        self.run.put("strategy", self.strategy)
        write(self.run.root / "scope.json", {"at": "2026-11-25T00:00:00+00:00", "hash": digest(self.run.scope_data()),
                                             "decision": "legacy approval"})
        self.call("eval")
        self.strategy["limits"] = {"to_date": "2020-12-31"}
        self.run.put("strategy", self.strategy)
        with self.assertRaisesRegex(Error, r"The scope changed since it was confirmed\. Check it"):
            self.call("eval")                                   # legacy record: no detail, still a clear command

    def test_review_fills_figures_and_refuses_stale_counts(self):
        self.call("eval")
        uid, current = self.run.latest("evaluations")
        earlier = dict(current, at="2000-01-01T00:00:00+00:00", final=dict(current["final"], count=30848))
        write(self.run.root / "evaluations" / ("0" * 32 + ".json"), earlier)
        file = self.run.root / "critique.json"
        base = dict.fromkeys(("scope", "vocabulary", "boolean_and_fields", "limits", "limitations"), "Reviewed.") | {"decision": "ready"}
        write(file, dict(base, retrieval="The search returns 30,848 works."))
        with self.assertRaisesRegex(Error, r"cites 30,848 works, the final count of an earlier evaluation.*\{final_count\}"):
            self.call("review", str(file))
        write(file, dict(base, retrieval="Down from 30,848 to {final_count} works; development recall {development_recall}; {pulled_count} pulled."))
        out = self.call("review", str(file))
        self.assertEqual(out["placeholders_filled"], ["development_recall", "final_count", "pulled_count"])
        write(file, dict(base, retrieval="{validation_recall}"))      # holdout figures are not offered
        with self.assertRaisesRegex(Error, r"Unknown critique placeholder\(s\): validation_recall\. Available: .*\{final_count\}"):
            self.call("review", str(file))
        write(file, dict(base, retrieval="Down from 30,848 to {final_count} works; development recall {development_recall}."))
        self.call("review", str(file))
        audit = (Path(self.call("report")["report"]) / "audit.md").read_text()
        count = current["final"]["count"]
        self.assertIn(f"Down from 30,848 to {count:,} works; development recall 50% (95% CI 9–91%).", audit)
        self.assertIn("filled in by `review` from evaluation", audit)

    def test_discovery_applies_the_date_window(self):
        self.strategy["limits"] = {"from_date": "2000-01-01", "to_date": "2020-12-31"}
        self.reconfirm(self.strategy)
        sent = []
        def page(oqo, cursor="*", size=100, select=None):
            sent.append(oqo)
            return {"count": 0, "rows": [], "next_cursor": None, "oqo": oqo, "oql": "neighbors"}
        self.api.page = page
        out = self.call("neighbors", "W1", "--direction", "related")
        rows = sent[-1]["filter_rows"]
        self.assertIn({"column_id": "to_publication_date", "value": "2020-12-31"}, rows)
        self.assertIn({"column_id": "from_publication_date", "value": "2000-01-01"}, rows)
        self.assertEqual(out["date_window"], {"from": "2000-01-01", "to": "2020-12-31"})
        self.call("neighbors", "W1", "W2", "--direction", "citing")                  # multi-seed path
        self.assertIn({"column_id": "to_publication_date", "value": "2020-12-31"}, sent[-1]["filter_rows"])
        out = self.call("neighbors", "W1", "--direction", "citing", "--any-date")
        self.assertFalse(any("publication_date" in r.get("column_id", "") for r in sent[-1]["filter_rows"]))
        self.assertIsNone(out["date_window"])
        with patch.object(self.api, "run", wraps=self.api.run) as run:
            self.call("sample", "--oql", "get works where title has (telemedicine)", "--size", "3")
            rows = run.call_args.args[0]["filter_rows"]
            self.assertEqual(sum(r == {"column_id": "to_publication_date", "value": "2020-12-31"} for r in rows), 1)
        from oasb.records import date_window, windowed
        bound = {"column_id": "to_publication_date", "value": "2020-12-31"}
        self.assertEqual(windowed({"filter_rows": [bound]}, date_window({"limits": {"to_date": "2020-12-31"}})),
                         {"filter_rows": [bound]})                       # a row the query already has is not repeated

    def test_dead_end_seeds_and_related_limits(self):
        calls = []
        def page(oqo, cursor="*", size=100, select=None):
            calls.append(size)
            return {"count": 30, "rows": [{"id": f"W{i}", "display_name": f"N{i}"} for i in range(100, 100 + size)],
                    "next_cursor": None, "oqo": oqo, "oql": "neighbors"}
        self.api.page = page
        self.api.links = {"W1": {"referenced_works_count": 0, "cited_by_count": 4}}
        with self.assertRaisesRegex(Error, r"W1 has no matched references in OpenAlex.*--direction citing.*open-access"):
            self.call("neighbors", "W1", "--direction", "references")
        self.assertEqual(calls, [])                                    # refused before any paging request
        out = self.call("neighbors", "W1", "W2", "--direction", "references")
        self.assertEqual((out["seed"], out["skipped_seeds"]), ("W2", ["W1"]))
        self.assertIn("Skipped W1: no matched references in OpenAlex.", out["notes"])
        out = self.call("neighbors", "W1", "--direction", "citing")
        self.assertEqual((out["route_kind"], out["seed_links"]["W1"]["cited_by_count"]), ("citation", 4))
        calls.clear()
        out = self.call("neighbors", "W1", "--direction", "related", "--size", "50", "--all")
        self.assertEqual((calls, len(out["candidates"]), out["route_kind"]), ([10], 10, "text-similarity"))
        self.assertIn("never include reviews", out["notes"][0])
        self.assertEqual(out["seed_links"], {})                        # no link check for a text route
        report = self.deliver()
        items = {i["topic"]: i for i in json.loads((report / "prisma-s.json").read_text())["items"]}
        self.assertIn("2 batch(es) of OpenAlex citation links (1 citing, 1 references)", items["Citation searching"]["text"])
        self.assertIn("about 82% of references", items["Citation searching"]["text"])
        self.assertIn("1 batch(es) of OpenAlex related works", items["Other methods"]["text"])

    def test_reference_list_rescue_end_to_end(self):
        self.strategy["limits"] = {"to_date": "2020-12-31"}
        self.reconfirm(self.strategy)
        report = self.deliver()
        delivered_rows = [{"id": "https://openalex.org/W1", "display_name": "Delivered",
                           "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W8", "https://openalex.org/W9",
                                                "https://openalex.org/W10", "https://openalex.org/W11"]}]
        paged = []
        def page(oqo, cursor="*", size=100, select=None):
            paged.append(select)
            return {"count": 1, "rows": delivered_rows, "next_cursor": None, "oqo": oqo, "oql": "delivered"}
        self.api.page = page
        self.api.lookup_index = {"W1": {"has_abstract": False, "year": 2015},   # delivered already: never rescued
                                 "W8": {"has_abstract": False, "year": 2015},   # rescued
                                 "W9": {"has_abstract": True, "year": 2015},    # has an abstract
                                 "W10": {"has_abstract": False, "year": 2024},  # after the cutoff
                                 "W11": {"has_abstract": False, "year": 2010}}  # fails the text gate
        self.api.reference_line_data = {"W1": [{"raw_reference_string": "Grey report 1999", "raw_doi": None, "work_id": None},
                                               {"raw_reference_string": "Linked", "raw_doi": None, "work_id": "https://openalex.org/W8"}]}
        real_val = self.api.validate
        def fake_val(q):
            if "doesn't have an abstract" in q and "in the set" not in q:   # the gate alone
                out = real_val(q.replace("it doesn't have an abstract and ", ""))
                return dict(out, oqo=dict(out["oqo"], filter_rows=[{"column_id": "has_abstract", "value": False},
                                                                   *out["oqo"]["filter_rows"]], tag="gate"))
            return real_val(q)
        def fake_members(obj, ids, include_pulled=None, batch_size=25):
            if obj.get("tag") == "gate":
                return set(ids) & {"W8", "W1"}
            return Index.members(self.api, obj, ids, include_pulled)
        with patch.object(self.api, "validate", side_effect=fake_val), patch.object(self.api, "members", side_effect=fake_members):
            est = self.call("rescue", "--estimate")
            self.assertEqual((est["parts"][0]["method"], est["parts"][0]["paging_credits"] > 0), ("references", True))
            self.assertFalse((report / "rescue").exists())                         # an estimate writes nothing
            out = self.call("rescue")
        self.assertEqual((out["method"], out["rescue_count"]), ("references", 1))
        self.assertEqual(out["stages"]["cited-by"], {"delivered_works": 1, "with_matched_references": 1, "reference_links": 5,
                                                     "cited_outside_delivered": 4, "without_abstract_in_window": 2,
                                                     "passing_gate": 1})
        self.assertEqual(paged, ["id,referenced_works"])                         # no export yet: paged
        rdir = Path(out["rescue_dir"])
        self.assertEqual(json.loads((rdir / "rescue-ids-cited-by.json").read_text()), ["W8"])
        self.assertEqual(json.loads((rdir / "unlinked-references.json").read_text()),
                         [{"cited_by": "W1", "raw_reference_string": "Grey report 1999", "raw_doi": None}])
        self.assertTrue(any("matched references" in n for n in out["notes"]))
        from oasb.export import rescue_queries
        self.assertEqual(rescue_queries(report)[0]["ids"], ["W8"])
        est = self.call("export", "--rescue", "--estimate")
        self.assertEqual((est["credits_per_page"], est["basis"]), (1, "lookup by ID, 1 credit per 100 IDs"))
        exp = self.call("export", "--rescue")
        self.assertEqual((exp["complete"], exp["unique_records"]), (True, 1))
        # With a complete primary export, reference lists come from it and the search is not paged again.
        self.call("export")
        paged.clear()
        with patch.object(self.api, "validate", side_effect=fake_val), patch.object(self.api, "members", side_effect=fake_members):
            self.call("rescue")
        manifest = json.loads((rdir / "rescue-manifest.json").read_text())
        self.assertEqual((paged, manifest["queries"][0]["source"]["from"]), ([], "export"))
        (rdir / "rescue-ids-cited-by.json").write_text('["W9"]')
        with self.assertRaisesRegex(Error, "rescue ID list differs"):
            self.call("export", "--rescue")

    def test_broadening_needs_evidence(self):
        from oasb.evaluate import revision_cost
        dev = {"W1", "W2"}
        grew = {"final": {"count": 3000}, "change": {"count_delta": 1000, "gained": ["W3"], "lost": []}}
        out = revision_cost(grew, dev)                                # W3 is a holdout: not counted, not shown
        self.assertEqual((out["development_gained"], out["warning"]["code"]), ([], "broadening_without_gain"))
        self.assertIn("grew by 1,000 works (50%)", out["warning"]["message"])
        self.assertNotIn("warning", revision_cost({"final": {"count": 2400}, "change": {"count_delta": 400, "gained": [], "lost": []}}, dev))
        paid = revision_cost({"final": {"count": 3000}, "change": {"count_delta": 1000, "gained": ["W1", "W2"], "lost": []}}, dev)
        self.assertEqual((paid["works_per_gained_development_work"], "warning" in paid), (500, False))
        self.assertIsNone(revision_cost({"final": {"count": 3}}, dev))  # first evaluation: nothing to compare

        first = self.call("eval")                                     # final retrieves W1
        self.assertIsNone(first["revision"])
        self.assertEqual(first["precision"]["screened"], 0)
        sample = self.call("sample", "--from-strategy", "--size", "3")
        self.call("screen", sample["discovery"], "W1", "include", "--reason", "eligible")
        self.call("screen", sample["discovery"], "W2", "exclude", "--reason", "not rural")
        prec = self.call("eval")["precision"]
        self.assertEqual((prec["screened"], prec["strict"]["precision"], prec["strict"]["ci95"]), (2, 0.5, wilson(1, 2)))
        self.assertEqual(prec["strict"]["estimated_eligible_records"][1], 0)   # 50% of 1 record rounds to 0
        self.strategy["blocks"][1]["terms"].append("hospital")         # broadening that adds W2
        self.reconfirm(self.strategy)
        second = self.call("eval")
        self.assertEqual(second["revision"]["development_gained"], ["W2"])
        self.assertEqual(second["precision"]["samples_of_earlier_strategies"], 1)   # old sample is stale now
        added = self.call("sample", "--added", "--size", "5")
        self.assertEqual(([c["id"] for c in added["candidates"]], added["drawn"], added["added_in_draw"]), (["W2"], 2, 1))
        self.call("screen", added["discovery"], "W2", "include", "--reason", "eligible after all")
        fresh = self.call("sample", "--from-strategy", "--size", "3", "--seed", "7")
        self.call("screen", fresh["discovery"], "W1", "include", "--reason", "eligible")
        self.call("screen", fresh["discovery"], "W2", "unclear", "--reason", "setting unclear")
        self.call("eval")
        self.attach_critique()
        report = Path(self.call("report")["report"])
        audit = (report / "audit.md").read_text()
        self.assertIn("## Precision", audit)
        self.assertIn("1 of 2 screened records eligible: 50%", audit)
        self.assertIn("Counting 1 unclear as eligible: 100%", audit)
        self.assertEqual(json.loads((report / "validation.json").read_text())["precision"]["samples"], 1)
        with self.assertRaisesRegex(Error, "exactly one of"):
            self.call("sample", "--added", "--from-strategy")

    def test_quick_eval_skips_ablation_and_review_needs_full(self):
        quick = self.call("eval", "--quick")
        self.assertEqual(sorted(quick["lines"]), ["block/delivery", "block/setting"])
        self.assertEqual(quick["ablation"], "not run (quick evaluation)")
        with self.assertRaisesRegex(Error, "was --quick.*Run eval without --quick"):
            self.attach_critique()
        full = self.call("eval")
        self.assertGreater(len(full["lines"]), len(quick["lines"]))
        self.assertIn("without/delivery", full["lines"])
        self.assertEqual(full["ablation"], "assessed")
        self.attach_critique()
        self.call("report")

    def test_capture_recapture_from_discovery(self):
        from oasb.capture import capture_recapture
        self.assertIsNone(capture_recapture(self.run)["estimate"])          # nothing discovered yet
        rows = lambda ids: [{"id": i} for i in ids]
        write(self.run.root / "discovery" / ("a" * 32 + ".json"), {"route": "sample", "route_kind": "text", "rows": rows(["W10", "W11", "W12"]), "protected_hits": []})
        write(self.run.root / "discovery" / ("b" * 32 + ".json"), {"route": "neighbors", "direction": "citing", "route_kind": "citation",
                                                               "rows": rows(["W11", "W13"]), "protected_hits": ["W12"]})
        write(self.run.root / "screening.json", {f"{b * 32}/{w}": {"work_id": w, "discovery": b * 32, "decision": d, "reason": "r"}
                                                 for b, w, d in [("a", "W10", "include"), ("a", "W11", "include"), ("a", "W12", "include"),
                                                                 ("b", "W13", "include"), ("b", "W11", "include")]})
        c = capture_recapture(self.run)
        # text: W10, W11, W12; citation: W11, W13 and the hidden validation work W12 -> both: W11, W12
        self.assertEqual((c["text_route"], c["citation_route"], c["both_routes"], c["observed"]), (3, 3, 2, 4))
        self.assertAlmostEqual(c["estimate"], round(4 * 4 / 3 - 1, 1))
        self.assertEqual(c["discovery_completeness"], round(4 / (4 * 4 / 3 - 1), 3))
        self.assertIn("Only 2 work(s) found by both routes", c["message"])
        batch = json.loads((self.run.root / "discovery" / ("b" * 32 + ".json")).read_text())
        del batch["protected_hits"]                                         # a batch saved before the field existed
        write(self.run.root / "discovery" / ("b" * 32 + ".json"), batch)
        c = capture_recapture(self.run)
        self.assertEqual((c["both_routes"], "predate" in c["note"]), (1, True))
        self.call("eval")
        self.attach_critique()
        audit = (Path(self.call("report")["report"]) / "audit.md").read_text()
        self.assertIn("## Estimated number of relevant works (capture–recapture)", audit)
        self.assertIn("discovery found about", audit)

    def test_discovery_records_hidden_validation_works(self):
        def page(oqo, cursor="*", size=100, select=None):
            return {"count": 2, "rows": [{"id": "W3", "display_name": "validation"}, {"id": "W40", "display_name": "new"}],
                    "next_cursor": None, "oqo": oqo, "oql": "neighbors"}
        self.api.page = page
        out = self.call("neighbors", "W1", "--direction", "citing")
        self.assertEqual([c["id"] for c in out["candidates"]], ["W40"])       # W3 (validation) is not shown
        batch = self.run.get("discovery/" + out["discovery"])
        self.assertEqual(batch["protected_hits"], ["W3"])
        self.assertNotIn("protected_hits", out)                             # not echoed to the builder
        sample = self.call("sample", "--oql", "get works where title has (telemedicine)", "--size", "3")
        self.assertEqual(self.run.get("discovery/" + sample["discovery"])["protected_hits"], ["W3"])
        self.assertIn("capture_recapture", self.call("eval"))

    def test_eval_abstract_stats_and_audit(self):
        # Mock no-abstract counts: final has 0/1 (0%), while blocks have 1/2 (50%) -> triggers attrition_note
        real_run = self.api.run
        def fake_run(q, size=1, fields="id", include_pulled=None):
            if isinstance(q, dict) and any(f.get("column_id") == "has_abstract" for f in q.get("filter_rows", [])):
                # 1 filter row + has_abstract = single block; >2 = final
                n_leaves = len(q["filter_rows"]) - 1
                return {"oqo": q, "oql": "no_abs", "rows": [], "count": 0 if n_leaves >= 4 else 1}
            return real_run(q, size, fields, include_pulled)
        with patch.object(self.api, "run", side_effect=fake_run):
            ev = self.call("eval", "--abstract-stats")
            self.assertEqual(ev["abstract_stats"]["final"]["pct_without_abstract"], 0.0)
            self.assertEqual(ev["abstract_stats"]["blocks"]["block/delivery"]["pct_without_abstract"], 50.0)
            self.assertIn("disproportionately dropping", ev["abstract_stats"]["attrition_note"])
            self.attach_critique()
            deliver = self.call("report")
        audit = (Path(deliver["report"]) / "audit.md").read_text()
        self.assertIn("## Missing-abstract attrition", audit)
        self.assertIn("disproportionately dropping", audit)


class StrategyTests(unittest.TestCase):
    def block(self, terms, role="search", field="title-abstract", id="a"):
        return {"id": id, "role": role, "field": field, "terms": terms, "rationale": "r"}

    def test_has_abstract_and_topic_filters(self):
        s = {
            "combine": "(a and b) or fallback",
            "blocks": [
                self.block(["telemedicine"]),
                self.block(["rural"], id="b"),
                {"id": "fallback", "role": "search", "field": "title", "terms": ["telemedicine"],
                 "topics": ["T10912"], "has_abstract": False, "rationale": "No-abstract fallback"},
            ],
            "limits": {"has_abstract": False, "topic": ["https://openalex.org/t10102", "T10912"]},
        }
        check(s)
        q, n = final_query(s)
        self.assertIn("(title has (telemedicine) and topic is T10912 and it doesn't have an abstract)", q)
        self.assertIn("topic is (T10102 or T10912)", q)
        self.assertEqual(n, 8)  # 1 + 1 + (1+1+1) + (1+2)
        with self.assertRaises(Error):
            check({"blocks": [self.block(["a1"])], "limits": {"has_abstract": "false"}})
        with self.assertRaises(Error):
            check({"blocks": [self.block(["a1"])], "limits": {"topic": ["W123"]}})
        with self.assertRaises(Error):
            check({"blocks": [self.block(["a1"]), {"id": "x", "role": "exclude", "field": "title", "terms": ["vet"], "has_abstract": False, "rationale": "r"}]})

    def test_rescue_oql_forms(self):
        s = {"corpus": "all", "blocks": [self.block(["telemedicine"]), self.block(["rural"], id="b")],
             "limits": {"to_date": "2020-12-31"}}
        q_both = rescue_oql(s)
        self.assertIn("get works (all corpora) where it doesn't have an abstract", q_both)
        self.assertIn("(title-abstract has (telemedicine) or title-abstract has (rural))", q_both)
        self.assertIn("it's cited by a work in the set", q_both)
        self.assertIn("it cites a work in the set", q_both)
        self.assertIn("publication_date <= 2020-12-31", q_both)
        q_topic = rescue_oql(s, direction="cited-by", topics=["https://openalex.org/t10102", "T10912"])
        self.assertIn("topic is (T10102 or T10912)", q_topic)
        self.assertNotIn("it cites a work in the set", q_topic)
        with self.assertRaises(Error):
            rescue_oql(s, direction="invalid")
        with self.assertRaises(Error):
            rescue_oql(s, topics=["W123"])

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

    def test_rescue_nested_set_keeps_corpus(self):
        s = {"corpus": "all", "blocks": [self.block(["telemedicine"]), self.block(["rural"], id="b")]}
        q = rescue_oql(s, direction="cited-by")
        self.assertIn("it's cited by a work in the set (get works (all corpora) where", q)
        self.assertEqual(rescue_expectation(s, "cited-by")[0], 1 + 2 + 2)
        self.assertEqual(rescue_expectation(s, "both")[0], 1 + 2 + 4)
        self.assertIn("(get works where", rescue_oql({**s, "corpus": "core"}, direction="cited-by"))

    def test_translation_guard_descends_into_nested_sets(self):
        s = json.loads((ROOT / "examples/telehealth.json").read_text())
        count, words = rescue_expectation(s, "cited-by")
        self.assertEqual(leaves(rescue_oqo()["filter_rows"]), count)
        check_translation(rescue_oqo(), "core", count, words)
        # OpenAlex silently drops `(works (all corpora) where ...)`; the guard must notice.
        outer_all = dict(rescue_oqo(), corpus="all")
        with self.assertRaisesRegex(Error, r"nested set \(cited_by\) as core, not all"):
            check_translation(outer_all, "all", count, words)
        check_translation(dict(rescue_oqo(nested_corpus="all"), corpus="all"), "all", count, words)

    def test_translation_guard_catches_compensated_drop(self):
        s = json.loads((ROOT / "examples/telehealth.json").read_text())
        n, words = final_query(s)[1], expected_tokens(core(s), s.get("limits"), excluded(s))
        swapped = {"get_rows": "works", "filter_rows": [search(t) for t in ("telemedicine", "remote", "rural", "remote")]}
        with self.assertRaisesRegex(Error, "lacks these words from the strategy: telehealth"):
            check_translation(swapped, "core", n, words)

    def test_tokens_survive_openalex_rewrites(self):
        # Live representations (2026-11-25) of a stemmed phrase, proximity and a hyphenated word.
        b = self.block(['stemmed "remote care"', {"near": ["video", "consultation"], "within": 3},
                        "Tele-health", "Telesalud"])
        rows = [search('"remote care"'), search('"video"~3~"consultation"'), search("Tele-health"), search("telesalud")]
        check_translation({"filter_rows": rows}, "core", 4, expected_tokens([b]))
        lim = {"from_date": "2010-01-01", "language": ["en"], "has_abstract": False, "topic": ["T10102"]}
        live = [{"column_id": "from_publication_date", "value": "2010-01-01"}, {"column_id": "language", "value": "en"},
                {"column_id": "has_abstract", "value": False}, {"column_id": "primary_topic.id", "value": "T10102"}]
        check_translation({"filter_rows": live}, "core", 4, expected_tokens([], lim))
        self.assertEqual(tokens("Café-Society"), ["cafe", "society"])

    def test_lint_warns_hyphenated_phrases(self):
        s = {"blocks": [self.block(['"open-field"', 'open-field', 'stemmed "risk-prone"', '"open-field test"', '"COVID-19"'])]}
        codes = [(w["code"], w["term"]) for w in lint(s)["warnings"] if w["code"] in ("hyphenated_phrase", "unquoted_hyphen")]
        self.assertEqual(codes, [("hyphenated_phrase", '"open-field"'), ("unquoted_hyphen", "open-field"),
                                 ("hyphenated_phrase", '"COVID-19"')])
        self.assertIn('Write "open field" for the phrase', lint(s)["warnings"][0]["message"])

    def test_chapman_estimate_and_routes(self):
        from oasb.capture import chapman, route_of
        estimate, ci = chapman(10, 10, 5)
        self.assertAlmostEqual(estimate, 121 / 6 - 1)                       # (n1+1)(n2+1)/(m+1) - 1
        variance = 11 * 11 * 5 * 5 / (6 ** 2 * 7)
        self.assertAlmostEqual(ci[1], estimate + 1.959964 * variance ** 0.5)
        self.assertEqual(ci[0], 15)                                         # floored at the 15 works observed
        self.assertEqual([route_of(b) for b in (
            {"route": "sample"}, {"route": "sample-added", "route_kind": "text"}, {"route": "neighbors", "direction": "related"},
            {"route": "neighbors", "direction": "citing"}, {"route_kind": "citation"}, {"route_kind": "text-similarity"})],
            ["text", "text", "text", "citation", "citation", "text"])

    def test_wilson_interval(self):
        self.assertEqual(wilson(3, 3), [0.4385, 1.0])
        self.assertEqual(wilson(0, 3), [0.0, 0.5615])
        self.assertIsNone(wilson(0, 0))

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

    def test_warning_field_order_is_canonicalized(self):
        w1 = {"type": "url_not_representable", "location": None,
              "message": "OR across different fields cannot be expressed in URL format. Fields involved: a.search.exact, a.search"}
        w2 = {"type": "url_not_representable", "location": None,
              "message": "OR across different fields cannot be expressed in URL format. Fields involved: a.search, a.search.exact"}
        w3 = {"type": "url_not_representable", "location": None,
              "message": "OR across different fields cannot be expressed in URL format. Fields involved: b.search, a.search"}
        self.assertEqual(normalize_warnings([w1]), normalize_warnings([w2]))
        self.assertNotEqual(normalize_warnings([w1]), normalize_warnings([w3]))
        self.assertEqual(normalize_warnings([w1, w3]), normalize_warnings([w3, w2]))

    def test_vocabulary_keywords_and_topics(self):
        api = API()
        kw_reply = {"meta": {"count": 1}, "results": [{
            "id": "https://openalex.org/keywords/telemedicine", "display_name": "telemedicine",
            "description": "Remote care", "display_name_alternatives": ["telehealth", "remote clinical care"],
            "works_count": 1234, "primary_topic": {"display_name": "Telemedicine and Telehealth Implementation"},
        }]}
        topic_reply = {"meta": {"count": 1}, "results": [{
            "id": "https://openalex.org/T10912", "display_name": "Telemedicine and Telehealth Implementation",
            "description": "Cluster", "keywords": [{"id": "https://openalex.org/keywords/telemedicine", "display_name": "Telemedicine", "score": 0.5}, "Virtual Care"],
            "domain": {"display_name": "Health Sciences"}, "field": {"display_name": "Medicine"},
            "subfield": {"display_name": "Health Informatics"}, "works_count": 5678,
        }]}
        with patch.object(api, "request", return_value=kw_reply) as req:
            out = api.vocabulary("keywords", "telemedicine", size=5)
            self.assertEqual(req.call_args.args[0], "/keywords")
            self.assertEqual(req.call_args.kwargs["params"]["per-page"], 5)
            self.assertEqual(out["results"][0]["id"], "keywords/telemedicine")
            self.assertEqual(out["results"][0]["alternatives"], ["telehealth", "remote clinical care"])
            self.assertEqual(out["results"][0]["primary_topic"], "Telemedicine and Telehealth Implementation")
        with patch.object(api, "request", return_value=topic_reply) as req:
            out = api.vocabulary("topics", "telemedicine")
            self.assertEqual(req.call_args.args[0], "/topics")
            self.assertEqual(out["results"][0]["id"], "T10912")
            self.assertEqual(out["results"][0]["keywords"], ["Telemedicine", "Virtual Care"])
            self.assertEqual(out["results"][0]["subfield"], "Health Informatics")
        for bad in (("concepts", "telemedicine", 10), ("keywords", "   ", 10), ("keywords", "telemedicine", 0), ("keywords", "telemedicine", 51)):
            with self.assertRaises(Error):
                api.vocabulary(*bad)

    def test_identifiers(self):
        for raw, key in [("https://doi.org/10.1234/A(B)", "doi:10.1234/a(b)"),
                         ("https://pubmed.ncbi.nlm.nih.gov/1234/", "pmid:1234"),
                         ("https://openalex.org/w123", "W123"),
                         ("PMC1420376", "pmcid:PMC1420376"), ("pmc1420376", "pmcid:PMC1420376"),
                         ("pmcid: 1420376", "pmcid:PMC1420376"),
                         ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1420376/", "pmcid:PMC1420376"),
                         ("https://pmc.ncbi.nlm.nih.gov/articles/PMC1420376/", "pmcid:PMC1420376"),
                         ("1420376", "pmid:1420376")]:                  # bare digits stay a PMID
            self.assertEqual(identifier(raw), key)
        with self.assertRaises(Error):
            identifier("unknown")

    def test_membership_batches_and_consistency(self):
        api = API()
        def reply(query, size, include_pulled=None):
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

    def test_every_execution_states_the_pulled_view(self):
        """An ID filter brings pulled works back unless include_pulled is explicit (live, 2026-11-25)."""
        api, sent = API(), []
        def fake_request(path, params=None, body=None, allow_missing=False):
            sent.append((params or {}).get("include_pulled"))
            filters = (body or {}).get("oqo", {}).get("filter_rows") or json.loads((params or {}).get("oqo", "{}")).get("filter_rows", [])
            ids = [f["value"] for f in filters[-1].get("filters", [filters[-1]])] if filters else []
            rows = [{"id": i} for i in ids if i.startswith("W")]
            return {"meta": {"count": len(rows), "next_cursor": None, "x_query": {"oqo": (body or {}).get("oqo") or {"x": 1}, "oql": "q"}}, "results": rows}
        with patch.object(api, "request", side_effect=fake_request):
            api.run("get works", 1)
            api.run({"get_rows": "works", "filter_rows": [{"column_id": "t", "value": "x" * 6000}]}, 1)   # POST
            api.members({"get_rows": "works"}, ["W1"])
            api.page({"get_rows": "works"})
            self.assertEqual(sent, ["false"] * 4)
            self.assertEqual(api.pulled(["W1"]), {"W1"})
            self.assertEqual(sent[-1], "true")      # detection must see pulled works
            api.include_pulled = True
            api.members({"get_rows": "works"}, ["W1"])
            self.assertEqual(sent[-1], "true")

    def test_search_timeouts_are_retried_but_refusals_are_not(self):
        def err(code, body):
            return HTTPError("https://api.openalex.org", code, "x", {}, io.BytesIO(body))
        timeout = b'{"validation":{"errors":[{"type":"es_error","message":"Elasticsearch error: Connection timed out"}]}}'
        ok = MagicMock()
        ok.__enter__.return_value = ok
        ok.read.return_value = b'{"ok":true}'
        ok.headers, ok.status = {}, 200
        with patch("oasb.api.urlopen", side_effect=[err(500, timeout), err(500, timeout), ok]) as req, \
                patch("oasb.api.time.sleep") as nap:
            self.assertEqual(API().request("/"), {"ok": True})
            self.assertEqual(req.call_count, 3)
            self.assertEqual([c.args[0] for c in nap.call_args_list], [2, 4])
        with patch("oasb.api.urlopen", side_effect=[err(500, timeout) for _ in range(4)]) as req, patch("oasb.api.time.sleep"):
            with self.assertRaisesRegex(Error, "es_error"):
                API().request("/")
            self.assertEqual(req.call_count, 4)
        for code, body in [(400, b'{"error":"query_too_slow"}'), (500, b'{"error":"internal"}')]:
            with patch("oasb.api.urlopen", side_effect=err(code, body)) as req, patch("oasb.api.time.sleep"):
                with self.assertRaises(Error):
                    API().request("/")
                self.assertEqual(req.call_count, 1)

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

    def test_benchmark_scores_rescue_union(self):
        import zlib
        case = bench.load_case("Bos_2018")
        bound = {"column_id": "to_publication_date", "value": case["to_date"]}
        final = {"get_rows": "works", "filter_rows": [bound], "tag": "final"}
        rq = {"get_rows": "works", "filter_rows": [bound], "tag": "rescue"}
        class FakeBench:
            include_pulled, cost_usd, unpriced = False, 0.0, 0
            def validate(self, q): return {"oqo": final}
            def run(self, obj, *a, **k): return {"count": 100 if obj.get("tag") == "final" else 7, "oql": "q"}
            def resolve(self, raw): return identifier(raw), {"id": f"https://openalex.org/W{zlib.crc32(raw.encode()) + 1}"}
            def pulled(self, ids): return set()
            def members(self, obj, ids, include_pulled=None):
                ordered = sorted(ids)
                return {"final": set(ordered[:3]), "rescue": set(ordered[2:5])}.get(obj.get("tag"), set(ids))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "bos"
            bench.prepare(case, "seedless", folder)
            report = folder / "run/reports/R1"
            (report / "rescue").mkdir(parents=True)
            write(report / "validation.json", {"evaluation": {"final": {"oql": "FINAL", "oqo": final},
                                                              "strategy": {"blocks": [], "limits": {}}}, "records": {}})
            (report / "final-query.oql").write_text("FINAL\n")
            write(report / "rescue/rescue-manifest.json", {"queries": [{"direction": "cited-by", "oql": "RQ", "oqo": rq, "count": 7}]})
            (report / "rescue/rescue-query-cited-by.oql").write_text("RQ\n")
            write(report / "rescue/rescue-query-cited-by.json", rq)
            plain = bench.score(case, folder, FakeBench(), report)
            self.assertNotIn("rescue", plain)
            row = bench.score(case, folder, FakeBench(), report, with_rescue=True)
            r = row["rescue"]
            self.assertEqual((row["hits"], r["rescue_hits"], r["added_hits"], r["union_hits"]), (3, 3, 2, 5))
            self.assertEqual(r["union_relative_recall"], 5 / row["tested"])
            self.assertEqual(r["union_relative_recall_ci95"], wilson(5, row["tested"]))
            self.assertEqual((r["counts"], r["combined_screening_upper_bound"]), ({"cited-by": 7}, 107))
            with self.assertRaisesRegex(Error, "pass --report"):
                bench.score(case, folder, FakeBench(), None, with_rescue=True)
            write(report / "rescue/rescue-query-cited-by.json", final)        # tampered rescue file
            with self.assertRaisesRegex(Error, "differ from the rescue manifest"):
                bench.score(case, folder, FakeBench(), report, with_rescue=True)

    def test_benchmark_explains_misses(self):
        import zlib
        case = bench.load_case("Bos_2018")
        bound = {"column_id": "to_publication_date", "value": case["to_date"]}
        final = {"get_rows": "works", "filter_rows": [bound], "tag": "final"}
        def line(tag): return {"oqo": {"get_rows": "works", "filter_rows": [bound], "tag": tag}}
        state = {}
        class FakeBench:
            include_pulled, cost_usd, unpriced = False, 0.0, 0
            def validate(self, q): return {"oqo": final}
            def run(self, obj, *a, **k): return {"count": 100, "oql": "q"}
            def resolve(self, raw):
                item = {"id": f"https://openalex.org/W{zlib.crc32(raw.encode()) + 1}", "display_name": raw}
                if zlib.crc32(raw.encode()) % 2:
                    item["abstract_inverted_index"] = {"text": [0]}
                return identifier(raw), item
            def pulled(self, ids): return set()
            def members(self, obj, ids, include_pulled=None):
                if obj.get("tag") is None:
                    return set(ids)                                   # the date-window check
                order = state.setdefault("order", sorted(ids))
                pick = {"final": order[:3], "a": order[:6], "b": order[:3] + order[6:8], "x": order[4:5],
                        "lim": [w for w in order if w != order[8]]}.get(obj.get("tag"))
                return set(ids) if pick is None else set(ids) & set(pick)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "bos"
            bench.prepare(case, "seedless", folder)
            report = folder / "run/reports/R1"
            report.mkdir(parents=True)
            strategy = {"blocks": [{"id": "a", "role": "search"}, {"id": "b", "role": "search"},
                                   {"id": "x", "role": "exclude"}], "limits": {"to_date": case["to_date"]}}
            write(report / "validation.json", {"evaluation": {"final": {"oql": "FINAL", "oqo": final}, "strategy": strategy,
                                                              "lines": {"block/a": line("a"), "block/b": line("b"),
                                                                        "exclude/x": line("x"), "limit/to_date": line("lim")}},
                                               "records": {}})
            (report / "final-query.oql").write_text("FINAL\n")
            row = bench.score(case, folder, FakeBench(), report, explain=True)
        order, ex = state["order"], row["explain"]
        by = {w["work"]: w for w in ex["works"]}
        self.assertEqual(ex["misses"], row["tested"] - row["hits"])
        self.assertEqual((by[order[3]]["failed_blocks"], by[order[3]]["excluded_by"]), (["b"], []))
        self.assertEqual(by[order[4]]["excluded_by"], ["x"])
        self.assertEqual(by[order[6]]["failed_blocks"], ["a"])
        self.assertEqual((by[order[8]]["failed_blocks"], by[order[8]]["outside_limits"]), (["a", "b"], ["to_date"]))
        self.assertEqual(ex["causes"]["block b"], sum("b" in w["failed_blocks"] for w in ex["works"]))
        self.assertEqual(ex["without_abstract"], sum(not w["has_abstract"] for w in ex["works"]))
        self.assertTrue(0 < ex["without_abstract"] < ex["misses"])
        with self.assertRaisesRegex(Error, "pass --report"):
            bench.explain_misses(None, FakeBench(), ["W1"], {})

    def test_all_benchmark_fixtures_valid_and_leak_free(self):
        from datetime import date
        idx = bench.fixture_index()
        self.assertGreaterEqual(len(idx), 32)
        listing = bench.list_cases()
        self.assertEqual(listing["count"], len(idx))
        self.assertTrue(all("original_screening_load" in row for row in listing["fixtures"]))
        self.assertEqual(bench.list_cases("frozen")["count"], 10)
        with tempfile.TemporaryDirectory() as temp:
            for cid, path in idx.items():
                case = bench.load_case(cid)
                self.assertEqual(case["id"], cid)
                date.fromisoformat(case["to_date"])
                self.assertTrue(case["question"].strip())
                self.assertTrue(case["gold"])
                self.assertIn("original_screening_load", case)
                load = case["original_screening_load"]
                self.assertTrue(load is None or (isinstance(load, int) and not isinstance(load, bool) and load > 0), cid)
                self.assertIsInstance(case.get("original_screening_load_source"), str)
                self.assertTrue(case["original_screening_load_source"].strip(), cid)
                # Every non-empty identifier in gold and seeds must parse cleanly
                for row in case["gold"]:
                    for raw in row["ids"]:
                        identifier(raw)
                for s in case.get("seeds") or []:
                    identifier(s)
                # Spot-check prepare isolation on one fixture per suite
                folder = Path(temp) / cid
                bench.prepare(case, "seeded", folder)
                given = json.loads((folder / "intake.json").read_text())
                self.assertNotIn("gold", given)
                self.assertNotIn("original_screening_load", given)
                self.assertNotIn("original_screening_load_source", given)
                self.assertFalse((folder / "skill/evals").exists())
                self.assertEqual(given["seeds"], bench.assignment(case, "seeded"))


if __name__ == "__main__":
    unittest.main()

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
from oasb.store import Run, write
from oasb.strategy import check
import re
from oasb.export import export
from oasb.strategy import core, excluded, expected_tokens, final_query, parse_combine, failing, rescue_expectation, rescue_oql
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
        bounded = self.call("neighbors", "W1", "--direction", "related", "--all", "--max-pages", "2")
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

    def test_lint_and_draft_offline(self):
        # Strategy exercising every local lint heuristic
        s = {
            "corpus": "all",
            "blocks": [
                {"id": "a", "role": "search", "field": "title-abstract",
                 "terms": ["source-content", "telemedicine", "Telemedicine", "ab*", 'stemmed "bar*"'], "rationale": ""},
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
                                     "short_wildcard", "stemmed_wildcard", "many_and_blocks", "exclude_blocks"})
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
            out = self.call("rescue", "--direction", "cited-by")
        self.assertEqual(out["report"], report.name)
        self.assertEqual(out["gate"], "any-block")
        self.assertEqual(out["rescue_count"], 2)
        self.assertEqual(out["development_rescued"], ["W2"])
        self.assertNotIn("W3", out["development_rescued"])  # protected record not leaked
        rdir = Path(out["rescue_dir"])
        self.assertTrue((rdir / "rescue-query.oql").is_file())
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
                self.call("rescue", "--direction", "cited-by")

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
        with self.assertRaisesRegex(Error, "Confirm the current concepts"):
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
                         ("https://openalex.org/w123", "W123")]:
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

    def test_all_benchmark_fixtures_valid_and_leak_free(self):
        from datetime import date
        idx = bench.fixture_index()
        self.assertGreaterEqual(len(idx), 32)
        listing = bench.list_cases()
        self.assertEqual(listing["count"], len(idx))
        self.assertEqual(bench.list_cases("frozen")["count"], 10)
        with tempfile.TemporaryDirectory() as temp:
            for cid, path in idx.items():
                case = bench.load_case(cid)
                self.assertEqual(case["id"], cid)
                date.fromisoformat(case["to_date"])
                self.assertTrue(case["question"].strip())
                self.assertTrue(case["gold"])
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
                self.assertFalse((folder / "skill/evals").exists())
                self.assertEqual(given["seeds"], bench.assignment(case, "seeded"))


if __name__ == "__main__":
    unittest.main()

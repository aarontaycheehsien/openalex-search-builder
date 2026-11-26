"""Prepare blind runs and score frozen deliveries against published inclusion labels."""
import argparse
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "evals/fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
from oasb.api import API, PULLED_VIEWS, Error, identifier, normalize_query, now, work_id
from oasb.store import Run, digest, write
from oasb.export import rescue_queries
from oasb.capture import capture_recapture
from oasb.records import wilson
from oasb.strategy import failing, parse_combine, pulled_view
from collections import Counter


def fixture_index():
    """Map fixture ID -> Path across evals/fixtures/**/*.json."""
    index = {}
    for path in sorted(FIXTURES_DIR.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cid = data.get("id") or path.stem
        if cid in index:
            raise Error(f"Duplicate fixture ID {cid!r}: {index[cid]} and {path}")
        index[cid] = path
    return index


def load_case(case_id):
    index = fixture_index()
    if case_id not in index:
        raise Error(f"Unknown fixture {case_id!r}. Run `python evals/bench.py list` to see available cases.")
    return json.loads(index[case_id].read_text(encoding="utf-8"))


# Preserved for backward compatibility with reference_runs.py; all fixtures are discovered dynamically.
CASES = ("Bos_2018", "Anmarkrud_2021")


def assignment(case, condition):
    if condition != "seeded":
        return []
    if case.get("seeds"):
        return sorted({identifier(s) for s in case["seeds"]})
    ids = sorted({identifier(row["ids"][0]) for row in case["gold"] if row["ids"]})
    return sorted(random.Random(42).sample(ids, min(3, len(ids))))


def prepare(case, condition, folder):
    folder = folder.resolve()
    if folder.is_relative_to(ROOT):
        raise Error("Prepare the build outside the repository so the answer key stays separate.")
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copytree(ROOT / "scripts", folder / "skill/scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "SKILL.md", folder / "skill/SKILL.md")
    shutil.copytree(ROOT / "examples", folder / "skill/examples")
    given = assignment(case, condition)
    elig = case.get("eligibility") or []
    elig_list = [elig] if isinstance(elig, str) else list(elig)
    prompt = {"question": case["question"], "eligibility": case.get("eligibility", ""),
              "to_date": case["to_date"], "condition": condition, "seeds": given,
              "fixture_hash": digest(case)}
    write(folder / "intake.json", prompt)
    run = Run(folder / "run")
    with run.locked():
        run.init(case["question"], elig_list, "standard")
        run.put("strategy", {"blocks": [], "limits": {"to_date": case["to_date"]}})
    return {"directory": str(folder), "supplied_seeds": given,
            "task": "Use skill/SKILL.md and intake.json. Preserve the publication cutoff. Avoid looking up the source review or its included list in benchmark mode."}


def check_limit(oqo, cutoff):
    bound = {"column_id": "to_publication_date", "value": cutoff}
    if bound not in oqo.get("filter_rows", []):
        raise Error("The reported strategy must apply the fixture publication cutoff.")


def screening_vs_gold(run, gold, titles):
    """Evaluator-only comparison of the agent's discovery and screening with the gold set: how many gold works
    discovery surfaced, how the agent decided on those it screened (an include anywhere counts), and how many
    of the works it judged eligible are gold. Gold sets can be incomplete, so a low last share does not by
    itself mean the agent over-included."""
    folder, path = run.root / "discovery", run.root / "screening.json"
    if not path.is_file():
        return None
    seen = set()
    for p in folder.glob("*.json") if folder.is_dir() else []:
        batch = json.loads(p.read_text(encoding="utf-8"))
        seen |= {r["id"] for r in batch.get("rows", [])} | set(batch.get("protected_hits", []))
    decisions = {}
    for e in json.loads(path.read_text(encoding="utf-8")).values():
        decisions.setdefault(e["work_id"], []).append(e)
    final = {w: next((e for e in es if e["decision"] == "include"), es[-1]) for w, es in decisions.items()}
    eligible = {w for w, e in final.items() if e["decision"] == "include"}
    screened_gold = {w: e for w, e in final.items() if w in gold}
    tally = Counter(e["decision"] for e in screened_gold.values())
    return {"gold": len(gold), "gold_seen_in_discovery": len(seen & gold), "gold_screened": len(screened_gold),
            "gold_included": tally["include"], "gold_unclear": tally["unclear"], "gold_excluded": tally["exclude"],
            "agent_eligible": len(eligible), "agent_eligible_gold": len(eligible & gold),
            "gold_not_included": [{"work": w, "title": titles.get(w), "decision": e["decision"], "reason": e["reason"]}
                                  for w, e in sorted(screened_gold.items()) if e["decision"] != "include"]}


def explain_misses(archive, api, misses, items):
    """Evaluator-only diagnosis of missed gold works from the delivery's archived search lines: the blocks
    each fails (honouring combine), exclusions that removed it, limits it falls outside, and whether
    OpenAlex holds an abstract. Never written into the build workspace."""
    if archive is None:
        raise Error("--explain diagnoses a delivered report; pass --report.")
    if not misses:
        return {"misses": 0, "works": []}
    strategy, lines = archive["evaluation"]["strategy"], archive["evaluation"]["lines"]
    blocks = [b["id"] for b in strategy["blocks"] if b["role"] == "search"]
    tree = parse_combine(strategy["combine"], blocks) if "combine" in strategy else None
    hits = {name: api.members(line["oqo"], misses) for name, line in lines.items()
            if name.startswith(("block/", "exclude/", "limit/"))}
    works, causes = [], Counter()
    for wid in misses:
        values = {b: wid in hits["block/" + b] for b in blocks}
        failed = failing(tree, values) if tree else [b for b, ok in values.items() if not ok]
        excluded = [n[8:] for n, h in hits.items() if n.startswith("exclude/") and wid in h]
        outside = [n[6:] for n, h in hits.items() if n.startswith("limit/") and wid not in h]
        item = items.get(wid) or {}
        has_abstract = bool(item.get("abstract_inverted_index"))
        works.append({"work": wid, "title": item.get("display_name"), "year": item.get("publication_year"),
                      "has_abstract": has_abstract, "failed_blocks": failed, "excluded_by": excluded,
                      "outside_limits": outside})
        causes.update(f"block {b}" for b in failed)
        causes.update(f"exclusion {e}" for e in excluded)
        causes.update(f"limit {l}" for l in outside)
        if not (failed or excluded or outside):
            causes["matched every line"] += 1   # index drift since delivery, or a pulled work
    return {"misses": len(misses), "without_abstract": sum(not w["has_abstract"] for w in works),
            "causes": dict(causes.most_common()), "works": works}


def score(case, folder, api, report_dir=None, reference=False, with_rescue=False, explain=False):
    intake = json.loads((folder / "intake.json").read_text())
    given = assignment(case, intake["condition"])
    if intake["fixture_hash"] != digest(case) or given != intake["seeds"]:
        raise Error("Fixture or seed assignment changed after preparation.")
    run = Run(folder / "run")
    mined = set()
    view = "exclude"   # comparator queries use OpenAlex's default view
    archive = None
    if report_dir:
        if report_dir.resolve().parent != (run.root / "reports").resolve():
            raise Error("Select a delivery from the prepared workspace.")
        archive = json.loads((report_dir / "validation.json").read_text())
        frozen = archive["evaluation"]["final"]
        if (report_dir / "final-query.oql").read_text().rstrip("\n") != frozen["oql"]:
            raise Error("The archived query text does not match the delivery.")
        canonical = api.validate(frozen["oql"])
        if normalize_query(canonical["oqo"]) != normalize_query(frozen["oqo"]):
            raise Error("The canonical query changed after delivery.")
        obj = frozen["oqo"]
        view = pulled_view(archive["evaluation"]["strategy"])
        mined = {k for k, r in archive["records"].items() if r["role"] in {"seed", "relevant"}}
    else:
        key = "reference" if reference else "baseline"
        candidate = case.get(key)
        if not candidate:
            raise Error(f"Fixture {case['id']!r} does not define a {key} query.")
        obj = api.validate(candidate)["oqo"]
        obj = dict(obj, filter_rows=[*obj["filter_rows"], {"column_id": "to_publication_date", "value": case["to_date"]}])
    check_limit(obj, case["to_date"])
    api.include_pulled = PULLED_VIEWS[view]
    result = api.run(obj)
    known, seed_works, items = set(), set(), {}
    unresolved = []
    for row in case["gold"]:
        item = None
        for raw in row["ids"]:
            _, item = api.resolve(raw)
            if item:
                break
        if item is None:
            unresolved.append(row["row"])
            continue
        wid = work_id(item)
        items[wid] = item
        if any(identifier(a) in given for a in row["ids"]):
            seed_works.add(wid)
        known.add(wid)
    # Only indexed records published by the stated cutoff belong in conditional recall.
    dated = api.members({"get_rows": "works", "filter_rows": [
        {"column_id": "to_publication_date", "value": case["to_date"]}]}, known, include_pulled=True)
    excluded = sorted(known - dated)
    # Under the default view a pulled gold work cannot be retrieved: report it, don't score it.
    pulled_gold = api.pulled(dated)
    unretrievable = (dated - seed_works) & pulled_gold if view == "exclude" else set()
    test = dated - seed_works - unretrievable
    hits = api.members(obj, test)
    unseen = test - mined
    explanation = explain_misses(archive, api, sorted(test - hits), items) if explain else None
    rescue = None
    if with_rescue:
        if not report_dir:
            raise Error("--with-rescue scores a delivered report's saved rescue queries; pass --report.")
        parts = rescue_queries(report_dir)   # checked against the rescue manifest
        found, counts = set(), {}
        for q in parts:
            if "ids" in q:   # reference-list rescue: an ID list already restricted to the date window
                counts[q["label"]] = len(q["ids"])
                found |= set(q["ids"]) & test
                continue
            check_limit(q["oqo"], case["to_date"])
            counts[q["label"]] = api.run(q["oqo"])["count"]
            found |= api.members(q["oqo"], test)
        union = hits | found
        rescue = {"directions": [q["label"] for q in parts], "counts": counts,
                  "rescue_hits": len(found), "added_hits": len(found - hits), "union_hits": len(union),
                  "union_relative_recall": len(union) / len(test) if test else None,
                  "union_relative_recall_ci95": wilson(len(union), len(test)),
                  "unseen_union_hits": len(union & unseen),
                  "unseen_union_recall": len(union & unseen) / len(unseen) if unseen else None,
                  "unseen_union_recall_ci95": wilson(len(union & unseen), len(unseen)),
                  "combined_screening_upper_bound": result["count"] + sum(counts.values()),
                  "note": "Screening volume is an upper bound: rescue records can overlap the delivered search and "
                          "each other. export --rescue gives the deduplicated count."}
    costs = []
    log = run.root / "requests.jsonl"
    if report_dir and log.exists():
        costs = [json.loads(s) for s in log.read_text().splitlines() if s]
    orig_load = case.get("original_screening_load")
    return {"case": case["id"], "suite": case.get("suite", "core"), "condition": intake["condition"],
            "mode": "delivered" if report_dir else ("reference" if reference else "baseline"),
            "at": now(), "oql": result["oql"], "cutoff": case["to_date"], "gold_rows": len(case["gold"]),
            "distinct_resolved": len(known), "unresolved_source_rows": unresolved,
            "outside_date_bound": len(excluded), "resolved_seed_works_excluded": len(dated & seed_works),
            "pulled_view": view, "pulled_gold": len(pulled_gold),
            "pulled_gold_excluded_from_test": len(unretrievable),
            "relative_recall_counting_pulled_as_missed": len(hits)/len(test | unretrievable) if test | unretrievable else None,
            "tested": len(test), "hits": len(hits), "relative_recall": len(hits)/len(test) if test else None,
            "relative_recall_ci95": wilson(len(hits), len(test)),
            "unseen_tested": len(unseen), "unseen_hits": len(hits & unseen),
            "unseen_recall": len(hits & unseen)/len(unseen) if unseen else None,
            "unseen_recall_ci95": wilson(len(hits & unseen), len(unseen)),
            "screening_count": result["count"], "original_screening_load": orig_load,
            "workload_ratio": round(result["count"] / orig_load, 3) if orig_load else None,
            "works_per_test_hit": result["count"]/len(hits) if hits else None,
            "build_usd_reported": round(sum(float(e.get("cost_usd") or 0) for e in costs), 6),
            "scoring_usd_reported": round(api.cost_usd, 6), "scoring_responses_without_cost": api.unpriced,
            **({"rescue": rescue} if rescue is not None else {}),
            **({"explain": explanation} if explanation is not None else {}),
            **({"capture_recapture": dict(capture_recapture(run), gold_in_window=len(dated))} if report_dir else {}),
            **({"screening": screen} if (screen := screening_vs_gold(
                run, dated, {w: (items.get(w) or {}).get("display_name") for w in dated})) is not None else {})}


def list_cases(suite=None):
    rows = []
    for cid, path in sorted(fixture_index().items()):
        c = json.loads(path.read_text(encoding="utf-8"))
        s = c.get("suite", "core")
        if suite and s != suite:
            continue
        rows.append({
            "id": cid, "suite": s, "to_date": c["to_date"],
            "gold_rows": len(c["gold"]),
            "gold_with_ids": sum(bool(r["ids"]) for r in c["gold"]),
            "original_screening_load": c.get("original_screening_load"),
            "explicit_seeds": len(c.get("seeds") or []),
            "has_baseline": bool(c.get("baseline")),
            "has_reference": bool(c.get("reference")),
        })
    return {"count": len(rows), "fixtures": rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["list", "prepare", "baseline", "reference", "score"])
    p.add_argument("case", nargs="?")
    p.add_argument("directory", nargs="?", type=Path)
    p.add_argument("--suite", help="Filter `list` by fixture suite (e.g. frozen, synergy, clef-tar-2018).")
    p.add_argument("--condition", choices=["seeded", "seedless"], default="seedless")
    p.add_argument("--report", type=Path)
    p.add_argument("--explain", action="store_true",
                   help="With score: list, for the evaluator only, why each missed gold work was missed.")
    p.add_argument("--with-rescue", action="store_true",
                   help="With score: also score the report's saved rescue queries (union recall, volume upper bound).")
    args = p.parse_args()
    if args.action == "list":
        return list_cases(args.suite)
    if not args.case or not args.directory:
        p.error(f"{args.action} requires <case> and <directory>")
    case = load_case(args.case)
    if args.action == "prepare":
        return prepare(case, args.condition, args.directory)
    if args.action == "score" and not args.report:
        p.error("score requires --report pointing to the delivered report directory")
    return score(case, args.directory, API(), args.report if args.action == "score" else None,
                 reference=args.action == "reference", with_rescue=args.with_rescue, explain=args.explain)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2))
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))

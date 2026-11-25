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
from oasb.api import API, Error, identifier, normalize_query, now, work_id
from oasb.store import Run, digest, write
from oasb.records import wilson


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


def score(case, folder, api, report_dir=None, reference=False):
    intake = json.loads((folder / "intake.json").read_text())
    given = assignment(case, intake["condition"])
    if intake["fixture_hash"] != digest(case) or given != intake["seeds"]:
        raise Error("Fixture or seed assignment changed after preparation.")
    run = Run(folder / "run")
    mined = set()
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
        mined = {k for k, r in archive["records"].items() if r["role"] in {"seed", "relevant"}}
    else:
        key = "reference" if reference else "baseline"
        candidate = case.get(key)
        if not candidate:
            raise Error(f"Fixture {case['id']!r} does not define a {key} query.")
        obj = api.validate(candidate)["oqo"]
        obj = dict(obj, filter_rows=[*obj["filter_rows"], {"column_id": "to_publication_date", "value": case["to_date"]}])
    check_limit(obj, case["to_date"])
    result = api.run(obj)
    known, seed_works = set(), set()
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
        if any(identifier(a) in given for a in row["ids"]):
            seed_works.add(wid)
        known.add(wid)
    # Only indexed records published by the stated cutoff belong in conditional recall.
    dated = api.members({"get_rows": "works", "filter_rows": [
        {"column_id": "to_publication_date", "value": case["to_date"]}]}, known)
    excluded = sorted(known - dated)
    test = dated - seed_works
    hits = api.members(obj, test)
    unseen = test - mined
    costs = []
    log = run.root / "requests.jsonl"
    if report_dir and log.exists():
        costs = [json.loads(s) for s in log.read_text().splitlines() if s]
    return {"case": case["id"], "suite": case.get("suite", "core"), "condition": intake["condition"],
            "mode": "delivered" if report_dir else ("reference" if reference else "baseline"),
            "at": now(), "oql": result["oql"], "cutoff": case["to_date"], "gold_rows": len(case["gold"]),
            "distinct_resolved": len(known), "unresolved_source_rows": unresolved,
            "outside_date_bound": len(excluded), "resolved_seed_works_excluded": len(dated & seed_works),
            "tested": len(test), "hits": len(hits), "relative_recall": len(hits)/len(test) if test else None,
            "relative_recall_ci95": wilson(len(hits), len(test)),
            "unseen_tested": len(unseen), "unseen_hits": len(hits & unseen),
            "unseen_recall": len(hits & unseen)/len(unseen) if unseen else None,
            "unseen_recall_ci95": wilson(len(hits & unseen), len(unseen)),
            "screening_count": result["count"], "works_per_test_hit": result["count"]/len(hits) if hits else None,
            "build_usd_reported": round(sum(float(e.get("cost_usd") or 0) for e in costs), 6),
            "scoring_usd_reported": round(api.cost_usd, 6), "scoring_responses_without_cost": api.unpriced}


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
    return score(case, args.directory, API(), args.report if args.action == "score" else None, reference=args.action == "reference")


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2))
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))

"""Prepare blind runs and score frozen deliveries against published inclusion labels."""
import argparse
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from oasb.api import API, Error, identifier, normalize_query, now, work_id
from oasb.store import Run, digest, write

CASES = ("Bos_2018", "Anmarkrud_2021")


def assignment(case, condition):
    ids = sorted({identifier(row["ids"][0]) for row in case["gold"] if row["ids"]})
    return sorted(random.Random(42).sample(ids, min(3, len(ids)))) if condition == "seeded" else []


def prepare(case, condition, folder):
    folder = folder.resolve()
    if folder.is_relative_to(ROOT):
        raise Error("Prepare the build outside the repository so the answer key stays separate.")
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copytree(ROOT / "scripts", folder / "skill/scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "SKILL.md", folder / "skill/SKILL.md")
    shutil.copytree(ROOT / "examples", folder / "skill/examples")
    given = assignment(case, condition)
    prompt = {k: case[k] for k in ("question", "eligibility", "to_date")}
    prompt.update(condition=condition, seeds=given, fixture_hash=digest(case))
    write(folder / "intake.json", prompt)
    run = Run(folder / "run")
    with run.locked():
        run.init(case["question"], [case["eligibility"]], "standard")
        run.put("strategy", {"blocks": [], "limits": {"to_date": case["to_date"]}})
    return {"directory": str(folder), "supplied_seeds": given, "task": "Use skill/SKILL.md and intake.json. Preserve the publication cutoff. Avoid looking up the source review or its included list in benchmark mode."}


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
        candidate = case["baseline"]
        if reference:
            candidate = case["reference"]
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
    return {"case": case["id"], "condition": intake["condition"], "mode": "delivered" if report_dir else ("reference" if reference else "baseline"),
            "at": now(), "oql": result["oql"], "cutoff": case["to_date"], "gold_rows": len(case["gold"]),
            "distinct_resolved": len(known), "unresolved_source_rows": unresolved,
            "outside_date_bound": len(excluded), "resolved_seed_works_excluded": len(dated & seed_works),
            "tested": len(test), "hits": len(hits), "relative_recall": len(hits)/len(test) if test else None,
            "unseen_tested": len(unseen), "unseen_hits": len(hits & unseen),
            "unseen_recall": len(hits & unseen)/len(unseen) if unseen else None,
            "screening_count": result["count"], "works_per_test_hit": result["count"]/len(hits) if hits else None,
            "build_usd_reported": round(sum(float(e.get("cost_usd") or 0) for e in costs), 6),
            "scoring_usd_reported": round(api.cost_usd, 6), "scoring_responses_without_cost": api.unpriced}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "baseline", "reference", "score"])
    p.add_argument("case", choices=CASES)
    p.add_argument("directory", type=Path)
    p.add_argument("--condition", choices=["seeded", "seedless"], default="seedless")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    case = json.loads((ROOT / "evals/fixtures" / (args.case + ".json")).read_text())
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

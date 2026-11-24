"""Command line interface for the OpenAlex search-builder skill."""
import argparse
import json
import sys
from pathlib import Path

from .api import API, Error, check_translation, now
from .evaluate import evaluate, report, review, save_eval
from .export import export
from .records import DEVELOPMENT, ROLES, add, discover, mining
from .store import Run, digest
from .strategy import check, final_query


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-w", "--workspace", required=True, type=Path)
    cmds = p.add_subparsers(dest="action", required=True)
    c = cmds.add_parser("init")
    c.add_argument("--question", required=True)
    c.add_argument("--eligibility", action="append", default=[])
    c.add_argument("--depth", choices=["quick", "standard", "thorough"], default="standard")
    c = cmds.add_parser("scope")
    c.add_argument("--confirm", action="store_true", help="Record the reviewer's scope decision.")
    c.add_argument("--note")
    c = cmds.add_parser("resolve")
    c.add_argument("identifiers", nargs="+")
    c.add_argument("--role", choices=ROLES, required=True)
    c.add_argument("--source", required=True)
    c = cmds.add_parser("develop", help="Move a protected work into development if it informed a revision.")
    c.add_argument("key")
    c.add_argument("--reason", required=True)
    cmds.add_parser("terms")
    cmds.add_parser("validate")
    cmds.add_parser("eval")
    c = cmds.add_parser("review")
    c.add_argument("file", type=Path, help="JSON critique, see SKILL.md")
    cmds.add_parser("report")
    c = cmds.add_parser("sample")
    c.add_argument("--oql", required=True)
    c.add_argument("--size", type=int, default=10)
    c.add_argument("--seed", type=int, default=42)
    c = cmds.add_parser("neighbors")
    c.add_argument("work_id")
    c.add_argument("--direction", choices=["citing", "references"], required=True)
    c.add_argument("--size", type=int, default=10)
    c = cmds.add_parser("screen")
    c.add_argument("discovery")
    c.add_argument("work_id")
    c.add_argument("decision", choices=["include", "exclude", "unclear"])
    c.add_argument("--reason", required=True)
    c = cmds.add_parser("export", help="Page the delivered query into resumable CSV, RIS and JSONL files.")
    c.add_argument("--report", help="Report ID under reports/ (default: the latest delivery).")
    c.add_argument("--max-pages", type=int, help="Stop after this many 100-record pages; rerun to resume.")
    return p


def miss_details(records, misses, dev):
    """Development-only context for each missed work. Protected records never appear here."""
    out = {}
    for wid, reasons in misses.items():
        if wid not in dev:
            continue
        work = records[wid]["work"] or {}
        out[wid] = {"title": work.get("display_name"), "year": work.get("publication_year"),
                    "has_abstract": bool(work.get("abstract_inverted_index")),
                    "keywords": [k.get("display_name") for k in work.get("keywords") or []],
                    "failed": reasons}
        if not out[wid]["has_abstract"]:
            out[wid]["note"] = "No abstract in OpenAlex: abstract-based blocks can only match the title (or keywords/full text)."
    return out


def dispatch(run, api, a):
    if a.action == "init":
        return run.init(a.question, a.eligibility, a.depth)
    run.get("protocol")
    if a.action == "scope":
        scope = run.scope_data()
        if a.confirm:
            if not a.note or not a.note.strip():
                raise Error("Record the reviewer's instruction using --note.")
            check(run.get("strategy"))
            run.put("scope", {"at": now(), "hash": digest(scope), "decision": a.note})
        return dict(scope, confirmed=a.confirm or (
            (run.root / "scope.json").exists() and run.get("scope")["hash"] == digest(scope)))
    if a.action == "validate":
        strategy = run.get("strategy")
        check(strategy)
        query, expected = final_query(strategy)
        result = api.validate(query)
        check_translation(result["oqo"], strategy.get("corpus", "core"), expected)
        return dict(result, submitted=query, leaves_checked=expected)
    if a.action == "eval":
        data = evaluate(run, api)
        uid = save_eval(run, data)
        records = run.get("records")
        dev = {k for k, v in records.items() if v["role"] in DEVELOPMENT}
        return {"evaluation": uid, "oql": data["final"]["oql"], "count": data["final"]["count"],
                "corpus": data["corpus"], "ablation": data["ablation"],
                "warnings": data["final"]["warnings"],
                "lines": {k: {"count": v["count"], "development_hits": sorted(dev.intersection(v["hits"])),
                              "warnings": v["warnings"]} for k, v in data["lines"].items()},
                "sets": {k: data["sets"][k] for k in sorted(DEVELOPMENT)},
                "misses": {k: v for k, v in data["misses"].items() if k in dev},
                "miss_details": miss_details(records, data["misses"], dev),
                "change": {k: sorted(dev.intersection(v)) if isinstance(v, list) else v
                           for k, v in data.get("change", {}).items()}, "usage": data["usage"]}
    if a.action == "review":
        return {"review": review(run, a.file)}
    if a.action == "report":
        return report(run, api)
    if a.action == "export":
        if a.max_pages is not None and a.max_pages < 1:
            raise Error("--max-pages must be at least 1.")
        return export(run, api, a.report, a.max_pages)
    records = run.get("records")
    if a.action == "resolve":
        result = [add(api, records, i, a.role, a.source) for i in a.identifiers]
        run.put("records", records)
        return {"records": result}
    if a.action == "develop":
        if a.key not in records or records[a.key]["role"] in DEVELOPMENT:
            raise Error("Choose the exact key of a validation or benchmark record.")
        r = records[a.key]
        r.setdefault("role_history", []).append({"at": now(), "from": r["role"], "reason": a.reason})
        r["role"] = "relevant"
        run.put("records", records)
        return {"role": "relevant", "key": a.key}
    if a.action == "terms":
        return mining(records)
    if a.action in ("sample", "neighbors"):
        if not 1 <= a.size <= 100:
            raise Error("--size must be between 1 and 100.")
        return discover(run, api, a)
    if a.action == "screen":
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", a.discovery):
            raise Error("Invalid discovery ID.")
        data = run.get("discovery/" + a.discovery)
        wid = a.work_id.upper()
        if wid not in {r["id"] for r in data["rows"]}:
            raise Error("This candidate is absent from the discovery batch.")
        decisions = run.get("screening") if (run.root / "screening.json").exists() else {}
        event = {"work_id": wid, "discovery": a.discovery, "decision": a.decision, "reason": a.reason}
        key = a.discovery + "/" + wid
        if key in decisions and decisions[key] != event:
            raise Error("This candidate already has a decision in this batch.")
        if a.decision == "include" and wid not in records:
            added = add(api, records, wid, "relevant", "screened from " + key + ": " + a.reason)
            if not added["work_id"]:
                raise Error("The screened work could not be resolved. Retry after checking its ID.")
            run.put("records", records)
        decisions[key] = event
        run.put("screening", decisions)
        return event
    raise Error("Unknown command.")


def main():
    a = parser().parse_args()
    run = Run(a.workspace)
    try:
        if a.action != "init" and not (run.root / "protocol.json").is_file():
            raise Error("Initialize this workspace first.")
        with run.locked():
            api = API(lambda event: run.log("requests", event))
            result = dispatch(run, api, a)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

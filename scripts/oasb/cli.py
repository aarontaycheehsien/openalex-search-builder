"""Command line interface for the OpenAlex search-builder skill."""
import argparse
import json
import sys
from pathlib import Path

from .api import API, Error, api_key_status, check_translation, load_dotenv, now
from .capture import capture_recapture
from .evaluate import evaluate, precision, report, review, revision_cost, save_eval, term_counts
from .export import estimate, export
from .holdouts import split_holdouts, score_holdouts
from .records import DEVELOPMENT, ROLES, add, discover, exposed_holdouts, mining
from .rescue import rescue
from .store import Run, digest, write
from .strategy import check, core, excluded, expected_tokens, final_query, lint


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-w", "--workspace", type=Path)
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
    c = cmds.add_parser("term-counts", help="Count each term alone to expose stemming collisions and hyphenated "
                                            "terms that are not phrases (about 10 credits per count).")
    c.add_argument("--block", help="Only this block's terms.")
    cmds.add_parser("validate")
    c = cmds.add_parser("eval")
    c.add_argument("--quick", action="store_true",
                   help="Skip the ablation lines (without/, drop-route/, with/) to save requests while developing. "
                        "review needs a full eval.")
    c.add_argument("--abstract-stats", action="store_true",
                   help="Also measure missing-abstract counts for final and each search block (1 extra API call per line).")
    c.add_argument("--term-impact", action="store_true",
                   help="Also measure marginal records and development hits uniquely contributed by each term.")
    c.add_argument("--no-cache", action="store_true",
                   help="Bypass query-cache.json and re-run every query line live.")
    c = cmds.add_parser("review")
    c.add_argument("file", type=Path, help="JSON critique, see SKILL.md")
    cmds.add_parser("report")
    c = cmds.add_parser("sample")
    c.add_argument("--oql", help="OQL query to sample (mutually exclusive with --from-strategy).")
    c.add_argument("--from-strategy", action="store_true",
                   help="Sample directly from the current strategy.json instead of passing --oql.")
    c.add_argument("--top", action="store_true",
                   help="With --oql or --from-strategy: take the best-matching records (OpenAlex's relevance order for a "
                        "text search) instead of a random sample. Not used for the precision estimate.")
    c.add_argument("--prior-reviews", action="store_true",
                   help="With --oql or --from-strategy: restrict to review works (`type is review`) within the date window "
                        "and report each review's matched-reference count for wording-independent snowballing.")
    c.add_argument("--added", action="store_true",
                   help="Sample only the records the latest evaluated revision added (in this search, not in the previous one).")
    c.add_argument("--removed", action="store_true",
                   help="Sample only the records the latest evaluated revision dropped (in the previous search, not in this one).")
    c.add_argument("--size", type=int, default=10)
    c.add_argument("--seed", type=int, default=42)
    c.add_argument("--any-date", action="store_true",
                   help="Do not apply the strategy's publication-date limits to the candidates (they apply by default).")
    c = cmds.add_parser("neighbors", help="Follow citing, referenced, or related works of one or more development records (cursor-paged).")
    c.add_argument("work_ids", nargs="+", help="One or more resolved development OpenAlex Work IDs.")
    c.add_argument("--direction", choices=["citing", "references", "related"], required=True)
    c.add_argument("--size", type=int, default=10, help="Maximum candidates to return (pages automatically above 100).")
    c.add_argument("--all", action="store_true", help="Page through all neighbors until exhausted (combine with --max-pages to bound cost).")
    c.add_argument("--cursor", help="Start from a saved next_cursor returned by a previous single-seed neighbors call.")
    c.add_argument("--max-pages", type=int, help="Stop after this many API pages (per seed when multiple seeds are given).")
    c.add_argument("--any-date", action="store_true",
                   help="Do not apply the strategy's publication-date limits to the candidates (they apply by default).")
    c = cmds.add_parser("screen")
    c.add_argument("discovery")
    c.add_argument("work_id")
    c.add_argument("decision", choices=["include", "exclude", "unclear"])
    c.add_argument("--reason", required=True)
    c.add_argument("--role", choices=["relevant", "validation"],
                   help="Allocate an included candidate directly; validation is not blinded if the builder saw it.")
    c = cmds.add_parser("holdout-split", help="Allocate citation-discovered eligible works in a separate reviewer workspace.")
    c.add_argument("--development-workspace", required=True, type=Path)
    c.add_argument("--seed", type=int, default=42)
    c = cmds.add_parser("holdout-score", help="Score sealed holdouts once, outside the development workspace, after delivery.")
    c.add_argument("--report", required=True, type=Path)
    c = cmds.add_parser("export", help="Page the delivered query into resumable CSV, RIS and JSONL files.")
    c.add_argument("--report", help="Report ID under reports/ (default: the latest delivery).")
    c.add_argument("--max-pages", type=int, help="Stop after this many 100-record pages; rerun to resume.")
    c.add_argument("--rescue", action="store_true", help="Export the report's saved no-abstract rescue query instead of the primary Boolean query.")
    c.add_argument("--estimate", action="store_true", help="Report pages and expected credits without downloading (free).")
    c = cmds.add_parser("rescue", help="Optional post-delivery citation-set rescue for works lacking abstracts in OpenAlex.")
    c.add_argument("--report", help="Report ID under reports/ (default: the latest delivery).")
    c.add_argument("--direction", choices=["cited-by", "citing", "both"], default="cited-by",
                   help="Citation link direction from the delivered Boolean set (default: cited-by, works the set "
                        "cites). both runs cited-by and citing as two queries, deduplicated on export.")
    c.add_argument("--method", choices=["references", "walk"], default="references",
                   help="How to find cited-by works: from the delivered works' matched reference lists (default; "
                        "no citation-set query) or with OpenAlex's citation-set query. citing always uses the query.")
    c.add_argument("--min-links", type=int, default=1,
                   help="With --direction cited-by --method references: require at least N delivered works to cite a candidate.")
    c.add_argument("--keywords-gate", action="store_true",
                   help="Upgrade title / title-abstract blocks in the rescue gate to title-abstract-keywords so abstract-less "
                        "works can match OpenAlex keywords.")
    c.add_argument("--estimate", action="store_true", help="Report expected credits without running the rescue.")
    c.add_argument("--topic", action="append", default=[],
                   help="Optional OpenAlex Topic ID (e.g. T10102) to gate rescue instead of the default any-block text gate. Repeatable.")
    c = cmds.add_parser("vocab", help="Look up OpenAlex keywords or topics for vocabulary development.")
    c.add_argument("entity", choices=["keywords", "topics"])
    c.add_argument("query")
    c.add_argument("--size", type=int, default=10)
    cmds.add_parser("lint", help="Check strategy syntax and heuristics offline (no API key or network).")
    cmds.add_parser("draft", help="Write untested draft artifacts under draft/ offline (no API key or network).")
    c = cmds.add_parser("doctor", help="Check OPENALEX_API_KEY, remaining budget/credits, API connectivity, and optional workspace state.")
    c.add_argument("-w", "--workspace", type=Path, default=argparse.SUPPRESS, help="Optional workspace to inspect.")
    c.add_argument("--offline", action="store_true",
                   help="Inspect Python, API key configuration, and workspace state without network calls.")
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


def write_draft(run):
    """Compile and save clearly labelled untested draft artifacts offline. No API calls."""
    protocol = run.get("protocol")
    strategy = run.get("strategy")
    checked = lint(strategy)
    dest = run.root / "draft"
    dest.mkdir(parents=True, exist_ok=True)
    at = now()
    lines = [
        "# Untested OpenAlex search draft",
        "",
        "> **Untested draft — not validated against OpenAlex.** No live counts, recall estimates, or API checks have been run.",
        "",
        f"Generated {at}. Corpus: `{checked['corpus']}`. Pulled works: `{checked['pulled']}`. Compiled leaves: {checked['leaves']}.",
        "",
        "## Review question",
        "",
        protocol["question"],
        "",
    ]
    if protocol.get("eligibility"):
        lines += ["## Eligibility criteria", ""] + [f"- {item}" for item in protocol["eligibility"]] + [""]
    lines += [
        "## Draft OQL query (untested)",
        "",
        "```text",
        checked["oql"],
        "```",
        "",
        "## Concept blocks",
        "",
        "| Block | Role | Field | Terms | Rationale |",
        "|---|---|---|---:|---|",
    ]
    for b in strategy["blocks"]:
        lines.append(f"| {b['id']} | {b['role']} | {b.get('field', 'title-abstract')} | {len(b.get('terms', []))} | {b.get('rationale', '')} |")
    if strategy.get("combine"):
        lines += ["", f"Combine expression: `{strategy['combine']}`"]
    if strategy.get("limits"):
        lines += ["", "## Limits", "", json.dumps(strategy["limits"], ensure_ascii=False, indent=2)]
    lines += ["", "## Local lint warnings", ""]
    if checked["warnings"]:
        lines += [f"- **{w['code']}** — {w['message']}" for w in checked["warnings"]]
    else:
        lines.append("No local lint warnings.")
    lines += [
        "",
        "## Next steps to validate against OpenAlex",
        "",
        "1. Set `OPENALEX_API_KEY` in the environment.",
        "2. Confirm scope with `scope --confirm --note \"...\"`.",
        "3. Run `validate` and `eval` (with optional seeds via `resolve`), then `review` and `report`.",
        "",
    ]
    (dest / "draft-query.oql").write_text(checked["oql"] + "\n", encoding="utf-8", newline="\n")
    (dest / "draft-notes.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    manifest = {
        "at": at,
        "validated_against_openalex": False,
        "oql": checked["oql"],
        "corpus": checked["corpus"],
        "leaves": checked["leaves"],
        "warnings": checked["warnings"],
        "strategy": strategy,
        "protocol": protocol,
        "note": "Untested draft — not validated against OpenAlex. Cannot be exported.",
    }
    write(dest / "draft-manifest.json", manifest)
    return {
        "draft": str(dest),
        "validated_against_openalex": False,
        "oql": checked["oql"],
        "corpus": checked["corpus"],
        "leaves": checked["leaves"],
        "warnings": checked["warnings"],
        "files": ["draft-query.oql", "draft-notes.md", "draft-manifest.json"],
        "note": "Untested draft — not validated against OpenAlex.",
    }


def workspace_status(run):
    if run is None:
        return None
    initialized = (run.root / "protocol.json").is_file()
    if not initialized:
        return {"path": str(run.root), "initialized": False}
    info = {"path": str(run.root), "initialized": True}
    try:
        info["scope_confirmed"] = run.scope_confirmed()
    except Error as exc:
        info["scope_confirmed"] = False
        info["scope_error"] = str(exc)
    cache_path = run.root / "query-cache.json"
    if cache_path.is_file():
        try:
            info["cached_queries"] = len(json.loads(cache_path.read_text(encoding="utf-8")).get("entries") or {})
        except (OSError, json.JSONDecodeError):
            info["cached_queries"] = 0
    else:
        info["cached_queries"] = 0
    eval_dir = run.root / "evaluations"
    info["evaluations"] = len(list(eval_dir.glob("*.json"))) if eval_dir.is_dir() else 0
    rep_dir = run.root / "reports"
    info["reports"] = len([d for d in rep_dir.iterdir() if d.is_dir()]) if rep_dir.is_dir() else 0
    req_log = run.root / "requests.jsonl"
    if req_log.is_file():
        try:
            info["logged_usd"] = round(sum(float(json.loads(s).get("cost_usd") or 0)
                                           for s in req_log.read_text(encoding="utf-8").splitlines() if s), 6)
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return info


def run_doctor(run, api, offline=False, key_source=None):
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    out = {
        "python_version": py_ver,
        "python_ok": sys.version_info >= (3, 10),
        "api_key": api_key_status(key_source),
    }
    if run is not None:
        out["workspace"] = workspace_status(run)
    if offline:
        out["offline"] = True
        out["ready_for_live_eval"] = None
        return out
    probe_fn = getattr(api, "probe", None)
    checks = probe_fn() if callable(probe_fn) else API().probe()
    out["translator"] = checks["translator"]
    out["budget"] = checks["budget"]
    out["ready_for_live_eval"] = bool(checks["translator"].get("ok") and checks["budget"].get("ok"))
    return out


def dispatch(run, api, a):
    if a.action == "doctor":
        return run_doctor(run, api, offline=getattr(a, "offline", False), key_source=getattr(a, "_key_source", None))
    if run is None:
        raise Error("Pass -w/--workspace.")
    if a.action == "init":
        return run.init(a.question, a.eligibility, a.depth)
    run.get("protocol")
    if a.action == "lint":
        return lint(run.get("strategy"))
    if a.action == "draft":
        return write_draft(run)
    if a.action == "scope":
        scope = run.scope_data()
        if a.confirm:
            if not a.note or not a.note.strip():
                raise Error("Record the reviewer's instruction using --note.")
            check(run.get("strategy"))
            key = run.scope_key()
            run.put("scope", {"at": now(), "hash": digest(key), "key": key, "decision": a.note})
        return dict(scope, confirmed=run.scope_confirmed())
    if a.action == "validate":
        strategy = run.get("strategy")
        check(strategy)
        query, expected = final_query(strategy)
        result = api.validate(query)
        check_translation(result["oqo"], strategy.get("corpus", "core"), expected,
                          expected_tokens(core(strategy), strategy.get("limits", {}), excluded(strategy)))
        return dict(result, submitted=query, leaves_checked=expected, strategy_warnings=lint(strategy)["warnings"])
    if a.action == "eval":
        data = evaluate(run, api, abstract_stats=getattr(a, "abstract_stats", False), quick=getattr(a, "quick", False),
                        term_impact=getattr(a, "term_impact", False), use_cache=not getattr(a, "no_cache", False))
        uid = save_eval(run, data)
        records = run.get("records")
        dev = {k for k, v in records.items() if v["role"] in DEVELOPMENT}
        out = {"evaluation": uid, "oql": data["final"]["oql"], "count": data["final"]["count"],
               "corpus": data["corpus"], "ablation": data["ablation"], "ablation_notes": data["ablation_notes"],
               "warnings": data["final"]["warnings"],
               "lines": {k: {"count": v["count"], "development_hits": sorted(dev.intersection(v["hits"])),
                             "warnings": v["warnings"]} for k, v in data["lines"].items()},
               "sets": {k: data["sets"][k] for k in sorted(DEVELOPMENT)},
               "misses": {k: v for k, v in data["misses"].items() if k in dev},
               "miss_details": miss_details(records, data["misses"], dev),
               "change": {k: sorted(dev.intersection(v)) if isinstance(v, list) else v
                          for k, v in data.get("change", {}).items()}, "usage": data["usage"],
               "cache": data["cache"], "delivery_estimate": data["delivery_estimate"],
               "independence": data["independence"],
               "strategy_warnings": lint(run.get("strategy"))["warnings"]}
        out["revision"] = revision_cost(data, dev)
        out["precision"] = precision(run, data["final"]["oqo"], data["final"]["count"])
        cr = capture_recapture(run)   # compact when not estimable, to keep eval output short
        out["capture_recapture"] = cr if cr["estimate"] is not None else {"diagnostic": cr["message"]}
        out["pulled"] = {"view": data["pulled"]["view"], "final_pulled_works": data["pulled"]["final_pulled_works"],
                         "development_pulled": sorted(dev.intersection(data["pulled"]["known_pulled"]))}
        for opt_key in ("abstract_stats", "irrelevant", "term_impact", "large_result"):
            if opt_key in data:
                out[opt_key] = data[opt_key]
        if exposed := exposed_holdouts(run, records):
            out["holdout_exposure"] = {
                "exposed": len(exposed),
                "note": "An earlier report showed these holdouts' retrieval. The next report will be labelled "
                        "post-exposure and also score the unexposed holdouts separately."}
        return out
    if a.action == "review":
        return review(run, a.file)   # {"review": id, "evaluation": id, "placeholders_filled": [...]}
    if a.action == "report":
        return report(run, api)
    if a.action == "holdout-split":
        return split_holdouts(run, a.development_workspace, a.seed)
    if a.action == "holdout-score":
        return score_holdouts(run, api, a.report)
    if a.action == "export":
        if a.estimate:
            return estimate(run, api, a.report, rescue=a.rescue)
        if a.max_pages is not None and a.max_pages < 1:
            raise Error("--max-pages must be at least 1.")
        return export(run, api, a.report, a.max_pages, rescue=a.rescue)
    if a.action == "rescue":
        return rescue(run, api, a.report, direction=a.direction, topics=a.topic or None, method=a.method,
                      estimate_only=a.estimate, min_links=getattr(a, "min_links", 1),
                      keywords_gate=getattr(a, "keywords_gate", False))
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
    if a.action == "term-counts":
        return term_counts(run, api, a.block)
    if a.action == "vocab":
        return api.vocabulary(a.entity, a.query, a.size)
    if a.action == "sample":
        if not 1 <= a.size <= 100:
            raise Error("--size must be between 1 and 100.")
        removed = getattr(a, "removed", False)
        prior_reviews = getattr(a, "prior_reviews", False)
        if sum(map(bool, (a.oql, a.from_strategy, a.added, removed))) != 1:
            raise Error("Pass exactly one of --oql, --from-strategy, --added or --removed.")
        if a.top and (a.added or removed):
            raise Error("--top works with --oql or --from-strategy, not --added or --removed.")
        if prior_reviews and (a.added or removed):
            raise Error("--prior-reviews works with --oql or --from-strategy, not --added or --removed.")
        if (a.added or removed) and a.size > 20:
            raise Error("--added and --removed draw five times --size from the search; use --size 20 or less.")
        return discover(run, api, a)
    if a.action == "neighbors":
        if not a.all and a.size < 1:
            raise Error("--size must be at least 1.")
        if a.max_pages is not None and a.max_pages < 1:
            raise Error("--max-pages must be at least 1.")
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
        if a.role and a.decision != "include":
            raise Error("--role is only valid with an include decision.")
        if a.role and wid in records and records[wid]["role"] != a.role:
            raise Error("This record already has a different role; do not relabel an exposed development work.")
        event = {"work_id": wid, "discovery": a.discovery, "decision": a.decision, "reason": a.reason}
        if a.role:
            event["role"] = a.role
        key = a.discovery + "/" + wid
        if key in decisions and decisions[key] != event:
            raise Error("This candidate already has a decision in this batch.")
        if a.decision == "include" and wid not in records:
            added = add(api, records, wid, a.role or "relevant", "screened from " + key + ": " + a.reason)
            if not added["work_id"]:
                raise Error("The screened work could not be resolved. Retry after checking its ID.")
            run.put("records", records)
        decisions[key] = event
        run.put("screening", decisions)
        return event
    raise Error("Unknown command.")


def main(argv=None, *, load_env=False):
    p = parser()
    a = p.parse_args(argv)
    if a.action != "doctor" and a.workspace is None:
        p.error("the following arguments are required: -w/--workspace")
    if load_env:
        dirs = ([a.workspace] if a.workspace else []) + [Path.cwd(), Path(__file__).resolve().parents[2]]
        a._key_source = load_dotenv(*dirs)
    run = Run(a.workspace) if a.workspace is not None else None
    try:
        if a.action == "doctor":
            result = dispatch(run, API(), a)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if a.action != "init" and not (run.root / "protocol.json").is_file():
            raise Error("Initialize this workspace first.")
        with run.locked():
            api = API(lambda event: run.log("requests", event))
            result = dispatch(run, api, a)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyError as exc:
        message = (f"{a.action}: a workspace file or OpenAlex response lacks the expected field {exc.args[0]!r}. "
                   "If a workspace file was edited by hand, restore it; otherwise rerun the previous step.")
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        message = f"{a.action}: {exc.filename} does not exist. Run the step that creates it first."
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

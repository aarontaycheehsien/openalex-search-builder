"""Execute strategies, isolate failing clauses, and bind delivery to fresh evidence."""
import json
import os
import tempfile
from pathlib import Path

from .api import Error, normalize_query, now
from .records import summary
from .store import digest, write
from .strategy import check, core, oql


def evaluate(run, api):
    run.approved()
    strategy, records = run.get("strategy"), run.get("records")
    start_hash = run.fingerprint()
    check(strategy)
    blocks = core(strategy)
    limits = strategy.get("limits", {})
    ids = {k for k, v in records.items() if v["work"]}
    cache = {}

    def measure(query):
        if query not in cache:
            parsed = api.validate(query)
            result = api.run(parsed["oql"])
            if normalize_query(result["oqo"]) != normalize_query(parsed["oqo"]):
                raise Error("The executed query differs from the validated query object.")
            hits = api.members(result["oqo"], ids) if result["count"] else set()
            if len(hits) > result["count"]:
                raise Error("Known-work matches exceed the total. Rerun the evaluation.")
            cache[query] = {"oql": result["oql"], "oqo": result["oqo"], "count": result["count"],
                            "hits": sorted(hits), "warnings": parsed["validation"].get("warnings", [])}
        return cache[query]

    final = measure(oql(blocks, limits))
    hits = set(final["hits"])
    lines = {}
    for b in blocks:
        lines["block/" + b["id"]] = measure(oql([b]))
    for key in limits:
        lines["limit/" + key] = measure(oql([], {key: limits[key]}))
    if len(blocks) > 1:
        for b in blocks:
            lines["without/" + b["id"]] = measure(oql([x for x in blocks if x != b], limits))
    for b in strategy["blocks"]:
        if b["role"] == "optional":
            lines["with/" + b["id"]] = measure(oql([*blocks, b], limits))
    missed = {wid: [k for k, v in lines.items() if k.startswith(("block/", "limit/")) and wid not in v["hits"]]
              for wid in sorted(ids - hits)}
    if any(not reasons for reasons in missed.values()):
        raise Error("A missed work matched every required clause. Repeat against the current index.")
    if start_hash != run.fingerprint():
        raise Error("Inputs changed during evaluation. Rerun with the current files.")
    return {"at": now(), "input_hash": start_hash, "final": final, "lines": lines, "misses": missed,
            "sets": summary(records, hits), "strategy": strategy,
            "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6),
                      "responses_without_cost_header": api.unpriced}}


def evidence(result):
    def signature(line):
        return {"oqo": normalize_query(line["oqo"]), "hits": line["hits"], "warnings": line["warnings"]}
    return digest({"inputs": result["input_hash"], "final": signature(result["final"]),
                   "lines": {k: signature(v) for k, v in result["lines"].items()}})


def save_eval(run, data):
    previous = run.latest("evaluations")[1] if (run.root / "latest-evaluations.json").exists() else None
    if previous:
        new, old = set(data["final"]["hits"]), set(previous["final"]["hits"])
        data["change"] = {"previous_at": previous["at"], "count_delta": data["final"]["count"] - previous["final"]["count"],
                          "gained": sorted(new - old), "lost": sorted(old - new)}
    return run.snapshot("evaluations", data)


def review(run, path):
    uid, current = run.latest("evaluations")
    if current["input_hash"] != run.fingerprint():
        raise Error("Evaluate the updated workspace before reviewing.")
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    sections = ("scope", "vocabulary", "boolean_and_fields", "limits", "retrieval", "limitations")
    for section in sections:
        if not isinstance(document.get(section), str) or not document[section].strip():
            raise Error(f"The critique needs a finding and disposition for {section}.")
    if document.get("decision") != "ready":
        raise Error("Address outstanding findings, reevaluate, and submit the revised critique.")
    return run.snapshot("reviews", {"at": now(), "evaluation": uid, "evidence": evidence(current), "critique": document})


def report(run, api):
    review_id, critic = run.latest("reviews")
    _, last = run.latest("evaluations")
    if critic["evidence"] != evidence(last) or last["input_hash"] != run.fingerprint():
        raise Error("The critique is stale. Evaluate and review the current strategy.")
    fresh = evaluate(run, api)
    uid = save_eval(run, fresh)
    if critic["evidence"] != evidence(fresh):
        raise Error("Live validation changed the retrieval evidence. Review the new evaluation.")
    query = fresh["final"]
    scope = run.scope_data()
    audit = ["# OpenAlex search audit", "", scope["protocol"]["question"], "",
             f"Executed {fresh['at']} via the OpenAlex OQL API. Works retrieved: {query['count']}.",
             "", "## Search as executed", "", "```text", query["oql"], "```", "",
             "## Scope and eligibility", "", json.dumps(scope, ensure_ascii=False, indent=2),
             "", "## Counts and known records", "", "| Line | Works | Known works retrieved |",
             "|---|---:|---:|", f"| Final | {query['count']} | {len(query['hits'])} |"]
    for name, line in fresh["lines"].items():
        audit.append(f"| {name} | {line['count']} | {len(line['hits'])} |")
    audit.extend(["", "| Set | Eligible | Resolved | Retrieved |", "|---|---:|---:|---:|"])
    for role, s in fresh["sets"].items():
        audit.append(f"| {role} | {s['eligible']} | {s['resolved']} | {s['retrieved']} |")
    audit += ["", "Coverage uses all eligible records. Relative recall uses resolved records within each set.",
              "A set with zero resolved works supplies no recall estimate.", "", "## Missed works", ""]
    audit += [f"- {wid} — {', '.join(reasons)}" for wid, reasons in fresh["misses"].items()] or ["No resolved known works missed."]
    audit += [f"- Unresolved ({role}) — {alias}" for role, s in fresh["sets"].items() for alias in s["unresolved"]]
    audit += ["", "## Search lines as executed", ""]
    for name, line in fresh["lines"].items():
        audit.extend([f"### {name}", "", "```text", line["oql"], "```", ""])
    history = [json.loads(p.read_text(encoding="utf-8")) for p in
               sorted((run.root / "evaluations").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)]
    audit += ["## Revision history", ""]
    for item in history:
        change = item.get("change", {})
        audit.append(f"- {item['at']} — {item['final']['count']} works. "
                     f"Known works gained: {', '.join(change.get('gained', [])) or 'none'}. "
                     f"Lost: {', '.join(change.get('lost', [])) or 'none'}.")
    audit += ["", "## PRESS-structured internal critique", ""]
    audit += [f"- **{k}** — {v}" for k, v in critic["critique"].items() if k != "decision"]
    audit += ["", "Draft for human peer review. OpenAlex metadata and counts can change.", ""]
    target = run.root / "reports"
    target.mkdir(exist_ok=True)
    dest = target / uid
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        (stage / "final-query.oql").write_text(query["oql"] + "\n", encoding="utf-8")
        write(stage / "final-query.json", query["oqo"])
        (stage / "audit.md").write_text("\n".join(audit), encoding="utf-8")
        write(stage / "validation.json", {"evaluation_id": uid, "review_id": review_id, "evaluation": fresh,
                                          "records": run.get("records"), "scope": scope, "review": critic,
                                          "history": history, "scope_approval": run.get("scope"),
                                          "screening": run.get("screening") if (run.root / "screening.json").is_file() else {}})
        if run.fingerprint() != fresh["input_hash"]:
            raise Error("Inputs changed before delivery.")
        os.replace(stage, dest)
    return {"report": str(dest), "validated_at": fresh["at"], "count": query["count"]}

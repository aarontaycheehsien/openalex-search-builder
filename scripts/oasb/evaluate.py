"""Execute strategies, isolate failing clauses, and bind delivery to fresh evidence."""
import json
import os
import tempfile
from pathlib import Path

from .api import PULLED_VIEWS, Error, check_translation, normalize_query, normalize_warnings, now
from . import prisma
from .records import PROTECTED, discovered_ids, exposed_holdouts, exposure, interval_text, summary
from .store import digest, write
from .strategy import (BARE_WORD, HYPHEN_PHRASE, assign, check, core, excluded, expected_leaves, expected_tokens, failing,
                       oql, parse_combine, pulled_view, spaced_phrase, term_text, tree_ids)


def evaluate(run, api, abstract_stats=False):
    run.approved()
    strategy, records = run.get("strategy"), run.get("records")
    start_hash = run.fingerprint()
    check(strategy)
    blocks, excludes = core(strategy), excluded(strategy)
    limits = strategy.get("limits", {})
    corpus = strategy.get("corpus", "core")
    tree = parse_combine(strategy["combine"], [b["id"] for b in blocks]) if "combine" in strategy else None
    ids = {k for k, v in records.items() if v["work"]}
    view = pulled_view(strategy)
    api.include_pulled = PULLED_VIEWS[view]
    # Pulled status changes nightly, so ask OpenAlex now rather than trusting stored records.
    pulled_known = api.pulled(ids)
    cache = {}

    def measure(use=(), lims=None, ex=(), combine=None):
        query = oql(list(use), lims, corpus, list(ex), combine)
        if query not in cache:
            parsed = api.validate(query)
            check_translation(parsed["oqo"], corpus, expected_leaves(use, lims, ex), expected_tokens(use, lims, ex))
            result = api.run(parsed["oql"])
            if normalize_query(result["oqo"]) != normalize_query(parsed["oqo"]):
                raise Error("The executed query differs from the validated query object.")
            hits = api.members(result["oqo"], ids) if result["count"] else set()
            if len(hits) > result["count"]:
                raise Error("Known-work matches exceed the total. Rerun the evaluation.")
            cache[query] = {"oql": result["oql"], "oqo": result["oqo"], "count": result["count"],
                            "hits": sorted(hits), "warnings": parsed["validation"].get("warnings", [])}
        return cache[query]

    final = measure(blocks, limits, excludes, tree)
    hits = set(final["hits"])
    lines = {}
    for b in blocks:
        lines["block/" + b["id"]] = measure([b])
    for b in excludes:
        lines["exclude/" + b["id"]] = measure([b])
    for key in limits:
        lines["limit/" + key] = measure((), {key: limits[key]})
    notes = []
    if tree is None and len(blocks) > 1:
        for b in blocks:
            lines["without/" + b["id"]] = measure([x for x in blocks if x != b], limits, excludes)
    elif tree is not None:
        # without/X: X relaxed (always satisfied), what X costs. drop-route/X: X removed as an
        # entry route (never satisfied), what X adds; only meaningful for a block under an OR.
        for b in blocks:
            for prefix, value in (("without/", True), ("drop-route/", False)):
                rest = assign(tree, b["id"], value)
                if rest is True:
                    notes.append(f"without/{b['id']} not assessed: relaxing it leaves no search block.")
                elif rest is not False:
                    use = [x for x in blocks if x["id"] in tree_ids(rest)]
                    lines[prefix + b["id"]] = measure(use, limits, excludes, rest)
    for b in excludes:
        lines["without/" + b["id"]] = measure(blocks, limits, [x for x in excludes if x != b], tree)
    for b in strategy["blocks"]:
        if b["role"] == "optional":
            if tree is None:
                lines["with/" + b["id"]] = measure([*blocks, b], limits, excludes)
            else:
                lines["with/" + b["id"]] = measure([*blocks, b], limits, excludes, ("and", [tree, b["id"]]))

    def explain(wid):
        if wid in pulled_known and view == "exclude":
            return ["pulled"]   # left out by OpenAlex's default view, whatever the blocks match
        values = {b["id"]: wid in lines["block/" + b["id"]]["hits"] for b in blocks}
        reasons = ["block/" + x for x in (failing(tree, values) if tree else [k for k, v in values.items() if not v])]
        reasons += ["exclude/" + b["id"] for b in excludes if wid in lines["exclude/" + b["id"]]["hits"]]
        reasons += [k for k in lines if k.startswith("limit/") and wid not in lines[k]["hits"]]
        return reasons

    missed = {wid: explain(wid) for wid in sorted(ids - hits)}
    if any(not reasons for reasons in missed.values()):
        raise Error("A missed work matched every required clause. Repeat against the current index.")
    if any(explain(wid) for wid in hits):
        raise Error("A retrieved work failed a required clause in its own line. Repeat against the current index.")
    if start_hash != run.fingerprint():
        raise Error("Inputs changed during evaluation. Rerun with the current files.")
    other = api.run(final["oql"], include_pulled=not PULLED_VIEWS[view])["count"]
    matching = (other - final["count"]) if view == "exclude" else (final["count"] - other)
    out = {"at": now(), "input_hash": start_hash, "final": final, "lines": lines, "misses": missed,
           "sets": summary(records, hits), "strategy": strategy, "corpus": corpus,
           "ablation": "assessed", "ablation_notes": notes,
           "pulled": {"view": view, "include_pulled": PULLED_VIEWS[view], "final_pulled_works": matching,
                      "known_pulled": sorted(pulled_known)}}
    if abstract_stats:
        def no_abs(line):
            total = line["count"]
            if not total:
                return {"without_abstract": 0, "total": 0, "pct_without_abstract": 0.0}
            oqo_na = dict(line["oqo"], filter_rows=[*line["oqo"].get("filter_rows", []),
                                                    {"column_id": "has_abstract", "value": False}])
            n = api.run(oqo_na)["count"]
            return {"without_abstract": n, "total": total, "pct_without_abstract": round(100 * n / total, 1)}
        stats = {"final": no_abs(final),
                 "blocks": {k: no_abs(lines[k]) for k in lines if k.startswith("block/")}}
        block_pcts = [v["pct_without_abstract"] for v in stats["blocks"].values() if v["total"]]
        if block_pcts and stats["final"]["total"] and stats["final"]["pct_without_abstract"] < min(block_pcts):
            stats["attrition_note"] = (
                f"Final query has {stats['final']['pct_without_abstract']}% works without abstracts vs "
                f"{min(block_pcts)}%-{max(block_pcts)}% in individual blocks; ANDing text blocks is "
                "disproportionately dropping abstract-less records. Consider `rescue` or a no-abstract fallback branch."
            )
        out["abstract_stats"] = stats
    if (run.root / "screening.json").is_file():
        decisions = run.get("screening")
        irrel_ids = sorted({e["work_id"] for e in decisions.values() if e.get("decision") == "exclude"} - set(records))
        if irrel_ids:
            irrel_hits = sorted(api.members(final["oqo"], set(irrel_ids))) if final["count"] else []
            out["irrelevant"] = {
                "screened_excluded": len(irrel_ids),
                "matched": len(irrel_hits),
                "match_rate": round(len(irrel_hits) / len(irrel_ids), 4),
                "hits": irrel_hits,
            }
    out["usage"] = {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6),
                    "responses_without_cost_header": api.unpriced}
    return out


STEM_RATIO, STEM_MIN = 3, 100        # stemmed count at least 3x the exact word and 100 more works
PHRASE_RATIO, PHRASE_MIN = 1.5, 50   # quoted hyphenated term at least 1.5x its phrase and 50 more works


def term_counts(run, api, block_id=None):
    """Per-term counts that expose stemming collisions and hyphenated terms that are not phrases.

    For a bare word, compares the stemmed count with the exact word: "fasting" stems to "fast"
    (2026-11-25: 471,555 titles stemmed vs 33,935 exact). For a quoted hyphenated term, compares
    it with its spaced phrase: "open-field" runs as open AND field (10,487 vs 4,872)."""
    strategy = run.get("strategy")
    check(strategy)
    corpus, limits = strategy.get("corpus", "core"), strategy.get("limits", {})
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]
    blocks = [b for b in strategy["blocks"] if b["role"] != "screen" and b.get("terms")]
    if block_id is not None:
        blocks = [b for b in blocks if b["id"] == block_id]
        if not blocks:
            raise Error(f"No block with terms has the ID {block_id!r}.")
    cache = {}

    def count(block, term):
        probe = {"id": "term", "role": "search", "field": block.get("field", "title-abstract"), "terms": [term], "rationale": ""}
        query = oql([probe], limits, corpus)
        if query not in cache:
            parsed = api.validate(query)
            check_translation(parsed["oqo"], corpus, expected_leaves([probe], limits), expected_tokens([probe], limits))
            cache[query] = api.run(parsed["oql"])["count"]
        return cache[query]

    rows, flags = [], []
    for b in blocks:
        for t in b["terms"]:
            row = {"block": b["id"], "term": term_text(t), "count": count(b, t)}
            if isinstance(t, str) and BARE_WORD.fullmatch(t):
                exact = count(b, f'"{t}"')
                row["exact_count"] = exact
                if row["count"] >= STEM_RATIO * max(exact, 1) and row["count"] - exact >= STEM_MIN:
                    row["flag"] = "stemming_expands"
                    row["message"] = (f"Stemmed {t} matches {row['count']:,} works but the exact word only {exact:,}: its stem "
                                      f"also matches other words. Consider \"{t}\" or listing the variants you want.")
            elif isinstance(t, str) and HYPHEN_PHRASE.fullmatch(t):
                phrase = count(b, spaced_phrase(t))
                row["phrase_count"] = phrase
                if row["count"] >= PHRASE_RATIO * max(phrase, 1) and row["count"] - phrase >= PHRASE_MIN:
                    row["flag"] = "hyphen_not_phrase"
                    row["message"] = (f"{t} matches {row['count']:,} works as an AND of its parts; the phrase "
                                      f"{spaced_phrase(t)} matches {phrase:,} and also covers the hyphenated spelling.")
            rows.append(row)
            if "flag" in row:
                flags.append(row)
    data = {"at": now(), "corpus": corpus, "limits": limits, "pulled": pulled_view(strategy), "terms": rows,
            "flagged": flags, "requests": len(cache),
            "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
            "note": "Each count is the term alone in its block's field, with the strategy's corpus and limits."}
    uid = run.snapshot("term-counts", data)
    return dict(data, snapshot=uid)


def evidence(result):
    def signature(line):
        return {"oqo": normalize_query(line["oqo"]), "hits": line["hits"],
                "warnings": normalize_warnings(line["warnings"])}
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


def pulled_text(p):
    if p["view"] == "exclude":
        return (f"Pulled works: excluded (request parameter include_pulled=false, OpenAlex's default view since "
                f"2026-11-19). {p['final_pulled_works']} retracted, withdrawn or removed works matching this search are "
                "left out of the count and the export.")
    return (f"Pulled works: included (request parameter include_pulled=true). {p['final_pulled_works']} of the "
            "retrieved works are retracted, withdrawn or removed; check their pull notices before screening.")


def report(run, api):
    review_id, critic = run.latest("reviews")
    _, last = run.latest("evaluations")
    if critic["evidence"] != evidence(last) or last["input_hash"] != run.fingerprint():
        raise Error("The critique is stale. Evaluate and review the current strategy.")
    fresh = evaluate(run, api, abstract_stats="abstract_stats" in last)
    uid = save_eval(run, fresh)
    if critic["evidence"] != evidence(fresh):
        raise Error("Live validation changed the retrieval evidence. Review the new evaluation.")
    query = fresh["final"]
    scope = run.scope_data()
    audit = ["# OpenAlex search audit", "", scope["protocol"]["question"], "",
             f"Executed {fresh['at']} via the OpenAlex OQL API. Works retrieved: {query['count']}.",
             f"Corpus: {fresh['corpus']}. Block ablation: {fresh['ablation']}"
             + ("".join(" " + n for n in fresh.get("ablation_notes", [])) or "."),
             pulled_text(fresh["pulled"]),
             "", "## Search as executed", "", "```text", query["oql"], "```", "",
             "## Scope and eligibility", "", json.dumps(scope, ensure_ascii=False, indent=2),
             "", "## Counts and known records", "", "| Line | Works | Known works retrieved |",
             "|---|---:|---:|", f"| Final | {query['count']} | {len(query['hits'])} |"]
    for name, line in fresh["lines"].items():
        audit.append(f"| {name} | {line['count']} | {len(line['hits'])} |")
    if "abstract_stats" in fresh:
        st = fresh["abstract_stats"]
        audit.extend(["", "## Missing-abstract attrition", "",
                      "| Line | Without abstract | Total | % without abstract |",
                      "|---|---:|---:|---:|",
                      f"| Final | {st['final']['without_abstract']} | {st['final']['total']} | {st['final']['pct_without_abstract']}% |"])
        for name, row in st["blocks"].items():
            audit.append(f"| {name} | {row['without_abstract']} | {row['total']} | {row['pct_without_abstract']}% |")
        if st.get("attrition_note"):
            audit.extend(["", st["attrition_note"]])
    audit.extend(["", "| Set | Eligible | Resolved | Retrieved | Relative recall (95% CI) |", "|---|---:|---:|---:|---|"])
    for role, s in fresh["sets"].items():
        audit.append(f"| {role} | {s['eligible']} | {s['resolved']} | {s['retrieved']} | {interval_text(s)} |")
    audit += ["", "Coverage uses all eligible records. Relative recall uses resolved records within each set.",
              "A set with zero resolved works supplies no recall estimate. Intervals are 95% Wilson score",
              "intervals; with only a few known works they are wide, and they describe the known works only.",
              ""]
    records = run.get("records")
    exposed = exposed_holdouts(run, records)
    post = None
    if exposed:
        earlier = [e for e in exposure(run) if set(e["ids"]) & set(exposed)]
        unexposed = summary({k: v for k, v in records.items() if k not in exposed}, set(query["hits"]))
        post = {"exposed": exposed, "first_exposed_at": min(e["at"] for e in earlier),
                "earlier_reports": sorted({e["report"] for e in earlier}),
                "unexposed_sets": {role: unexposed[role] for role in PROTECTED}}
        audit += ["## Holdout exposure", "",
                  f"**Post-exposure delivery.** {len(exposed)} validation or benchmark work(s) had their retrieval "
                  f"shown in earlier report(s) {', '.join(post['earlier_reports'])} (first at {post['first_exposed_at']}). "
                  "Any revision since then may have been informed by those results, so their relative recall is no "
                  "longer an independent estimate.", "",
                  "| Set, excluding exposed works | Resolved | Retrieved | Relative recall (95% CI) |", "|---|---:|---:|---|"]
        audit += [f"| {role} | {s['resolved']} | {s['retrieved']} | {interval_text(s)} |"
                  for role, s in post["unexposed_sets"].items()]
        audit += ["", "Exposed works: " + ", ".join(exposed), ""]
    else:
        audit += ["## Holdout exposure", "", "No validation or benchmark result had been shown in an earlier delivery.", ""]
    seen_holdouts = sorted(discovered_ids(run) & {k for k, v in records.items() if v["role"] in PROTECTED})
    audit += ["", "## Holdout selection and blinding", "",
              "Local validation and benchmark roles are protected from intermediate CLI output and mining, "
              "not from an agent with filesystem access. A local holdout is not necessarily blinded."]
    if seen_holdouts:
        audit += ["Known holdouts previously returned in this workspace's discovery batches: "
                  + ", ".join(seen_holdouts) + ". These are not blinded to the development agent."]
    audit += ["For separate-context validation use holdout-split and holdout-score in an isolated reviewer workspace.", ""]
    audit += ["## Missed works", ""]
    audit += [f"- {wid} — {', '.join(reasons)}" for wid, reasons in fresh["misses"].items()] or ["No resolved known works missed."]
    audit += [f"- Unresolved ({role}) — {alias}" for role, s in fresh["sets"].items() for alias in s["unresolved"]]
    if fresh["pulled"]["known_pulled"]:
        audit += ["", "Known works OpenAlex marks as pulled: " + ", ".join(fresh["pulled"]["known_pulled"]) + "."]
    audit += ["", "## Search lines as executed", ""]
    for name, line in fresh["lines"].items():
        audit.extend([f"### {name}", "", "```text", line["oql"], "```", ""])
    # Order by each evaluation's own timestamp; file times change when a workspace is copied or cloned.
    stamped = [(json.loads(p.read_text(encoding="utf-8")), p.stat().st_mtime_ns)
               for p in (run.root / "evaluations").glob("*.json")]
    history = [item for item, _ in sorted(stamped, key=lambda x: (x[0]["at"], x[1]))]
    audit += ["## Revision history", ""]
    for item in history:
        change = item.get("change", {})
        audit.append(f"- {item['at']} — {item['final']['count']} works. "
                     f"Known works gained: {', '.join(change.get('gained', [])) or 'none'}. "
                     f"Lost: {', '.join(change.get('lost', [])) or 'none'}.")
    audit += ["", "## PRESS-structured internal critique", ""]
    audit += [f"- **{k}** — {v}" for k, v in critic["critique"].items() if k != "decision"]
    checklist = prisma.items(run, fresh, critic, run.get("scope")["decision"])
    audit += [""] + prisma.section(checklist)
    audit += ["", "Draft for human peer review. OpenAlex metadata and counts can change.", ""]
    target = run.root / "reports"
    target.mkdir(exist_ok=True)
    dest = target / uid
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        (stage / "final-query.oql").write_text(query["oql"] + "\n", encoding="utf-8")
        write(stage / "final-query.json", query["oqo"])
        write(stage / "prisma-s.json", {"guideline": prisma.CITATION, "items": checklist})
        (stage / "audit.md").write_text("\n".join(audit), encoding="utf-8")
        write(stage / "validation.json", {"evaluation_id": uid, "review_id": review_id, "evaluation": fresh,
                                          "records": run.get("records"), "scope": scope, "review": critic,
                                          "history": history, "scope_approval": run.get("scope"),
                                          "screening": run.get("screening") if (run.root / "screening.json").is_file() else {},
                                          "post_exposure": post, "discovery_exposed_holdouts": seen_holdouts})
        if run.fingerprint() != fresh["input_hash"]:
            raise Error("Inputs changed before delivery.")
        os.replace(stage, dest)
    # This audit reveals holdout retrieval. Record it outside the input fingerprint, so the
    # delivered evidence stays valid while later deliveries are labelled post-exposure.
    shown = sorted(k for k, r in records.items() if r["role"] in PROTECTED and r["work"])
    run.put("exposure", {"events": [*exposure(run), {"at": fresh["at"], "report": uid, "ids": shown}]})
    return {"report": str(dest), "validated_at": fresh["at"], "count": query["count"],
            "post_exposure": bool(post), "exposed_holdouts": exposed}

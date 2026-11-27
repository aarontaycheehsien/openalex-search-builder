"""Optional post-delivery citation rescue for works lacking abstracts in OpenAlex.

Two methods. `references` (the default for cited-by) reads the delivered works' matched reference
lists (`referenced_works`), keeps the cited works outside the delivered set that have no abstract and
fall in the date window, then applies the text or topic gate to those IDs. It never rebuilds the
delivered search inside a citation query, which is what times out. `walk` runs OpenAlex's citation-set
query (`it's cited by a work in the set (...)` or `it cites a work in the set (...)`); citing needs it.
On 2026-11-25 the union of matched reference lists reproduced the walk's cited-by set exactly once
pulled works were excluded.
"""
from collections import Counter
import hashlib
import json
import math
import re

from .api import PULLED_VIEWS, Error, check_translation, normalize_query, now, work_id
from .export import PAGE_SIZE, USD_PER_CREDIT, delivered, request_cost
from .records import DEVELOPMENT
from .store import write
from .strategy import pulled_view, rescue_expectation, rescue_gate, rescue_oql

MAX_SET_WORKS = 450_000
# `both` embeds the delivered query twice and is the query most likely to time out, so it runs as
# two single-direction queries whose records are deduplicated by work ID on export.
DIRECTIONS = {"cited-by": ["cited-by"], "citing": ["citing"], "both": ["cited-by", "citing"]}
METHODS = {"references", "walk"}
SLOW = re.compile(r"query_too_slow|es_error|timed out", re.I)
NEAR_BUDGET = 0.8
LOOKUP = 100                # IDs per lookup request
NO_ABSTRACT_SHARE = 0.225   # 8,101 of 36,023 works cited by a 3,279-work set lacked abstracts (2026-11-25)
REFS_PER_WORK = 11          # 36,023 distinct cited works for 3,279 delivered works (2026-11-25)
LINK_NOTE = ("Built from OpenAlex's matched references (referenced_works): about 82% of references to works in "
             "OpenAlex are linked, unmatched ones are dropped and many records have no reference list "
             "(https://help.openalex.org/data/works/citations/). Links change as OpenAlex rematches them "
             "(they changed on 25 November 2026), so this ID list is a dated snapshot.")


def slow_advice(direction, done, estimate):
    seconds = estimate.get("seconds")
    budget = estimate.get("budget_seconds")
    timing = f" OpenAlex estimated {seconds}s of a {budget}s budget." if seconds and budget else ""
    finished = f" The {' and '.join(done)} direction completed; rerun with `--direction {done[0]}` to keep it." if done else ""
    # Observed 2026-11-25: most of the time goes into rebuilding the delivered set inside the query;
    # a --topic gate on the outer query left OpenAlex's estimate unchanged and still timed out.
    advice = ("For cited-by, use the default `--method references`, which does not rebuild the search. "
              if direction == "cited-by" else "")
    return (f"The {direction} rescue query timed out in OpenAlex after retries.{timing}{finished} Most of the "
            "time goes into rebuilding the delivered search as the citation set, so a --topic gate does not "
            f"shorten it. {advice}Otherwise follow `neighbors --direction references|citing` from screened "
            "development works, or rescue a smaller delivered search (for example a later from_date or the core "
            "corpus) and report that restriction.")


def export_source(run, dest):
    """The complete primary export of this report, if there is one: its records hold referenced_works."""
    folder = run.root / "exports" / dest.name
    manifest = folder / "export-manifest.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("report") != dest.name or data.get("rescue") or not data.get("complete") \
            or not (folder / "results.jsonl").is_file():
        return None
    return folder / "results.jsonl"


def reference_lists(run, api, dest, final_oqo):
    """{work ID: matched references} for every delivered work, from the export or by paging the search."""
    source = export_source(run, dest)
    if source:
        lists = {}
        with source.open(encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                lists[work_id(record)] = [r.rsplit("/", 1)[-1].upper() for r in record.get("referenced_works") or []]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return lists, {"from": "export", "file": str(source.relative_to(run.root)), "sha256": digest, "pages": 0}
    lists, cursor, pages, seen = {}, "*", 0, set()
    while cursor:
        page = api.page(final_oqo, cursor, PAGE_SIZE, select="id,referenced_works")
        if page["next_cursor"] and (page["next_cursor"] == cursor or page["next_cursor"] in seen):
            raise Error("Repeated cursor while reading the delivered works' reference lists.")
        seen.add(cursor)
        pages += 1
        for record in page["rows"]:
            lists[work_id(record)] = [r.rsplit("/", 1)[-1].upper() for r in record.get("referenced_works") or []]
        cursor = page["next_cursor"]
    return lists, {"from": "paged delivered search", "pages": pages, "at": now()}


def date_filters(limits):
    return [f"{column}:{limits[key]}" for key, column in (("from_date", "from_publication_date"),
                                                          ("to_date", "to_publication_date")) if key in limits]


def references_rescue(run, api, dest, final_oqo, strategy, topics, min_links=1, keywords_gate=False):
    lists, source = reference_lists(run, api, dest, final_oqo)
    delivered_ids = set(lists)
    cite_counts = Counter(r for refs in lists.values() for r in set(refs) if r not in delivered_ids)
    all_cited = sorted(cite_counts)
    cited = [r for r in all_cited if cite_counts[r] >= min_links]
    corpus = strategy.get("corpus", "core")
    limits = strategy.get("limits", {})
    candidates = set()
    for start in range(0, len(cited), LOOKUP):
        rows = api.lookup(cited[start:start + LOOKUP], ["has_abstract:false", *date_filters(limits)], corpus)
        candidates |= {work_id(r) for r in rows}
    gate_oql, leaves, words = rescue_gate(strategy, topics, keywords_gate=keywords_gate)
    parsed = api.validate(gate_oql)
    check_translation(parsed["oqo"], corpus, leaves, words)
    rescued = api.members(parsed["oqo"], candidates, batch_size=LOOKUP) if candidates else set()
    stages = {"delivered_works": len(lists), "with_matched_references": sum(1 for v in lists.values() if v),
              "reference_links": sum(len(v) for v in lists.values()), "cited_outside_delivered": len(all_cited),
              **({"at_least_min_links": len(cited)} if min_links > 1 else {}),
              "without_abstract_in_window": len(candidates), "passing_gate": len(rescued)}
    co_citations = {"1_or_more": len(all_cited), "2_or_more": sum(1 for c in cite_counts.values() if c >= 2),
                    "3_or_more": sum(1 for c in cite_counts.values() if c >= 3), "min_links_applied": min_links}
    return {"direction": "cited-by", "method": "references", "count": len(rescued), "ids": sorted(rescued),
            "gate_oql": parsed["oql"], "stages": stages, "co_citations": co_citations, "source": source,
            "warnings": parsed["validation"].get("warnings", []), "time_estimate": {}}


def walk_rescue(api, strategy, part, topics, done, keywords_gate=False):
    parsed = api.validate(rescue_oql(strategy, direction=part, topics=topics, keywords_gate=keywords_gate))
    # Same guard as the primary search, applied inside the nested citation set too.
    check_translation(parsed["oqo"], strategy.get("corpus", "core"),
                      *rescue_expectation(strategy, direction=part, topics=topics))
    estimate = (parsed.get("check") or {}).get("estimate") or {}
    try:
        result = api.run(parsed["oql"])
    except Error as exc:
        if SLOW.search(str(exc)):
            raise Error(slow_advice(part, done, estimate)) from exc
        raise
    if normalize_query(result["oqo"]) != normalize_query(parsed["oqo"]):
        raise Error("The executed rescue query differs from the validated query object.")
    return {"direction": part, "method": "walk", "oql": result["oql"], "oqo": result["oqo"], "count": result["count"],
            "warnings": parsed["validation"].get("warnings", []), "time_estimate": estimate}


def unlinked_references(api, records):
    """Printed reference lines of the development works that OpenAlex did not link to a work: leads for
    other sources (grey literature, works outside OpenAlex). Free requests; protected works are skipped."""
    lines = []
    for wid in sorted(k for k, v in records.items() if v.get("role") in DEVELOPMENT and v.get("work")):
        for line in api.reference_lines(wid):
            if not line.get("work_id") and (line.get("raw_reference_string") or line.get("raw_doi")):
                lines.append({"cited_by": wid, "raw_reference_string": line.get("raw_reference_string"),
                              "raw_doi": line.get("raw_doi")})
    return lines


def estimate(run, api, dest, final_oqo, strategy, direction, method, min_links=1, keywords_gate=False):
    """Expected credits for a rescue, without running it. The reference pass is exact when an export exists."""
    parts, total = [], 0
    for part in DIRECTIONS[direction]:
        if part == "cited-by" and method == "references":
            source = export_source(run, dest)
            if source:
                with source.open(encoding="utf-8") as f:
                    lists = {}
                    for line in f:
                        record = json.loads(line)
                        lists[work_id(record)] = [r.rsplit("/", 1)[-1].upper() for r in (record.get("referenced_works") or [])]
                delivered_ids = set(lists)
                cite_counts = Counter(r for v in lists.values() for r in set(v) if r not in delivered_ids)
                cited = sum(1 for c in cite_counts.values() if c >= min_links)
                paging, basis = 0, ("exact distinct cited works from the export"
                                    + (f" (cited by >= {min_links} delivered works)" if min_links > 1 else ""))
            else:
                delivered_count = json.loads((dest / "validation.json").read_text(encoding="utf-8"))["evaluation"]["final"]["count"]
                cited = delivered_count * REFS_PER_WORK
                if min_links > 1:
                    cited = max(1, cited // (min_links * 2))
                per_page, _ = request_cost(final_oqo, None)
                paging = math.ceil(delivered_count / PAGE_SIZE) * per_page
                basis = f"no export: paging the delivered search, and about {REFS_PER_WORK} cited works per delivered work"
            lookups = math.ceil(cited / LOOKUP)
            gate = math.ceil(cited * NO_ABSTRACT_SHARE / LOOKUP) * 10
            credits = paging + lookups + gate
            parts.append({"direction": part, "method": "references", "min_links": min_links,
                          "paging_credits": paging, "cited_works": cited,
                          "lookup_credits": lookups, "gate_credits_estimate": gate, "credits": credits, "basis": basis
                          + f"; gate cost assumes {NO_ABSTRACT_SHARE:.0%} of cited works lack an abstract"})
        else:
            parsed = api.validate(rescue_oql(strategy, direction=part, keywords_gate=keywords_gate))
            check = parsed.get("check") or {}
            credits = (check.get("cost") or {}).get("credits")
            parts.append({"direction": part, "method": "walk", "credits": credits, "time_estimate": check.get("estimate"),
                          "basis": "OpenAlex's cost check for one execution (exporting re-runs it for every page)"})
            credits = credits or 0
        total += credits
    return {"report": dest.name, "estimate": True, "parts": parts, "estimated_credits": total,
            "estimated_usd": round(total * USD_PER_CREDIT, 4),
            "note": "Nothing was run. Single-work lookups (reference lines) are free and not counted."}


def rescue(run, api, report_id=None, direction="cited-by", topics=None, method="references", estimate_only=False,
           min_links=1, keywords_gate=False):
    """Measure the delivered search's missing-abstract share and rescue abstract-less works linked to it by
    citation. Saves reproducible artifacts under reports/<report-id>/rescue/ without changing the delivered
    Boolean query. Only development records are shown in hit diagnostics."""
    if direction not in DIRECTIONS:
        raise Error("Rescue direction must be cited-by, citing, or both.")
    if method not in METHODS:
        raise Error("Rescue method must be references or walk.")
    if type(min_links) is not int or min_links < 1:
        raise Error("--min-links must be an integer >= 1.")
    if min_links > 1 and not (direction == "cited-by" and method == "references"):
        raise Error("--min-links requires --direction cited-by and --method references.")
    dest, archive, final_oqo = delivered(run, report_id)
    ev = archive["evaluation"]
    strategy = ev["strategy"]
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]   # the delivered search's view
    corpus = strategy.get("corpus", "core")
    final_count = ev["final"]["count"]
    if estimate_only:
        return estimate(run, api, dest, final_oqo, strategy, direction, method,
                        min_links=min_links, keywords_gate=keywords_gate)
    if final_count > MAX_SET_WORKS and method == "walk":
        raise Error(f"Delivered query has {final_count:,} works, exceeding OpenAlex's {MAX_SET_WORKS:,}-work set limit.")

    # Missing-abstract share of the delivered Boolean query
    no_abs_oqo = dict(final_oqo, filter_rows=[*final_oqo.get("filter_rows", []),
                                              {"column_id": "has_abstract", "value": False}])
    final_no_abs = api.run(no_abs_oqo)["count"]
    final_no_abs_pct = round(100 * final_no_abs / final_count, 1) if final_count else 0.0

    records = archive.get("records") or run.get("records")
    resolved_ids = {k for k, v in records.items() if v.get("work")}
    dev_ids = {k for k, v in records.items() if v.get("role") in DEVELOPMENT and v.get("work")}
    final_hits = set(ev["final"]["hits"])

    queries = []
    for part in DIRECTIONS[direction]:
        if part == "cited-by" and method == "references":
            entry = references_rescue(run, api, dest, final_oqo, strategy, topics,
                                      min_links=min_links, keywords_gate=keywords_gate)
            entry["hits"] = sorted(set(entry["ids"]) & resolved_ids)
        else:
            entry = walk_rescue(api, strategy, part, topics, [q["direction"] for q in queries],
                                keywords_gate=keywords_gate)
            entry["hits"] = sorted(api.members(entry["oqo"], resolved_ids)) if (entry["count"] and resolved_ids) else []
        queries.append(entry)

    rescue_hits = set().union(*(set(q["hits"]) for q in queries))
    dev_rescued = sorted((rescue_hits - final_hits) & dev_ids)
    dev_overlap = sorted((rescue_hits & final_hits) & dev_ids)
    single = len(queries) == 1
    rescue_count = queries[0]["count"] if single else None
    upper = sum(q["count"] for q in queries)
    slow = [q["direction"] for q in queries
            if q["time_estimate"].get("seconds") and q["time_estimate"].get("budget_seconds")
            and q["time_estimate"]["seconds"] >= NEAR_BUDGET * q["time_estimate"]["budget_seconds"]]
    leads = unlinked_references(api, records)

    folder = dest / "rescue"
    folder.mkdir(exist_ok=True)
    for stale in [*folder.glob("rescue-query*"), *folder.glob("rescue-ids*")]:   # an earlier rescue of this report
        stale.unlink()
    saved = []
    for q in queries:
        if q["method"] == "references":
            name = f"rescue-ids-{q['direction']}.json"
            write(folder / name, q["ids"])
            saved.append({k: q[k] for k in ("direction", "method", "count", "gate_oql", "stages", "co_citations", "source")}
                         | {"ids_file": name, "ids_sha256": hashlib.sha256((folder / name).read_bytes()).hexdigest()})
        else:
            (folder / f"rescue-query-{q['direction']}.oql").write_text(q["oql"] + "\n", encoding="utf-8", newline="\n")
            write(folder / f"rescue-query-{q['direction']}.json", q["oqo"])
            saved.append({k: q[k] for k in ("direction", "method", "oql", "oqo", "count", "time_estimate")})
    write(folder / "unlinked-references.json", leads)
    gate = f"topic:{','.join(topics)}" if topics else ("any-block-keywords" if keywords_gate else "any-block")
    warnings = [w for q in queries for w in q["warnings"]]
    notes = []
    if any(q["method"] == "references" for q in queries):
        notes.append(LINK_NOTE)
    if not single:
        notes.append(f"Run as {len(queries)} single-direction parts. The rescue total is at most {upper:,}; "
                     "works found by both are deduplicated by work ID on export.")
    if slow:
        notes.append("OpenAlex estimates the " + " and ".join(slow) + " query close to its time budget; "
                     "reruns may time out. A topic gate or narrower limits make it lighter.")
    if leads:
        notes.append(f"{len(leads)} reference line(s) of development works are not linked to any OpenAlex work; "
                     "they are listed in unlinked-references.json as leads for other sources.")
    manifest = {
        "at": now(), "report": dest.name, "direction": direction, "method": method, "gate": gate,
        "min_links": min_links, "keywords_gate": keywords_gate,
        "topics": list(topics or []), "corpus": corpus, "include_pulled": api.include_pulled,
        "final_count": final_count, "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct, "rescue_count": rescue_count,
        "rescue_count_upper_bound": upper, "queries": saved, "warnings": warnings,
        "hits_all_sets": sorted(rescue_hits), "rescued_all_sets": sorted(rescue_hits - final_hits),
        "unlinked_reference_lines": len(leads),
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
        "notes": notes,
        "note": "Supplementary citation rescue for works lacking abstracts in OpenAlex. Report separately from the "
                "primary Boolean search in PRISMA flow diagrams; deduplicate by OpenAlex Work ID against the primary export.",
    }
    write(folder / "rescue-manifest.json", manifest)
    co_cites = {q["direction"]: q["co_citations"] for q in queries if "co_citations" in q}
    return {
        "report": dest.name, "rescue_dir": str(folder), "direction": direction, "method": method, "gate": gate,
        "min_links": min_links, "keywords_gate": keywords_gate,
        "corpus": corpus, "final_count": final_count, "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct, "rescue_count": rescue_count,
        "rescue_count_upper_bound": upper, "counts_by_direction": {q["direction"]: q["count"] for q in queries},
        "stages": {q["direction"]: q["stages"] for q in queries if "stages" in q},
        **({"co_citations": co_cites} if co_cites else {}),
        "oql": [q.get("oql") or q.get("gate_oql") for q in queries] if not single else (queries[0].get("oql") or queries[0].get("gate_oql")),
        "warnings": warnings, "notes": notes, "unlinked_reference_lines": len(leads),
        "development_rescued": dev_rescued, "development_overlap": dev_overlap, "usage": manifest["usage"],
        "next": f"Run `export --report {dest.name} --rescue --estimate`, then `export --report {dest.name} --rescue` "
                "to download the rescue records for deduplication.",
    }

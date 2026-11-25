"""Optional post-delivery citation-set rescue for works lacking abstracts in OpenAlex."""
import re

from .api import PULLED_VIEWS, Error, check_translation, normalize_query, now
from .export import delivered
from .records import DEVELOPMENT
from .store import write
from .strategy import pulled_view, rescue_expectation, rescue_oql

MAX_SET_WORKS = 450_000
# `both` embeds the delivered query twice and is the query most likely to time out, so it runs as
# two single-direction queries whose records are deduplicated by work ID on export.
DIRECTIONS = {"cited-by": ["cited-by"], "citing": ["citing"], "both": ["cited-by", "citing"]}
SLOW = re.compile(r"query_too_slow|es_error|timed out", re.I)
NEAR_BUDGET = 0.8


def slow_advice(direction, done, estimate):
    seconds = estimate.get("seconds")
    budget = estimate.get("budget_seconds")
    timing = f" OpenAlex estimated {seconds}s of a {budget}s budget." if seconds and budget else ""
    finished = f" The {' and '.join(done)} direction completed; rerun with `--direction {done[0]}` to keep it." if done else ""
    # Observed 2026-11-25: most of the time goes into rebuilding the delivered set inside the query;
    # a --topic gate on the outer query left OpenAlex's estimate unchanged and still timed out.
    return (f"The {direction} rescue query timed out in OpenAlex after retries.{timing}{finished} Most of the "
            "time goes into rebuilding the delivered search as the citation set, so a --topic gate does not "
            "shorten it. Instead follow `neighbors --direction references|citing` from screened development "
            "works, or rescue a smaller delivered search (for example a later from_date or the core corpus) "
            "and report that restriction.")


def rescue(run, api, report_id=None, direction="cited-by", topics=None):
    """Measure missing-abstract share on a delivered report and run citation-set rescue.

    Saves reproducible rescue artifacts under reports/<report-id>/rescue/ without modifying
    the delivered Boolean query. Only development records are shown in hit diagnostics.
    """
    if direction not in DIRECTIONS:
        raise Error("Rescue direction must be cited-by, citing, or both.")
    dest, archive, final_oqo = delivered(run, report_id)
    ev = archive["evaluation"]
    strategy = ev["strategy"]
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]   # the delivered search's view
    corpus = strategy.get("corpus", "core")
    final_count = ev["final"]["count"]
    if final_count > MAX_SET_WORKS:
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
        parsed = api.validate(rescue_oql(strategy, direction=part, topics=topics))
        # Same guard as the primary search, applied inside the nested citation set too.
        check_translation(parsed["oqo"], corpus, *rescue_expectation(strategy, direction=part, topics=topics))
        estimate = (parsed.get("check") or {}).get("estimate") or {}
        try:
            result = api.run(parsed["oql"])
        except Error as exc:
            if SLOW.search(str(exc)):
                raise Error(slow_advice(part, [q["direction"] for q in queries], estimate)) from exc
            raise
        if normalize_query(result["oqo"]) != normalize_query(parsed["oqo"]):
            raise Error("The executed rescue query differs from the validated query object.")
        hits = api.members(result["oqo"], resolved_ids) if (result["count"] and resolved_ids) else set()
        queries.append({"direction": part, "oql": result["oql"], "oqo": result["oqo"], "count": result["count"],
                        "hits": sorted(hits), "warnings": parsed["validation"].get("warnings", []),
                        "time_estimate": estimate})

    rescue_hits = set().union(*(set(q["hits"]) for q in queries))
    dev_rescued = sorted((rescue_hits - final_hits) & dev_ids)
    dev_overlap = sorted((rescue_hits & final_hits) & dev_ids)
    single = len(queries) == 1
    rescue_count = queries[0]["count"] if single else None
    upper = sum(q["count"] for q in queries)
    slow = [q["direction"] for q in queries
            if q["time_estimate"].get("seconds") and q["time_estimate"].get("budget_seconds")
            and q["time_estimate"]["seconds"] >= NEAR_BUDGET * q["time_estimate"]["budget_seconds"]]

    folder = dest / "rescue"
    folder.mkdir(exist_ok=True)
    for stale in folder.glob("rescue-query*"):   # an earlier rescue of this report, possibly another direction
        stale.unlink()
    for q in queries:
        (folder / f"rescue-query-{q['direction']}.oql").write_text(q["oql"] + "\n", encoding="utf-8", newline="\n")
        write(folder / f"rescue-query-{q['direction']}.json", q["oqo"])
    gate = f"topic:{','.join(topics)}" if topics else "any-block"
    warnings = [w for q in queries for w in q["warnings"]]
    notes = []
    if not single:
        notes.append(f"Run as {len(queries)} single-direction queries. The rescue total is at most {upper:,}; "
                     "works found by both are deduplicated by work ID on export.")
    if slow:
        notes.append("OpenAlex estimates the " + " and ".join(slow) + " query close to its time budget; "
                     "reruns may time out. A topic gate or narrower limits make it lighter.")
    manifest = {
        "at": now(),
        "report": dest.name,
        "direction": direction,
        "gate": gate,
        "topics": list(topics or []),
        "corpus": corpus,
        "include_pulled": api.include_pulled,
        "final_count": final_count,
        "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct,
        "rescue_count": rescue_count,
        "rescue_count_upper_bound": upper,
        "queries": [{k: q[k] for k in ("direction", "oql", "oqo", "count", "time_estimate")} for q in queries],
        "warnings": warnings,
        "hits_all_sets": sorted(rescue_hits),
        "rescued_all_sets": sorted(rescue_hits - final_hits),
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
        "notes": notes,
        "note": "Supplementary citation-set rescue for works lacking abstracts in OpenAlex. "
                "Report separately from the primary Boolean search in PRISMA flow diagrams; "
                "deduplicate by OpenAlex Work ID against the primary export.",
    }
    write(folder / "rescue-manifest.json", manifest)
    return {
        "report": dest.name,
        "rescue_dir": str(folder),
        "direction": direction,
        "gate": gate,
        "corpus": corpus,
        "final_count": final_count,
        "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct,
        "rescue_count": rescue_count,
        "rescue_count_upper_bound": upper,
        "counts_by_direction": {q["direction"]: q["count"] for q in queries},
        "oql": [q["oql"] for q in queries] if not single else queries[0]["oql"],
        "warnings": warnings,
        "notes": notes,
        "development_rescued": dev_rescued,
        "development_overlap": dev_overlap,
        "usage": manifest["usage"],
        "next": f"Run `export --report {dest.name} --rescue --estimate`, then `export --report {dest.name} --rescue` "
                "to download the rescue records for deduplication.",
    }

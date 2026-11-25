"""Optional post-delivery citation-set rescue for works lacking abstracts in OpenAlex."""
from .api import PULLED_VIEWS, Error, check_translation, normalize_query, now
from .export import delivered
from .records import DEVELOPMENT
from .store import write
from .strategy import pulled_view, rescue_expectation, rescue_oql

MAX_SET_WORKS = 450_000


def rescue(run, api, report_id=None, direction="both", topics=None):
    """Measure missing-abstract share on a delivered report and run citation-set rescue.

    Saves reproducible rescue artifacts under reports/<report-id>/rescue/ without modifying
    the delivered Boolean query. Only development records are shown in hit diagnostics.
    """
    dest, archive, final_oqo = delivered(run, report_id)
    ev = archive["evaluation"]
    strategy = ev["strategy"]
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]   # the delivered search's view
    final_count = ev["final"]["count"]
    if final_count > MAX_SET_WORKS:
        raise Error(f"Delivered query has {final_count:,} works, exceeding OpenAlex's {MAX_SET_WORKS:,}-work set limit.")

    # Missing-abstract share of the delivered Boolean query
    no_abs_oqo = dict(final_oqo, filter_rows=[*final_oqo.get("filter_rows", []),
                                              {"column_id": "has_abstract", "value": False}])
    no_abs_res = api.run(no_abs_oqo)
    final_no_abs = no_abs_res["count"]
    final_no_abs_pct = round(100 * final_no_abs / final_count, 1) if final_count else 0.0

    # Compile, validate, and execute the conditional no-abstract citation-set rescue query
    query = rescue_oql(strategy, direction=direction, topics=topics)
    parsed = api.validate(query)
    # Same guard as the primary search, applied inside the nested citation sets too.
    check_translation(parsed["oqo"], strategy.get("corpus", "core"),
                      *rescue_expectation(strategy, direction=direction, topics=topics))
    result = api.run(parsed["oql"])
    if normalize_query(result["oqo"]) != normalize_query(parsed["oqo"]):
        raise Error("The executed rescue query differs from the validated query object.")

    # Intersect against resolved known works; archive all sets, return development only
    records = archive.get("records") or run.get("records")
    resolved_ids = {k for k, v in records.items() if v.get("work")}
    dev_ids = {k for k, v in records.items() if v.get("role") in DEVELOPMENT and v.get("work")}
    final_hits = set(ev["final"]["hits"])
    rescue_hits = api.members(result["oqo"], resolved_ids) if (result["count"] and resolved_ids) else set()
    dev_rescued = sorted((rescue_hits - final_hits) & dev_ids)
    dev_overlap = sorted((rescue_hits & final_hits) & dev_ids)

    folder = dest / "rescue"
    folder.mkdir(exist_ok=True)
    (folder / "rescue-query.oql").write_text(result["oql"] + "\n", encoding="utf-8", newline="\n")
    write(folder / "rescue-query.json", result["oqo"])
    gate = f"topic:{','.join(topics)}" if topics else "any-block"
    manifest = {
        "at": now(),
        "report": dest.name,
        "direction": direction,
        "gate": gate,
        "topics": list(topics or []),
        "corpus": strategy.get("corpus", "core"),
        "include_pulled": api.include_pulled,
        "final_count": final_count,
        "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct,
        "rescue_count": result["count"],
        "oql": result["oql"],
        "oqo": result["oqo"],
        "warnings": parsed["validation"].get("warnings", []),
        "hits_all_sets": sorted(rescue_hits),
        "rescued_all_sets": sorted(rescue_hits - final_hits),
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
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
        "corpus": manifest["corpus"],
        "final_count": final_count,
        "final_without_abstract": final_no_abs,
        "final_without_abstract_pct": final_no_abs_pct,
        "rescue_count": result["count"],
        "oql": result["oql"],
        "warnings": manifest["warnings"],
        "development_rescued": dev_rescued,
        "development_overlap": dev_overlap,
        "usage": manifest["usage"],
        "next": f"Run `export --report {dest.name} --rescue` to download the rescue records for deduplication.",
    }

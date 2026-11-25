"""Draft PRISMA-S reporting items from the workspace record.

PRISMA-S: Rethlefsen et al. (2021), Systematic Reviews 10:39, https://doi.org/10.1186/s13643-020-01542-z.
The item names and wording below follow its Table 1 (16 items). The tool fills what its own record
can support and marks the rest for the reviewer; it never reports on searches it did not run.
"""
import json
from collections import Counter

FILLED, PARTLY, REVIEWER, NA = "filled", "partly filled", "reviewer to complete", "not applicable"
CITATION = "Rethlefsen et al. (2021), https://doi.org/10.1186/s13643-020-01542-z"
CORPUS = {"core": "core (OpenAlex's default set of works)", "all": "all corpora (core plus the expansion corpus)",
          "expansion": "expansion corpus only"}


def limits_text(limits):
    parts = []
    if "from_date" in limits or "to_date" in limits:
        parts.append(f"publication date {limits.get('from_date', 'any')} to {limits.get('to_date', 'any')}")
    for key, label in (("language", "language"), ("type", "work type"), ("topic", "OpenAlex topic")):
        if key in limits:
            parts.append(f"{label} in ({', '.join(limits[key])})")
    if "has_abstract" in limits:
        parts.append("works with an abstract" if limits["has_abstract"] else "works without an abstract")
    return "; ".join(parts)


def discovery(run):
    """Development discovery routes recorded in the workspace (neighbors and sample batches)."""
    folder = run.root / "discovery"
    batches = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []
    screening = run.get("screening") if (run.root / "screening.json").is_file() else {}
    included = sum(1 for e in screening.values() if e["decision"] == "include")
    return batches, screening, included


def items(run, fresh, critic, scope_note):
    strategy = fresh["strategy"]
    limits = strategy.get("limits", {})
    pulled = fresh["pulled"]
    at = fresh["at"]
    count = fresh["final"]["count"]
    batches, screening, included = discovery(run)
    neighbors = [b for b in batches if b.get("route") == "neighbors"]
    samples = [b for b in batches if b.get("route") == "sample"]
    directions = Counter(b.get("direction") for b in neighbors)
    pulled_text = ("retracted, withdrawn and removed works excluded (include_pulled=false, OpenAlex's default view)"
                   if pulled["view"] == "exclude" else "retracted, withdrawn and removed works included (include_pulled=true)")

    if neighbors:
        routes = ", ".join(f"{n} {d}" for d, n in sorted(directions.items()))
        seeds = len({b.get("seed") for b in neighbors})
        citation = (PARTLY, f"Citation searching was used during strategy development: {len(neighbors)} batch(es) of "
                    f"OpenAlex citation links ({routes}) from {seeds} development work(s), with {len(screening)} candidate "
                    f"decision(s) recorded and {included} included. These informed the strategy; they are not a separate "
                    "set of records in this report. If the optional no-abstract rescue (`rescue`) is run, report it here "
                    "as a supplementary citation search, with its manifest.")
    else:
        citation = (REVIEWER, "No citation searching is recorded in this workspace. If the optional no-abstract rescue "
                    "(`rescue`) is run, report it here as a supplementary citation search; report any other citation "
                    "searching done outside the tool.")

    other = ((PARTLY, f"{len(samples)} random-sample pilot(s) of candidate queries were screened during development to "
              "check precision and terminology; they were not a source of records. Report any other methods.")
             if samples else (REVIEWER, "Report any additional information sources or search methods."))

    if limits:   # PRISMA-S asks for a justification; the scope note may not give one
        limit_status, limit_text = PARTLY, f"Limits applied: {limits_text(limits)}."
    else:
        limit_status, limit_text = FILLED, "No date, language, type or topic limits were applied."
    limit_text += (f" Corpus: {CORPUS.get(fresh['corpus'], fresh['corpus'])}. Pulled works: {pulled_text}."
                   f" Scope decision recorded at approval: \"{scope_note}\"."
                   + (" Confirm that it justifies each limit." if limits else ""))

    review_at = critic.get("at", "")
    return [
        {"item": 1, "topic": "Database name", "status": FILLED,
         "text": "OpenAlex (https://openalex.org), searched through the OpenAlex API's OQL interface "
                 f"(https://api.openalex.org), corpus: {CORPUS.get(fresh['corpus'], fresh['corpus'])}."},
        {"item": 2, "topic": "Multi-database searching", "status": NA,
         "text": "Not applicable: one database (OpenAlex) was searched by this tool."},
        {"item": 3, "topic": "Study registries", "status": REVIEWER,
         "text": "This tool searches no study registries. List any registries searched separately."},
        {"item": 4, "topic": "Online resources and browsing", "status": REVIEWER,
         "text": "Describe any online or print sources searched or browsed outside this tool."},
        {"item": 5, "topic": "Citation searching", "status": citation[0], "text": citation[1]},
        {"item": 6, "topic": "Contacts", "status": REVIEWER,
         "text": "State whether authors, experts or others were contacted for additional studies or data."},
        {"item": 7, "topic": "Other methods", "status": other[0], "text": other[1]},
        {"item": 8, "topic": "Full search strategies", "status": FILLED,
         "text": "The OpenAlex search exactly as run is under 'Search as executed' and in final-query.oql (OQL) and "
                 f"final-query.json (the OpenAlex query object), executed with include_pulled={str(pulled['include_pulled']).lower()}."},
        {"item": 9, "topic": "Limits and restrictions", "status": limit_status, "text": limit_text},
        {"item": 10, "topic": "Search filters", "status": PARTLY,
         "text": "The tool builds no published search filter into the strategy. State whether any block reproduces or "
                 "adapts a published filter, and cite it."},
        {"item": 11, "topic": "Prior work", "status": REVIEWER,
         "text": "State whether search strategies from earlier reviews were adapted or reused, and cite them."},
        {"item": 12, "topic": "Updates", "status": PARTLY,
         "text": "To update, rerun final-query.json against the OpenAlex API (POST https://api.openalex.org/ with the "
                 f"query object and include_pulled={str(pulled['include_pulled']).lower()}), or rerun `report` in this "
                 "workspace. A rerun searches the live index on that date, so counts can change. State the update "
                 "method actually used."},
        {"item": 13, "topic": "Dates of searches", "status": FILLED,
         "text": f"OpenAlex was last searched on {at[:10]} (at {at[11:19]} UTC)."},
        {"item": 14, "topic": "Peer review", "status": PARTLY,
         "text": f"An internal PRESS-structured critique (six domains) was recorded on {review_at[:10]}. It is not "
                 "formal peer review; describe any human peer review of the search."},
        {"item": 15, "topic": "Total records", "status": PARTLY,
         "text": f"OpenAlex: {count:,} records on {at[:10]}. Add the records identified from each other source."},
        {"item": 16, "topic": "Deduplication", "status": PARTLY,
         "text": "Within OpenAlex, records are unique by OpenAlex Work ID; `export` deduplicates by Work ID, including "
                 "across the optional rescue queries. Describe the process and software used to deduplicate records "
                 "across databases and other sources."},
    ]


def section(entries):
    lines = ["## PRISMA-S search reporting checklist (draft)", "",
             f"Draft wording for the 16 PRISMA-S items ({CITATION}). Items marked for the reviewer cover sources "
             "and steps outside this tool.", "",
             "| # | Item | Status | Draft text |", "|---:|---|---|---|"]
    for e in entries:
        text = e["text"].replace("|", "\\|")
        lines.append(f"| {e['item']} | {e['topic']} | {e['status']} | {text} |")
    return lines + [""]

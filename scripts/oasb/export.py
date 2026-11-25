"""Resumable, deduplicated export of a delivered query to CSV, RIS and JSONL."""
import csv
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from .api import PULLED_VIEWS, Error, normalize_query, now, subquery, work_id
from .records import abstract
from .store import digest, write
from .strategy import pulled_view

COLUMNS = ["id", "doi", "title", "authors", "year", "date", "type", "source", "volume", "issue",
           "first_page", "last_page", "abstract", "url", "language", "pmid", "issn", "publisher", "keywords",
           "is_xpac", "is_pulled", "is_retracted"]
RIS_TYPES = {"article": "JOUR", "review": "JOUR", "letter": "JOUR", "editorial": "JOUR", "book": "BOOK",
             "book-chapter": "CHAP", "dissertation": "THES", "dataset": "DATA", "preprint": "UNPB",
             "report": "RPRT", "conference-paper": "CPAPER"}


def row(record):
    location = record.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = record.get("biblio") or {}
    pmid = str((record.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1]
    return {
        "id": work_id(record), "doi": record.get("doi") or "", "title": record.get("display_name") or "",
        "authors": "; ".join((a.get("author") or {}).get("display_name") or "" for a in record.get("authorships") or []),
        "year": record.get("publication_year") or "", "date": record.get("publication_date") or "",
        "type": record.get("type") or "", "source": source.get("display_name") or "",
        "volume": biblio.get("volume") or "", "issue": biblio.get("issue") or "",
        "first_page": biblio.get("first_page") or "", "last_page": biblio.get("last_page") or "",
        "abstract": abstract(record), "url": location.get("landing_page_url") or record.get("doi") or record.get("id"),
        "language": record.get("language") or "", "pmid": pmid,
        "issn": "; ".join(source.get("issn") or ([source["issn_l"]] if source.get("issn_l") else [])),
        "publisher": source.get("host_organization_name") or "",
        "keywords": "; ".join(k.get("display_name") or "" for k in record.get("keywords") or [] if k.get("display_name")),
        "is_xpac": record.get("is_xpac", ""),
        "is_pulled": record.get("is_pulled", ""), "is_retracted": record.get("is_retracted", ""),
    }


def csv_cell(value):
    # Spreadsheet formula injection guard; the original value is kept in JSONL.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def ris(tag, value):
    return f"{tag}  - " + " ".join(str(value).splitlines()) + "\n"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render(db, folder, complete):
    prefix = "results" if complete else "results.partial"
    paths = [folder / (prefix + ext) for ext in (".csv", ".ris", ".jsonl")]
    with paths[0].open("w", newline="", encoding="utf-8") as cf, \
            paths[1].open("w", encoding="utf-8", newline="\n") as rf, \
            paths[2].open("w", encoding="utf-8", newline="\n") as jf:
        writer = csv.DictWriter(cf, fieldnames=COLUMNS)
        writer.writeheader()
        for (raw,) in db.execute("select body from records order by rowid"):
            record = json.loads(raw)
            r = row(record)
            writer.writerow({k: csv_cell(v) for k, v in r.items()})
            jf.write(raw + "\n")
            rf.write(ris("TY", RIS_TYPES.get(r["type"], "GEN")))
            for tag, key in [("ID", "id"), ("TI", "title"), ("PY", "year"), ("DA", "date"), ("JO", "source"),
                             ("VL", "volume"), ("IS", "issue"), ("SP", "first_page"), ("EP", "last_page"),
                             ("AB", "abstract"), ("UR", "url"), ("LA", "language")]:
                if r[key] != "":
                    rf.write(ris(tag, r[key]))
            if r["doi"]:
                rf.write(ris("DO", r["doi"].removeprefix("https://doi.org/")))
            if r["pmid"]:
                rf.write(ris("AN", "PMID:" + r["pmid"]))
            for author in r["authors"].split("; ") if r["authors"] else []:
                # Display names as OpenAlex gives them: reversing to "Last, First" would guess wrongly for
                # many names, and reference managers parse either order.
                rf.write(ris("AU", author))
            for tag, key in [("SN", "issn"), ("KW", "keywords")]:
                for value in r[key].split("; ") if r[key] else []:
                    rf.write(ris(tag, value))
            if r["publisher"]:
                rf.write(ris("PB", r["publisher"]))
            rf.write("ER  - \n\n")
    return {p.name: sha256(p) for p in paths}


def delivered(run, report_id=None):
    folder = run.root / "reports"
    candidates = [p for p in folder.iterdir() if (p / "validation.json").is_file()] if folder.is_dir() else []
    if report_id:
        candidates = [p for p in candidates if p.name == report_id]
    if not candidates:
        raise Error("No delivered report to export. Run report first." if not report_id else "Unknown report ID.")
    def stamp(p):
        return json.loads((p / "validation.json").read_text(encoding="utf-8"))["evaluation"]["at"]
    dest = max(candidates, key=stamp)
    archive = json.loads((dest / "validation.json").read_text(encoding="utf-8"))
    oqo = json.loads((dest / "final-query.json").read_text(encoding="utf-8"))
    oql = (dest / "final-query.oql").read_text(encoding="utf-8").strip()
    final = archive["evaluation"]["final"]
    if normalize_query(oqo) != normalize_query(final["oqo"]) or oql != final["oql"]:
        raise Error("The delivered query files differ from the validation archive. Rerun report.")
    return dest, archive, oqo


PAGE_SIZE = 100
USD_PER_CREDIT = 0.0001  # https://help.openalex.org/access/example-costs/


def columns(obj):
    """Column IDs of every leaf, including those inside nested citation sets."""
    if isinstance(obj, list):
        return [c for v in obj for c in columns(v)]
    if not isinstance(obj, dict):
        return []
    if "column_id" in obj:
        nested = subquery(obj)
        return [obj["column_id"], *(columns(nested.get("filter_rows", [])) if nested else [])]
    return [c for k in ("filter_rows", "filters") for c in columns(obj.get(k))]


def request_cost(oqo, check):
    """Credits for one page. OpenAlex prices per request, not per record."""
    credits = ((check or {}).get("cost") or {}).get("credits")
    if type(credits) is int and credits >= 0:
        return credits, "OpenAlex's cost check for this query (one execution)"
    if any(".search" in c for c in columns(oqo.get("filter_rows", []))):
        return 10, "documented price of a request that searches text (10 credits)"
    return 1, "documented price of a filter-only request (1 credit)"


def rescue_queries(dest):
    """The saved rescue queries, checked against the manifest. Reads older single-query rescues too."""
    rdir = dest / "rescue"
    if not (rdir / "rescue-manifest.json").is_file():
        raise Error("No rescue query found for this report. Run rescue first.")
    manifest = json.loads((rdir / "rescue-manifest.json").read_text(encoding="utf-8"))
    entries = manifest.get("queries") or [{"direction": manifest.get("direction", "both"), "oql": manifest["oql"],
                                           "oqo": manifest["oqo"], "count": manifest["rescue_count"], "legacy": True}]
    queries = []
    for q in entries:
        stem = "rescue-query" if q.get("legacy") else f"rescue-query-{q['direction']}"
        oqo = json.loads((rdir / (stem + ".json")).read_text(encoding="utf-8"))
        oql = (rdir / (stem + ".oql")).read_text(encoding="utf-8").strip()
        if normalize_query(oqo) != normalize_query(q["oqo"]) or oql != q["oql"]:
            raise Error("The rescue query files differ from the rescue manifest. Rerun rescue.")
        queries.append({"label": q["direction"], "oqo": oqo, "oql": oql, "count": q["count"]})
    return queries


def target(run, report_id=None, rescue=False):
    """The delivered (or rescue) queries to export, their folder and validated count."""
    dest, archive, oqo = delivered(run, report_id)
    if rescue:
        queries = rescue_queries(dest)
        folder = run.root / "exports" / (dest.name + "-rescue")
    else:
        final = archive["evaluation"]["final"]
        queries = [{"label": "final", "oqo": oqo, "oql": final["oql"], "count": final["count"]}]
        folder = run.root / "exports" / dest.name
    return dest, archive, queries, folder


def checkpoint_state(folder):
    if not (folder / "checkpoint.sqlite3").is_file():
        return None
    db = sqlite3.connect(folder / "checkpoint.sqlite3")
    try:
        saved = db.execute("select value from state where key='checkpoint'").fetchone()
    except sqlite3.Error:
        saved = None
    db.close()
    return json.loads(saved[0]) if saved else None


def query_hash(queries):
    return digest([normalize_query(q["oqo"]) for q in queries])


def estimate(run, api, report_id=None, rescue=False):
    """Expected export cost without paging. Query translation is free; nothing is downloaded."""
    dest, _, queries, folder = target(run, report_id, rescue)
    state = checkpoint_state(folder)
    if state and state.get("query_hash", query_hash(queries)) != query_hash(queries):
        state = None   # belongs to an earlier query; export will refuse to resume it
    parts, changed_any = [], False
    for i, q in enumerate(queries):
        parsed = api.validate(q["oql"])
        # export pages the stored query object, so a changed translation of the text only
        # makes OpenAlex's cost check describe a different query: fall back to the documented price.
        changed = normalize_query(parsed["oqo"]) != normalize_query(q["oqo"])
        changed_any |= changed
        per_request, basis = request_cost(q["oqo"], None if changed else parsed.get("check"))
        pages = max(1, math.ceil(q["count"] / PAGE_SIZE))
        if state is None:
            remaining = pages
        elif state["complete"] or i < state.get("query_index", 0):
            remaining = 0
        elif i == state.get("query_index", 0):
            remaining = max(1, math.ceil(max(q["count"] - state.get("received_in_query", state["received"]), 0) / PAGE_SIZE))
        else:
            remaining = pages
        parts.append({"label": q["label"], "count": q["count"], "pages": pages, "credits_per_page": per_request,
                      "basis": basis, "remaining_pages": remaining, "translation_changed_since_report": changed})
    pages = sum(p["pages"] for p in parts)
    credits = sum(p["pages"] * p["credits_per_page"] for p in parts)
    remaining_pages = sum(p["remaining_pages"] for p in parts)
    remaining = sum(p["remaining_pages"] * p["credits_per_page"] for p in parts)
    return {
        "report": dest.name, "rescue": rescue, "oql": [q["oql"] for q in queries] if len(queries) > 1 else queries[0]["oql"],
        "validated_count": sum(q["count"] for q in queries), "page_size": PAGE_SIZE, "pages": pages,
        "credits_per_page": parts[0]["credits_per_page"] if len(parts) == 1 else None,
        "basis": parts[0]["basis"] if len(parts) == 1 else "per query, see queries",
        "queries": parts if len(parts) > 1 else None,
        "estimated_credits": credits, "estimated_usd": round(credits * USD_PER_CREDIT, 4),
        "translation_changed_since_report": changed_any,
        "already_received": state["received"] if state else 0, "remaining_pages": remaining_pages,
        "remaining_credits": remaining, "remaining_usd": round(remaining * USD_PER_CREDIT, 4),
        "note": "Estimate from the count validated at delivery (summed over rescue directions, before "
                "deduplication); the live index may hold more or fewer works. Actual credits are recorded "
                "in export-manifest.json.",
    }


def export(run, api, report_id=None, max_pages=None, rescue=False):
    dest, archive, queries, folder = target(run, report_id, rescue)
    api.include_pulled = PULLED_VIEWS[pulled_view(archive["evaluation"]["strategy"])]
    qhash = query_hash(queries)
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "checkpoint.sqlite3")
    db.execute("create table if not exists records (id text primary key, body text not null)")
    db.execute("create table if not exists state (key text primary key, value text not null)")
    saved = db.execute("select value from state where key='checkpoint'").fetchone()
    state = json.loads(saved[0]) if saved else {"report": dest.name, "started_at": now(), "cursor": "*",
                                               "complete": False, "pages": 0, "received": 0, "counts": [],
                                               "seen_cursors": [], "query_index": 0, "received_in_query": 0,
                                               "counts_by_query": {}, "query_hash": qhash}
    if state.get("query_hash", qhash) != qhash:
        db.close()
        raise Error(f"{folder} holds a checkpoint for a different query (for example an earlier rescue direction). "
                    "Move or delete that folder, then rerun export.")
    error, pages_now = None, 0
    try:
        while not state["complete"]:
            if max_pages is not None and pages_now >= max_pages:
                error = f"Stopped after --max-pages {max_pages}; rerun export to resume."
                break
            index, cursor = state.get("query_index", 0), state["cursor"]
            seen_key = cursor if index == 0 else f"{index}:{cursor}"
            page = api.page(queries[index]["oqo"], cursor)
            nxt = page["next_cursor"]
            if nxt and (nxt == cursor or (nxt if index == 0 else f"{index}:{nxt}") in state["seen_cursors"]):
                raise Error("Repeated cursor; export stopped to prevent an infinite loop.")
            if not page["rows"] and nxt:
                raise Error("Empty page with a continuation cursor; export remains incomplete.")
            label = queries[index]["label"]
            by_query = dict(state.get("counts_by_query", {}))
            by_query[label] = sorted({*by_query.get(label, []), page["count"]})
            last = nxt is None and index + 1 == len(queries)
            new = {**state, "pages": state["pages"] + 1, "received": state["received"] + len(page["rows"]),
                   "cursor": nxt if nxt is not None or last else "*", "complete": last,
                   "query_index": index + 1 if nxt is None and not last else index,
                   "received_in_query": 0 if nxt is None else state.get("received_in_query", 0) + len(page["rows"]),
                   "counts": sorted({*state["counts"], page["count"]}) if len(queries) == 1 else state["counts"],
                   "counts_by_query": by_query, "seen_cursors": [*state["seen_cursors"], seen_key], "query_hash": qhash}
            with db:
                for r in page["rows"]:
                    db.execute("insert or ignore into records values (?, ?)",
                               (work_id(r), json.dumps(r, ensure_ascii=False)))
                db.execute("insert or replace into state values ('checkpoint', ?)", (json.dumps(new),))
            state, pages_now = new, pages_now + 1
    except (Error, KeyboardInterrupt) as exc:
        error = str(exc) or "Interrupted"
    finally:
        unique = db.execute("select count(*) from records").fetchone()[0]
        files = render(db, folder, state["complete"])
        db.close()
    validated = sum(q["count"] for q in queries)
    single = len(queries) == 1
    manifest = {
        "at": now(), "report": dest.name, "rescue": rescue,
        "oql": queries[0]["oql"] if single else [q["oql"] for q in queries],
        "include_pulled": api.include_pulled, "complete": state["complete"],
        "error": error, "pages": state["pages"], "received": state["received"], "unique_records": unique,
        "validated_count": validated, "counts_seen_while_paging": state["counts"] if single else state.get("counts_by_query", {}),
        "count_discrepancy": (any(c != unique for c in [validated, *state["counts"]]) if single else None)
                             if state["complete"] else None,
        "duplicates_across_queries": (state["received"] - unique) if not single and state["complete"] else None,
        "workspace_changed_since_report": run.fingerprint() != archive["evaluation"]["input_hash"],
        "files": files, "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
        "note": "Cursor traversal of a changing index, not a historical snapshot. CSV formula-like cells are "
                "escaped; original values are in JSONL. Abstracts and metadata may be incomplete.",
    }
    write(folder / "export-manifest.json", manifest)
    return dict(manifest, directory=str(folder))

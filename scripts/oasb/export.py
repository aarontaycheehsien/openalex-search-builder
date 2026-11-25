"""Resumable, deduplicated export of a delivered query to CSV, RIS and JSONL."""
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from .api import Error, normalize_query, now, work_id
from .records import abstract
from .store import write

COLUMNS = ["id", "doi", "title", "authors", "year", "date", "type", "source", "volume", "issue",
           "first_page", "last_page", "abstract", "url", "language", "pmid", "is_xpac"]
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
        "language": record.get("language") or "", "pmid": pmid, "is_xpac": record.get("is_xpac", ""),
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
                rf.write(ris("AU", author))
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


def export(run, api, report_id=None, max_pages=None, rescue=False):
    dest, archive, oqo = delivered(run, report_id)
    if rescue:
        rdir = dest / "rescue"
        if not (rdir / "rescue-manifest.json").is_file():
            raise Error("No rescue query found for this report. Run rescue first.")
        rmanifest = json.loads((rdir / "rescue-manifest.json").read_text(encoding="utf-8"))
        oqo = json.loads((rdir / "rescue-query.json").read_text(encoding="utf-8"))
        roql = (rdir / "rescue-query.oql").read_text(encoding="utf-8").strip()
        if normalize_query(oqo) != normalize_query(rmanifest["oqo"]) or roql != rmanifest["oql"]:
            raise Error("The rescue query files differ from the rescue manifest. Rerun rescue.")
        folder = run.root / "exports" / (dest.name + "-rescue")
        validated = rmanifest["rescue_count"]
        oql_text = rmanifest["oql"]
    else:
        folder = run.root / "exports" / dest.name
        validated = archive["evaluation"]["final"]["count"]
        oql_text = archive["evaluation"]["final"]["oql"]
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "checkpoint.sqlite3")
    db.execute("create table if not exists records (id text primary key, body text not null)")
    db.execute("create table if not exists state (key text primary key, value text not null)")
    saved = db.execute("select value from state where key='checkpoint'").fetchone()
    state = json.loads(saved[0]) if saved else {"report": dest.name, "started_at": now(), "cursor": "*",
                                               "complete": False, "pages": 0, "received": 0, "counts": [],
                                               "seen_cursors": []}
    error, pages_now = None, 0
    try:
        while not state["complete"]:
            if max_pages is not None and pages_now >= max_pages:
                error = f"Stopped after --max-pages {max_pages}; rerun export to resume."
                break
            cursor = state["cursor"]
            page = api.page(oqo, cursor)
            nxt = page["next_cursor"]
            if nxt and (nxt == cursor or nxt in state["seen_cursors"]):
                raise Error("Repeated cursor; export stopped to prevent an infinite loop.")
            if not page["rows"] and nxt:
                raise Error("Empty page with a continuation cursor; export remains incomplete.")
            new = {**state, "pages": state["pages"] + 1, "received": state["received"] + len(page["rows"]),
                   "cursor": nxt, "complete": nxt is None,
                   "counts": sorted({*state["counts"], page["count"]}), "seen_cursors": [*state["seen_cursors"], cursor]}
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
    manifest = {
        "at": now(), "report": dest.name, "rescue": rescue, "oql": oql_text, "complete": state["complete"],
        "error": error, "pages": state["pages"], "received": state["received"], "unique_records": unique,
        "validated_count": validated, "counts_seen_while_paging": state["counts"],
        "count_discrepancy": any(c != unique for c in [validated, *state["counts"]]) if state["complete"] else None,
        "workspace_changed_since_report": run.fingerprint() != archive["evaluation"]["input_hash"],
        "files": files, "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
        "note": "Cursor traversal of a changing index, not a historical snapshot. CSV formula-like cells are "
                "escaped; original values are in JSONL. Abstracts and metadata may be incomplete.",
    }
    write(folder / "export-manifest.json", manifest)
    return dict(manifest, directory=str(folder))

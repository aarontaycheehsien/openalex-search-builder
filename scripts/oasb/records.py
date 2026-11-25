"""Known-work identity, eligibility, provenance and development-only vocabulary."""
import math
import re
from collections import Counter

from .api import Error, work_id, now
from .strategy import CORPORA

ROLES = ("seed", "relevant", "validation", "benchmark")
DEVELOPMENT = {"seed", "relevant"}
PROTECTED = ("validation", "benchmark")


def exposure(run):
    """Earlier deliveries whose audit showed validation or benchmark results."""
    return run.get("exposure")["events"] if (run.root / "exposure.json").is_file() else []


def exposed_holdouts(run, records):
    """Protected records whose retrieval an earlier report already revealed."""
    protected = {k for k, r in records.items() if r["role"] in PROTECTED}
    return sorted(protected & {i for e in exposure(run) for i in e["ids"]})


def add(api, records, raw, role, source):
    alias, item = api.resolve(raw)
    key = work_id(item) if item is not None else alias
    previous = next((k for k, r in records.items() if alias in r["aliases"]), None)
    existing = [records[k] for k in dict.fromkeys([previous, key]) if k in records]
    if any(r["role"] != role for r in existing):
        raise Error(f"{alias} already belongs to another role. Use develop to retire a holdout.")
    if previous and item is None and records[previous]["work"]:
        raise Error(f"Previously resolved record {alias} now returned 404. Reconcile its identity.")
    aliases = {alias}
    sources = {source}
    for record in existing:
        aliases.update(record["aliases"])
        sources.update(record["sources"])
    if previous and previous != key:
        del records[previous]
    records[key] = {**(existing[-1] if existing else {}), "role": role, "aliases": sorted(aliases), "sources": sorted(sources), "work": item}
    return {"identifier": alias, "work_id": key if item else None, "role": role}


def wilson(hits, n, z=1.959964):
    """95% Wilson score interval for a proportion (Wilson, 1927). Known-work sets are small:
    3 of 3 retrieved is compatible with a true relative recall of about 44%."""
    if not n:
        return None
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def interval_text(s):
    ci = s.get("relative_recall_ci95")
    if s.get("relative_recall") is None or not ci:
        return "no estimate"
    return f"{100 * s['relative_recall']:.0f}% (95% CI {100 * ci[0]:.0f}–{100 * ci[1]:.0f}%)"


def summary(records, hits):
    result = {}
    for role in ROLES:
        group = {k: r for k, r in records.items() if r["role"] == role}
        resolved = {k for k, r in group.items() if r["work"]}
        found = sorted(resolved & hits)
        result[role] = {"eligible": len(group), "resolved": len(resolved), "retrieved": len(found),
                        "coverage": len(resolved) / len(group) if group else None,
                        "relative_recall": len(found) / len(resolved) if resolved else None,
                        "relative_recall_ci95": wilson(len(found), len(resolved)),
                        "unresolved": sorted(set(group) - resolved), "missed": sorted(resolved - hits),
                        "hits": found}
    return result


def abstract(work):
    index = work.get("abstract_inverted_index") or {}
    return " ".join(w for _, w in sorted((pos, word) for word, positions in index.items() for pos in positions))


def mining(records):
    stop = set("the of in and for to on with as a an by from is were was are at using it this that or we be our between".split())
    words, phrases, keywords = Counter(), Counter(), Counter()
    docs = []
    for wid, record in records.items():
        if record["role"] not in DEVELOPMENT or not record["work"]:
            continue
        work = record["work"]
        body = abstract(work)
        docs.append({"id": wid, "title": work.get("display_name"), "abstract": body})
        vocabulary, pairs = set(), set()
        for field in (work.get("display_name") or "", body):
            tokens = re.findall(r"[^\W\d_][\w-]*", field.casefold())
            vocabulary.update(t for t in tokens if len(t) > 2 and t not in stop)
            pairs.update(a + " " + b for a, b in zip(tokens, tokens[1:]) if a not in stop and b not in stop)
        words.update(vocabulary)
        phrases.update(pairs)
        keywords.update({k["display_name"] for k in work.get("keywords") or [] if k.get("display_name")})
    def top(counter):
        return sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:40]
    return {"records": docs, "words": top(words), "bigrams": top(phrases), "openalex_keywords": top(keywords)}


DISCOVERY_FIELDS = "id,display_name,publication_year,abstract_inverted_index"


def discover(run, api, args):
    protected = {k for k, v in run.get("records").items() if v["role"] not in DEVELOPMENT}
    if args.action == "neighbors":
        records = run.get("records")
        wid = args.work_id.upper()
        if wid not in records or records[wid]["role"] not in DEVELOPMENT or not records[wid]["work"]:
            raise Error("Choose a resolved development record as the citation starting point.")
        corpus = run.get("strategy").get("corpus", "core")
        head = f"get works{CORPORA.get(corpus, '')}"
        if args.direction == "citing":
            query = f"{head} citing []({wid})"
        elif args.direction == "references":
            query = f"{head} cited by []({wid})"
        else:
            query = f"{head} where it's related to ([]({wid}))"
        parsed = api.validate(query)
        obj = dict(parsed["oqo"])
        if obj.get("get_rows") != "works" or set(obj) - {"get_rows", "filter_rows", "sort_by", "corpus"}:
            raise Error("Discovery requires a query that lists works.")
        fetch_all = bool(getattr(args, "all", False))
        limit = None if fetch_all else args.size
        max_pages = getattr(args, "max_pages", None)
        cursor = getattr(args, "cursor", None) or "*"
        seen_cursors, seen_ids, rows = set(), set(), []
        pages, total, oql_echo, oqo_echo = 0, None, parsed["oql"], obj
        while cursor is not None:
            if max_pages is not None and pages >= max_pages:
                break
            if limit is not None and len(rows) >= limit:
                break
            per_page = 100 if limit is None else min(100, limit - len(rows))
            page = api.page(obj, cursor=cursor, size=per_page, select=DISCOVERY_FIELDS)
            nxt = page["next_cursor"]
            if nxt and (nxt == cursor or nxt in seen_cursors):
                raise Error("Repeated cursor; neighbor paging stopped to prevent an infinite loop.")
            if not page["rows"] and nxt:
                raise Error("Empty page with a continuation cursor; neighbor paging stopped.")
            seen_cursors.add(cursor)
            pages += 1
            total = page["count"]
            if page.get("oql"):
                oql_echo = page["oql"]
            if page.get("oqo"):
                oqo_echo = page["oqo"]
            for w in page["rows"]:
                r_id = work_id(w)
                if r_id in protected or r_id in seen_ids:
                    continue
                seen_ids.add(r_id)
                rows.append({"id": r_id, "title": w.get("display_name"), "year": w.get("publication_year"),
                             "abstract": abstract(w)})
                if limit is not None and len(rows) >= limit:
                    break
            cursor = nxt
        complete = cursor is None
        data = {"at": now(), "route": "neighbors", "direction": args.direction, "seed": wid,
                "oql": oql_echo, "oqo": oqo_echo, "total": total, "pages": pages,
                "complete": complete, "next_cursor": cursor, "rows": rows}
        uid = run.snapshot("discovery", data)
        return {"discovery": uid, "candidates": rows, "total": total, "pages": pages,
                "complete": complete, "next_cursor": cursor,
                "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)}}

    parsed = api.validate(args.oql)
    obj = dict(parsed["oqo"])
    if obj.get("get_rows") != "works" or set(obj) - {"get_rows", "filter_rows", "sort_by", "corpus"}:
        raise Error("Discovery requires a query that lists works.")
    obj.update(sample=args.size, seed=str(args.seed))
    result = api.run(obj, args.size, DISCOVERY_FIELDS)
    rows = [{"id": work_id(w), "title": w.get("display_name"), "year": w.get("publication_year"),
             "abstract": abstract(w)} for w in result["rows"] if work_id(w) not in protected]
    data = {"at": now(), "route": args.action, "oql": result["oql"], "oqo": result["oqo"], "rows": rows}
    uid = run.snapshot("discovery", data)
    return {"discovery": uid, "candidates": rows}

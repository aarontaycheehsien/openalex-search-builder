"""Known-work identity, eligibility, provenance and development-only vocabulary."""
import re
from collections import Counter

from .api import Error, work_id, now
from .strategy import CORPORA

ROLES = ("seed", "relevant", "validation", "benchmark")
DEVELOPMENT = {"seed", "relevant"}


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


def summary(records, hits):
    result = {}
    for role in ROLES:
        group = {k: r for k, r in records.items() if r["role"] == role}
        resolved = {k for k, r in group.items() if r["work"]}
        found = sorted(resolved & hits)
        result[role] = {"eligible": len(group), "resolved": len(resolved), "retrieved": len(found),
                        "coverage": len(resolved) / len(group) if group else None,
                        "relative_recall": len(found) / len(resolved) if resolved else None,
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


def discover(run, api, args):
    if args.action == "neighbors":
        records = run.get("records")
        wid = args.work_id.upper()
        if wid not in records or records[wid]["role"] not in DEVELOPMENT or not records[wid]["work"]:
            raise Error("Choose a resolved development record as the citation starting point.")
        direction = "citing" if args.direction == "citing" else "cited by"
        corpus = run.get("strategy").get("corpus", "core")
        query = f"get works{CORPORA.get(corpus, '')} {direction} []({wid})"
    else:
        query = args.oql
    parsed = api.validate(query)
    obj = dict(parsed["oqo"])
    if obj.get("get_rows") != "works" or set(obj) - {"get_rows", "filter_rows", "sort_by", "corpus"}:
        raise Error("Discovery requires a query that lists works.")
    if args.action == "sample":
        obj.update(sample=args.size, seed=str(args.seed))
    result = api.run(obj, args.size, "id,display_name,publication_year,abstract_inverted_index")
    protected = {k for k, v in run.get("records").items() if v["role"] not in DEVELOPMENT}
    rows = [{"id": work_id(w), "title": w.get("display_name"), "year": w.get("publication_year"),
             "abstract": abstract(w)} for w in result["rows"] if work_id(w) not in protected]
    data = {"at": now(), "route": args.action, "oql": result["oql"], "oqo": result["oqo"], "rows": rows}
    uid = run.snapshot("discovery", data)
    return {"discovery": uid, "candidates": rows}

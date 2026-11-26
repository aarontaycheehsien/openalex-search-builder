"""Known-work identity, eligibility, provenance and development-only vocabulary."""
import math
import re
from collections import Counter

from .api import PULLED_VIEWS, Error, check_translation, work_id, now
from .strategy import CORPORA, check, core, excluded, expected_tokens, final_query, pulled_view

ROLES = ("seed", "relevant", "validation", "benchmark")
DEVELOPMENT = {"seed", "relevant"}
PROTECTED = ("validation", "benchmark")


def discovered_ids(run):
    """IDs shown in discovery batches, whether or not they were subsequently screened."""
    return {row["id"] for path in (run.root / "discovery").glob("*.json")
            for row in run.get("discovery/" + path.stem)["rows"]}


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
# OpenAlex related_works: up to 10 nearest neighbours by text embeddings (since October 2026); never reviews,
# retracted works or paratext. https://help.openalex.org/data/works/attributes/#related_works
RELATED_MAX = 10
RELATED_NOTE = ("OpenAlex related works are at most 10 nearest neighbours by text similarity (since October 2026) and "
                "never include reviews or retracted works: a text route like keyword search, not citation searching, "
                "and of no use when the eligible studies are reviews.")
LINK_NOTE = ("Citation links are OpenAlex's matched references: about 82% of references to works in OpenAlex are "
             "linked, unmatched ones are dropped, and many records have no reference list "
             "(https://help.openalex.org/data/works/citations/).")


def route_kind(direction):
    return "text-similarity" if direction == "related" else "citation"


def date_window(strategy, any_date=False):
    """The strategy's publication-date limits as filter rows, so discovery shows only works inside the
    eligibility window. Works outside it cannot be eligible; in a blind benchmark the source review,
    published after the cutoff, would otherwise turn up among citation neighbours."""
    if any_date:
        return []
    limits = strategy.get("limits", {})
    return [{"column_id": column, "value": limits[key]}
            for key, column in (("from_date", "from_publication_date"), ("to_date", "to_publication_date"))
            if key in limits]


def windowed(obj, window):
    rows = list(obj.get("filter_rows", []))
    return dict(obj, filter_rows=rows + [w for w in window if w not in rows])


def discover(run, api, args):
    protected = {k for k, v in run.get("records").items() if v["role"] not in DEVELOPMENT}
    strategy = run.get("strategy")
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]
    window = date_window(strategy, getattr(args, "any_date", False))
    window_note = {r["column_id"].removesuffix("_publication_date"): r["value"] for r in window} or None
    if args.action == "neighbors":
        records = run.get("records")
        raw_ids = getattr(args, "work_ids", None) or [args.work_id]
        wids = list(dict.fromkeys(w.upper() for w in raw_ids))
        for wid in wids:
            if wid not in records or records[wid]["role"] not in DEVELOPMENT or not records[wid]["work"]:
                raise Error("Choose a resolved development record as the citation starting point.")
        cursor_arg = getattr(args, "cursor", None)
        if len(wids) > 1 and cursor_arg:
            raise Error("--cursor can only be used with a single seed work ID.")
        # A seed with no matched references (or citations) returns nothing: say so before spending a request.
        needed = {"references": "referenced_works_count", "citing": "cited_by_count"}.get(args.direction)
        seed_links, skipped = {}, []
        if needed:
            for wid in wids:
                seed_links[wid] = api.link_counts(wid)
                if seed_links[wid][needed] == 0:
                    skipped.append(wid)
            if skipped and len(skipped) == len(wids):
                other = "citing" if args.direction == "references" else "references"
                raise Error(f"{', '.join(skipped)} {'has' if len(skipped) == 1 else 'have'} no "
                            f"{'matched references' if needed == 'referenced_works_count' else 'citations'} in OpenAlex, "
                            f"so --direction {args.direction} would return nothing. Choose another seed or try "
                            f"--direction {other}. For references, recent open-access papers are the best seeds: "
                            "OpenAlex reads their reference lists from the PDF.")
            wids = [w for w in wids if w not in skipped]
        corpus = strategy.get("corpus", "core")
        head = f"get works{CORPORA.get(corpus, '')}"
        fetch_all = bool(getattr(args, "all", False))
        max_pages = getattr(args, "max_pages", None)

        def fetch_seed(wid, limit, start_cursor="*"):
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
            obj = windowed(obj, window)
            cursor = start_cursor or "*"
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
            return rows, total, pages, cursor, oql_echo, oqo_echo

        if args.direction == "related":   # OpenAlex never returns more than 10 related works
            fetch_all = False
            args.size = min(args.size or RELATED_MAX, RELATED_MAX)
        notes = [RELATED_NOTE if args.direction == "related" else LINK_NOTE]
        if skipped:
            label = "matched references" if needed == "referenced_works_count" else "citations"
            notes.append(f"Skipped {', '.join(skipped)}: no {label} in OpenAlex.")
        if len(wids) == 1:
            wid = wids[0]
            limit = None if fetch_all else args.size
            rows, total, pages, cursor, oql_echo, oqo_echo = fetch_seed(wid, limit, cursor_arg or "*")
            complete = cursor is None
            data = {"at": now(), "route": "neighbors", "direction": args.direction, "route_kind": route_kind(args.direction),
                    "seed": wid, "seed_links": seed_links, "oql": oql_echo, "oqo": oqo_echo, "total": total, "pages": pages,
                    "date_window": window_note, "complete": complete, "next_cursor": cursor, "rows": rows}
            uid = run.snapshot("discovery", data)
            return {"discovery": uid, "seed": wid, "candidates": rows, "total": total, "pages": pages, "date_window": window_note,
                    "route_kind": route_kind(args.direction), "seed_links": seed_links, "skipped_seeds": skipped,
                    "notes": notes, "complete": complete, "next_cursor": cursor,
                    "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)}}

        # Multi-seed snowballing: fetch per seed, deduplicate, and rank co-cited/multi-linked candidates first.
        per_seed_limit = None if fetch_all else max(args.size, 50)
        seed_set = set(wids)
        merged, seed_links = {}, {}
        total_pages, all_complete, per_seed_totals = 0, True, {}
        for wid in wids:
            s_rows, s_total, s_pages, s_cursor, _, _ = fetch_seed(wid, per_seed_limit)
            total_pages += s_pages
            all_complete = all_complete and (s_cursor is None)
            per_seed_totals[wid] = s_total
            for row in s_rows:
                r_id = row["id"]
                if r_id in seed_set:
                    continue
                if r_id not in merged:
                    merged[r_id] = row
                    seed_links[r_id] = []
                if wid not in seed_links[r_id]:
                    seed_links[r_id].append(wid)
        ranked = []
        for r_id, row in merged.items():
            ranked.append(dict(row, matched_seeds=seed_links[r_id], seed_count=len(seed_links[r_id])))
        ranked.sort(key=lambda r: (-r["seed_count"], -(r["year"] or 0), r["id"]))
        rows = ranked if fetch_all else ranked[:args.size]
        data = {"at": now(), "route": "neighbors", "direction": args.direction, "route_kind": route_kind(args.direction),
                "seed": wids[0], "seeds": wids, "seed_links": seed_links, "skipped_seeds": skipped,
                "per_seed_totals": per_seed_totals, "pages": total_pages, "date_window": window_note,
                "complete": all_complete, "rows": rows}
        uid = run.snapshot("discovery", data)
        return {"discovery": uid, "seeds": wids, "candidates": rows, "total_unique": len(ranked), "date_window": window_note,
                "route_kind": route_kind(args.direction), "seed_links": seed_links, "skipped_seeds": skipped, "notes": notes,
                "pages": total_pages, "complete": all_complete,
                "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)}}

    if getattr(args, "from_strategy", False):
        check(strategy)
        query, expected = final_query(strategy)
        parsed = api.validate(query)
        check_translation(parsed["oqo"], strategy.get("corpus", "core"), expected,
                          expected_tokens(core(strategy), strategy.get("limits", {}), excluded(strategy)))
    else:
        parsed = api.validate(args.oql)
    obj = dict(parsed["oqo"])
    if obj.get("get_rows") != "works" or set(obj) - {"get_rows", "filter_rows", "sort_by", "corpus"}:
        raise Error("Discovery requires a query that lists works.")
    obj = windowed(obj, window)
    obj.update(sample=args.size, seed=str(args.seed))
    result = api.run(obj, args.size, DISCOVERY_FIELDS)
    rows = [{"id": work_id(w), "title": w.get("display_name"), "year": w.get("publication_year"),
             "abstract": abstract(w)} for w in result["rows"] if work_id(w) not in protected]
    data = {"at": now(), "route": args.action, "route_kind": "text", "oql": result["oql"], "oqo": result["oqo"],
            "date_window": window_note, "rows": rows}
    uid = run.snapshot("discovery", data)
    return {"discovery": uid, "candidates": rows, "date_window": window_note}

#!/usr/bin/env python3
"""Evaluator-only replay diagnostics on non-frozen benchmark fixtures (no LLM calls).

Modes:
  gold-profile CASE
      Free (0-credit single-work lookups). Profiles resolved gold works in OpenAlex:
      missing abstracts, keyword/fulltext availability on abstract-less gold works,
      work types, primary vs. all-topic concentration, and internal gold citation links.

  impact CASE (--strategy FILE | --report DIR)
      Runs `eval --term-impact` against the fixture's gold set (requires OPENALEX_API_KEY)
      to measure `records_only_here` and `gold_only_here` per term.

  keywords CASE (--strategy FILE | --report DIR)
      Tests switching each title / title-abstract search block one at a time to
      `title-abstract-keywords`, reporting volume ratio and gold works gained.

  rescue CASE --report DIR [--min-links 1,2,3]
      Compares reference-list rescue across `--min-links` thresholds and
      `--keywords-gate` (off vs. on) against the fixture's resolved gold set.

Frozen held-out fixtures (`suite == "frozen"`) are refused in all modes.
"""
import argparse
from collections import Counter
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))

import bench  # noqa: E402
from oasb.api import API, PULLED_VIEWS, Error, check_translation, identifier, load_dotenv, work_id  # noqa: E402
from oasb.evaluate import evaluate  # noqa: E402
from oasb.rescue import LOOKUP, date_filters, reference_lists  # noqa: E402
from oasb.store import Run  # noqa: E402
from oasb.strategy import (check, core, excluded, expected_leaves, expected_tokens,
                           oql, parse_combine, pulled_view, rescue_gate)  # noqa: E402

PROFILE_FIELDS = ("id,ids,display_name,publication_year,publication_date,type,language,"
                  "abstract_inverted_index,keywords,primary_topic,topics,has_fulltext,"
                  "referenced_works,cited_by_count,is_pulled,is_retracted")


def load_non_frozen(case_id):
    case = bench.load_case(case_id)
    if case.get("suite") == "frozen":
        raise Error(f"Refusing frozen held-out fixture {case_id!r}: replay reads the gold answer key.")
    return case


def load_strategy(strategy_file=None, report_dir=None):
    if bool(strategy_file) == bool(report_dir):
        raise Error("Pass exactly one of --strategy or --report.")
    if report_dir:
        archive = json.loads((Path(report_dir) / "validation.json").read_text(encoding="utf-8"))
        return json.loads(json.dumps(archive["evaluation"]["strategy"]))
    return json.loads(Path(strategy_file).read_text(encoding="utf-8"))


def resolve_gold(case, api, fields=None):
    """Resolve a non-frozen fixture's gold rows using 0-credit single-work lookups."""
    items, unresolved = {}, []
    for row in case["gold"]:
        found = None
        for raw in row["ids"]:
            alias = identifier(raw)
            if fields:
                from urllib.parse import quote
                found = api.request("/works/" + quote(alias, safe=":/"), {"select": fields}, allow_missing=True)
            else:
                _, found = api.resolve(raw)
            if found:
                break
        if found is None:
            unresolved.append(row["row"])
            continue
        items[work_id(found)] = found
    return items, unresolved


def gold_profile(case, api):
    """Zero-credit structural profile of a non-frozen fixture's gold works in OpenAlex."""
    items, unresolved = resolve_gold(case, api, fields=PROFILE_FIELDS)
    cutoff = case["to_date"]
    in_window = {wid: w for wid, w in items.items()
                 if not w.get("publication_date") or w["publication_date"] <= cutoff}
    gold_ids = set(in_window)
    no_abs = [w for w in in_window.values() if not w.get("abstract_inverted_index")]
    no_abs_kw = [w for w in no_abs if w.get("keywords")]
    no_abs_ft = [w for w in no_abs if w.get("has_fulltext")]
    types = Counter(w.get("type") or "unknown" for w in in_window.values())
    langs = Counter(w.get("language") or "unknown" for w in in_window.values())
    prim_topics = Counter()
    any_topics = Counter()
    topic_names = {}
    gold_cite_counts = Counter()
    linked_gold = set()
    for wid, w in in_window.items():
        pt = w.get("primary_topic") or {}
        if pt.get("id"):
            tid = pt["id"].rsplit("/", 1)[-1]
            prim_topics[tid] += 1
            topic_names[tid] = pt.get("display_name")
        seen_t = set()
        for t in w.get("topics") or []:
            if t.get("id"):
                tid = t["id"].rsplit("/", 1)[-1]
                seen_t.add(tid)
                topic_names[tid] = t.get("display_name")
        for tid in seen_t:
            any_topics[tid] += 1
        refs = {r.rsplit("/", 1)[-1].upper() for r in (w.get("referenced_works") or [])} & gold_ids - {wid}
        if refs:
            linked_gold.add(wid)
            linked_gold |= refs
        for r in refs:
            gold_cite_counts[r] += 1

    n = len(in_window)
    return {
        "case": case["id"],
        "suite": case.get("suite", "core"),
        "cutoff": cutoff,
        "gold_rows": len(case["gold"]),
        "resolved": len(items),
        "in_window": n,
        "unresolved_rows": unresolved,
        "pulled_gold": sum(1 for w in in_window.values() if w.get("is_pulled")),
        "without_abstract": len(no_abs),
        "without_abstract_pct": round(100 * len(no_abs) / n, 1) if n else 0.0,
        "without_abstract_with_keywords": len(no_abs_kw),
        "without_abstract_with_fulltext": len(no_abs_ft),
        "with_keywords_overall": sum(1 for w in in_window.values() if w.get("keywords")),
        "with_fulltext_overall": sum(1 for w in in_window.values() if w.get("has_fulltext")),
        "with_matched_references": sum(1 for w in in_window.values() if w.get("referenced_works")),
        "gold_linked_to_other_gold": len(linked_gold),
        "gold_cited_by_2plus_gold": sum(1 for c in gold_cite_counts.values() if c >= 2),
        "types": dict(types.most_common()),
        "languages": dict(langs.most_common()),
        "top_primary_topics": [{"id": t, "name": topic_names.get(t), "gold_works": c,
                                "share_pct": round(100 * c / n, 1)} for t, c in prim_topics.most_common(5)],
        "top_any_topics": [{"id": t, "name": topic_names.get(t), "gold_works": c,
                            "share_pct": round(100 * c / n, 1)} for t, c in any_topics.most_common(5)],
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
    }


def build_replay_workspace(tmp, case, strategy, api):
    limits = dict(strategy.get("limits") or {})
    limits.setdefault("to_date", case["to_date"])
    strategy = dict(strategy, limits=limits)
    ws = Run(Path(tmp) / "replay")
    ws.init(case["question"], [], "standard")
    ws.put("strategy", strategy)
    records = {}
    items, unresolved = resolve_gold(case, api)
    for wid, item in sorted(items.items()):
        records[wid] = {"role": "seed", "aliases": [wid], "sources": ["gold-replay"], "work": item}
    ws.put("records", records)
    key = ws.scope_key()
    from oasb.store import digest
    from oasb.api import now
    ws.put("scope", {"at": now(), "hash": digest(key), "key": key, "decision": "replay"})
    return ws, strategy, items, unresolved


def replay_impact(case, strategy, api):
    with tempfile.TemporaryDirectory() as tmp:
        ws, strat, items, unresolved = build_replay_workspace(tmp, case, strategy, api)
        ev = evaluate(ws, api, quick=True, term_impact=True, use_cache=True)
        rows = []
        for r in ev["term_impact"]["terms"]:
            rows.append({
                "block": r["block"],
                "term": r["term"],
                "records_only_here": r["records_only_here"],
                "share_pct": r["share_pct"],
                "gold_only_here": r["development_only_here"],
            })
        return {
            "case": case["id"], "count": ev["final"]["count"],
            "gold_resolved": len(items), "gold_hits": len(ev["final"]["hits"]),
            "unresolved_rows": unresolved, "terms": rows, "usage": ev["usage"],
        }


def replay_keywords(case, strategy, api):
    check(strategy)
    limits = dict(strategy.get("limits") or {})
    limits.setdefault("to_date", case["to_date"])
    strategy = dict(strategy, limits=limits)
    corpus = strategy.get("corpus", "core")
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]
    items, unresolved = resolve_gold(case, api)
    gold_ids = set(items)
    blocks, excludes = core(strategy), excluded(strategy)
    tree = parse_combine(strategy["combine"], [b["id"] for b in blocks]) if "combine" in strategy else None

    def score_blocks(bs):
        q = oql(bs, limits, corpus, excludes, tree)
        parsed = api.validate(q)
        check_translation(parsed["oqo"], corpus, expected_leaves(bs, limits, excludes),
                          expected_tokens(bs, limits, excludes))
        res = api.run(parsed["oql"])
        hits = api.members(res["oqo"], gold_ids) if (res["count"] and gold_ids) else set()
        return res["count"], hits

    base_count, base_hits = score_blocks(blocks)
    trials = []
    for b in blocks:
        field = b.get("field", "title-abstract")
        if field == "title-abstract-keywords" or field == "fulltext":
            continue
        mod = [dict(x, field="title-abstract-keywords") if x["id"] == b["id"] else x for x in blocks]
        cnt, hits = score_blocks(mod)
        gained = sorted(hits - base_hits)
        delta = cnt - base_count
        trials.append({
            "block": b["id"], "field_before": field, "count_after": cnt,
            "volume_ratio": round(cnt / base_count, 3) if base_count else None,
            "gold_gained_count": len(gained), "gold_gained": gained,
            "extra_records_per_gold": round(delta / len(gained)) if gained and delta > 0 else None,
        })
    return {
        "case": case["id"], "base_count": base_count, "base_gold_hits": len(base_hits),
        "gold_resolved": len(gold_ids), "unresolved_rows": unresolved,
        "keyword_trials": trials,
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
    }


def replay_rescue(case, report_dir, min_links_list, api):
    report_dir = Path(report_dir).resolve()
    run = Run(report_dir.parents[1])
    archive = json.loads((report_dir / "validation.json").read_text(encoding="utf-8"))
    strategy = archive["evaluation"]["strategy"]
    final_oqo = archive["evaluation"]["final"]["oqo"]
    corpus = strategy.get("corpus", "core")
    limits = strategy.get("limits", {})
    api.include_pulled = PULLED_VIEWS[pulled_view(strategy)]
    items, _ = resolve_gold(case, api)
    gold_ids = set(items)
    base_hits = api.members(final_oqo, gold_ids) if gold_ids else set()
    lists, source = reference_lists(run, api, report_dir, final_oqo)
    delivered_ids = set(lists)
    cite_counts = Counter(r for refs in lists.values() for r in set(refs) if r not in delivered_ids)
    all_cited = sorted(cite_counts)
    no_abs_all = set()
    for start in range(0, len(all_cited), LOOKUP):
        rows = api.lookup(all_cited[start:start + LOOKUP], ["has_abstract:false", *date_filters(limits)], corpus)
        no_abs_all |= {work_id(r) for r in rows}

    variants = []
    for kw_gate in (False, True):
        gate_oql, lvs, words = rescue_gate(strategy, keywords_gate=kw_gate)
        parsed = api.validate(gate_oql)
        check_translation(parsed["oqo"], corpus, lvs, words)
        passed_all = api.members(parsed["oqo"], no_abs_all, batch_size=LOOKUP) if no_abs_all else set()
        for ml in min_links_list:
            eligible_cited = {r for r, c in cite_counts.items() if c >= ml}
            rescued = passed_all & eligible_cited
            added_gold = sorted((rescued & gold_ids) - base_hits)
            variants.append({
                "keywords_gate": kw_gate,
                "min_links": ml,
                "rescue_count": len(rescued),
                "gold_added_count": len(added_gold),
                "gold_added": added_gold,
                "rescue_records_per_gold": round(len(rescued) / len(added_gold)) if added_gold else None,
            })
    return {
        "case": case["id"], "source": source, "base_gold_hits": len(base_hits),
        "gold_resolved": len(gold_ids), "cited_outside_delivered": len(all_cited),
        "without_abstract_in_window": len(no_abs_all), "variants": variants,
        "usage": {"credits_reported": api.credits, "usd_reported": round(api.cost_usd, 6)},
    }


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("gold-profile")
    c.add_argument("case")
    for name in ("impact", "keywords"):
        c = sub.add_parser(name)
        c.add_argument("case")
        c.add_argument("--strategy", type=Path)
        c.add_argument("--report", type=Path)
    c = sub.add_parser("rescue")
    c.add_argument("case")
    c.add_argument("--report", type=Path, required=True)
    c.add_argument("--min-links", default="1,2,3")
    return p


def main():
    args = parser().parse_args()
    case = load_non_frozen(args.case)
    api = API()
    if args.cmd == "gold-profile":
        out = gold_profile(case, api)
    elif args.cmd == "impact":
        out = replay_impact(case, load_strategy(args.strategy, args.report), api)
    elif args.cmd == "keywords":
        out = replay_keywords(case, load_strategy(args.strategy, args.report), api)
    elif args.cmd == "rescue":
        mls = [int(x) for x in args.min_links.split(",") if x.strip()]
        out = replay_rescue(case, args.report, mls, api)
    else:
        raise Error("Unknown command.")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    load_dotenv(Path.cwd(), ROOT)
    sys.exit(main())

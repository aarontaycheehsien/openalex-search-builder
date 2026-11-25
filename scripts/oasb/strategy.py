"""A small, editable schema. Each term has an explicit matching mode."""
import re
from datetime import date

from .api import Error

FIELDS = {"title", "abstract", "title-abstract", "title-abstract-keywords", "fulltext"}
# OQL field names after `where`. "fulltext" searches title, abstract and full text.
FIELD_OQL = {"fulltext": "full text"}
ROLES = {"search", "optional", "screen", "exclude"}
CORPORA = {"core": "", "all": " (all corpora)", "expansion": " (expansion corpus)"}
TOKEN = re.compile(r"[\w][\w*?'-]*", re.UNICODE)
WORD = re.compile(r"[\w][\w'-]*", re.UNICODE)


def word_term(term):
    if not isinstance(term, str) or not term.strip():
        raise Error("Terms must be nonempty strings.")
    if TOKEN.fullmatch(term) and term.upper() not in {"AND", "OR", "NOT"}:
        return term
    if re.fullmatch(r'(?:stemmed )?"[^"\n]+"', term):
        return term
    raise Error(f"Quote multiword phrases (or use stemmed quotes): {term!r}")


def near_term(term):
    """Native OQL proximity: {"near": ["rural", "clinic"], "within": 3, "exact": false}."""
    if set(term) - {"near", "within", "exact"}:
        raise Error("A proximity term takes near, within and optional exact.")
    words, within, exact = term.get("near"), term.get("within"), term.get("exact", False)
    if not isinstance(words, list) or len(words) < 2 or not all(isinstance(w, str) and w.strip() for w in words):
        raise Error("A proximity term needs at least two words or phrases in near.")
    if type(within) is not int or not 1 <= within <= 50:
        raise Error("Proximity distance (within) must be an integer from 1 to 50.")
    if type(exact) is not bool:
        raise Error("Proximity exact must be true or false.")
    for w in words:
        if '"' in w or "\n" in w:
            raise Error("Write proximity words without quotes; set exact for unstemmed matching.")
        if not exact and not WORD.fullmatch(w):
            raise Error(f"Stemmed proximity takes single words. Set exact: true for phrases or wildcards: {w!r}")
        if exact and not re.fullmatch(r"[\w][\w*?' -]*", w):
            raise Error(f"Unsupported characters in proximity term: {w!r}")
        if re.search(r"(?:^|\s)[^\s*]{0,2}\*", w):
            raise Error(f"A wildcard needs at least three leading characters: {w!r}")
    items = [f'"{w}"' if exact else w for w in words]
    listed = " and ".join(items) if len(items) == 2 else ", ".join(items[:-1]) + ", and " + items[-1]
    return f"({listed} within {within} words of each other)"


def term_text(term):
    return near_term(term) if isinstance(term, dict) else word_term(term)


def limit_clauses(limits):
    if not isinstance(limits, dict) or set(limits) - {"from_date", "to_date", "language", "type"}:
        raise Error("Supported limits: from_date, to_date, language, type.")
    clauses = {}
    for key, op in [("from_date", ">="), ("to_date", "<=")]:
        if key in limits:
            value = date.fromisoformat(limits[key]).isoformat()
            clauses[key] = f"publication_date {op} {value}"
    if limits.get("from_date", "") > limits.get("to_date", "9999-12-31"):
        raise Error("The date range is reversed.")
    for key in ["language", "type"]:
        if key not in limits:
            continue
        values = limits[key]
        if not isinstance(values, list) or not values or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z-]*", v) for v in values
        ):
            raise Error(f"{key} must be a nonempty list of OpenAlex codes.")
        clauses[key] = f"{key} is (" + " or ".join(values) + ")"
    return clauses


def limit_leaves(limits):
    return sum(len(v) if isinstance(v, list) else 1 for v in (limits or {}).values())


# --- combine: a small AND/OR expression over search block IDs -------------------------

def parse_combine(text, names):
    """Parse `a and (b or c)` into nested ("and"|"or", [...]) tuples. NOT is not allowed:
    use blocks with the exclude role, which are always ANDed as NOT."""
    if not isinstance(text, str) or not text.strip():
        raise Error("combine must be a nonempty expression of search block IDs.")
    tokens = re.findall(r"\(|\)|[A-Za-z][\w-]*|\S", text)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def expr():
        node = conj()
        parts = [node]
        while peek() and peek().lower() == "or":
            take()
            parts.append(conj())
        return parts[0] if len(parts) == 1 else ("or", parts)

    def conj():
        parts = [atom()]
        while peek() and peek().lower() == "and":
            take()
            parts.append(atom())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def atom():
        tok = take() if peek() else None
        if tok == "(":
            node = expr()
            closing = take() if peek() else None
            if closing != ")":
                raise Error("Unbalanced parentheses in combine.")
            return node
        if tok is None or tok.lower() in {"and", "or", "not"} or tok == ")":
            raise Error(f"Unexpected {tok!r} in combine. Use search block IDs with and/or; exclude blocks provide NOT.")
        if tok not in names:
            raise Error(f"combine references {tok!r}, which is not a search block.")
        return tok

    tree = expr()
    if peek() is not None:
        raise Error(f"Unexpected {peek()!r} in combine.")
    used = set(re.findall(r"[A-Za-z][\w-]*", text)) - {"and", "or", "AND", "OR", "And", "Or"}
    if used != set(names):
        raise Error("combine must reference every search block exactly as the strategy defines them: "
                    + ", ".join(sorted(set(names) - used)) + " missing.")
    return tree


def truth(tree, values):
    if isinstance(tree, str):
        return values[tree]
    op, parts = tree
    results = [truth(p, values) for p in parts]
    return all(results) if op == "and" else any(results)


def failing(tree, values):
    """Block IDs that make a monotone expression false (a sufficient explanation)."""
    if truth(tree, values):
        return []
    if isinstance(tree, str):
        return [tree]
    op, parts = tree
    found = []
    for p in parts:
        if op == "or" or not truth(p, values):
            found += failing(p, values)
    return sorted(dict.fromkeys(found))


# --- validation and compilation -------------------------------------------------------

def check(strategy):
    if not isinstance(strategy, dict) or set(strategy) - {"blocks", "limits", "corpus", "combine"}:
        raise Error("Expected a strategy with blocks and optional limits, corpus and combine.")
    if strategy.get("corpus", "core") not in CORPORA:
        raise Error("corpus must be core, all or expansion.")
    blocks = strategy.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise Error("Define at least one search block in strategy.json.")
    ids = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) - {"id", "role", "field", "terms", "rationale"}:
            raise Error("Unknown block property.")
        name = block.get("id", "")
        if not re.fullmatch(r"[A-Za-z][\w-]*", name) or name in ids or name.lower() in {"and", "or", "not"}:
            raise Error(f"Invalid or duplicate block ID: {name}")
        ids.add(name)
        if block.get("role") not in ROLES or block.get("field", "title-abstract") not in FIELDS:
            raise Error(f"Invalid field or role in {name}.")
        terms = block.get("terms", [])
        if not isinstance(terms, list) or (block["role"] != "screen" and not terms):
            raise Error(f"Define the vocabulary for {name}.")
        for term in terms:
            term_text(term)
    if not any(b["role"] == "search" for b in blocks):
        raise Error("At least one concept must have the search role.")
    if "combine" in strategy:
        parse_combine(strategy["combine"], [b["id"] for b in core(strategy)])
    limit_clauses(strategy.get("limits", {}))


def clause(block, negate=False):
    terms = " OR ".join(term_text(t) for t in block["terms"])
    field = block.get("field", "title-abstract")
    return ("not " if negate else "") + f"{FIELD_OQL.get(field, field)} has ({terms})"


def render(tree, by_id):
    if isinstance(tree, str):
        return clause(by_id[tree])
    op, parts = tree
    return "(" + f" {op} ".join(render(p, by_id) for p in parts) + ")"


def oql(blocks, limits=None, corpus="core", excludes=(), combine=None):
    if combine is not None:
        tree = parse_combine(combine, [b["id"] for b in blocks]) if isinstance(combine, str) else combine
        clauses = [render(tree, {b["id"]: b for b in blocks})]
    else:
        clauses = [clause(b) for b in blocks]
    clauses += [clause(b, negate=True) for b in excludes]
    clauses.extend(limit_clauses(limits or {}).values())
    return "get works" + CORPORA[corpus] + (" where " + " and ".join(clauses) if clauses else "")


def expected_leaves(blocks, limits=None, excludes=()):
    return sum(len(b["terms"]) for b in [*blocks, *excludes]) + limit_leaves(limits)


def core(strategy):
    return [b for b in strategy["blocks"] if b["role"] == "search"]


def excluded(strategy):
    return [b for b in strategy["blocks"] if b["role"] == "exclude"]


def final_query(strategy):
    """The delivered query and its leaf count."""
    blocks, ex, limits = core(strategy), excluded(strategy), strategy.get("limits", {})
    return (oql(blocks, limits, strategy.get("corpus", "core"), ex, strategy.get("combine")),
            expected_leaves(blocks, limits, ex))


def lint(strategy):
    """Offline syntax check, local OQL compilation, and heuristic warnings. No API calls."""
    check(strategy)
    query, leaves = final_query(strategy)
    corpus = strategy.get("corpus", "core")
    blocks = core(strategy)
    excludes = excluded(strategy)
    warnings = []
    block_oql = {}
    for b in strategy["blocks"]:
        bid = b["id"]
        if not isinstance(b.get("rationale"), str) or not b["rationale"].strip():
            warnings.append({"code": "missing_rationale", "block": bid,
                             "message": f"Block {bid!r} has no rationale."})
        if b["role"] == "screen":
            continue
        block_oql[bid] = oql([b], corpus=corpus)
        seen_terms = set()
        for t in b["terms"]:
            rendered = term_text(t)
            norm = rendered.casefold()
            if norm in seen_terms:
                warnings.append({"code": "duplicate_term", "block": bid, "term": rendered,
                                 "message": f"Duplicate term {rendered!r} in block {bid!r}."})
            seen_terms.add(norm)
            if isinstance(t, str):
                if "-" in t and not t.startswith(('"', 'stemmed "')):
                    warnings.append({"code": "unquoted_hyphen", "block": bid, "term": t,
                                     "message": f"Unquoted hyphenated term {t!r} in {bid!r} can split/stem broadly in OpenAlex; consider quoting it."})
                if "*" in t and re.search(r'(?:^|[\s"])[^\s"*]{0,2}\*', t):
                    warnings.append({"code": "short_wildcard", "block": bid, "term": t,
                                     "message": f"Wildcard {t!r} in {bid!r} has fewer than 3 leading characters; OpenAlex will reject it."})
                if t.startswith("stemmed ") and ("*" in t or "?" in t):
                    warnings.append({"code": "stemmed_wildcard", "block": bid, "term": t,
                                     "message": f"Stemmed phrase with wildcard {t!r} in {bid!r} is rejected by OpenAlex; drop stemmed."})
    if "combine" not in strategy and len(blocks) >= 4:
        warnings.append({"code": "many_and_blocks", "block": None,
                         "message": f"{len(blocks)} search blocks are ANDed together; consider moving outcomes/settings/designs to role='screen' to protect recall."})
    if excludes:
        warnings.append({"code": "exclude_blocks", "block": None,
                         "message": "Exclusion blocks (NOT) can drop relevant records; check exclude/<id> against known works when online."})
    return {
        "ok": True,
        "validated_against_openalex": False,
        "oql": query,
        "corpus": corpus,
        "leaves": leaves,
        "blocks": block_oql,
        "warnings": warnings,
        "note": "Local syntax and heuristic checks only — not validated against OpenAlex.",
    }

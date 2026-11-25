"""A small, editable schema. Each term has an explicit matching mode."""
import re
from datetime import date

from .api import Error, tokens

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


def topic_ids(values):
    if not isinstance(values, list) or not values:
        raise Error("topic/topics must be a nonempty list of OpenAlex Topic IDs like T10102.")
    out = []
    for t in values:
        tid = re.sub(r"^https?://openalex\.org/", "", str(t).strip()).upper()
        if not re.fullmatch(r"T[1-9][0-9]*", tid):
            raise Error(f"Expected an OpenAlex Topic ID like T10102: {t!r}")
        if tid not in out:
            out.append(tid)
    return out


def topic_clause(values):
    ids = topic_ids(values)
    return f"topic is {ids[0]}" if len(ids) == 1 else "topic is (" + " or ".join(ids) + ")"


def abstract_clause(value):
    if type(value) is not bool:
        raise Error("has_abstract must be true or false.")
    return "it has an abstract" if value else "it doesn't have an abstract"


def limit_clauses(limits):
    if not isinstance(limits, dict) or set(limits) - {"from_date", "to_date", "language", "type", "has_abstract", "topic"}:
        raise Error("Supported limits: from_date, to_date, language, type, has_abstract, topic.")
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
    if "has_abstract" in limits:
        clauses["has_abstract"] = abstract_clause(limits["has_abstract"])
    if "topic" in limits:
        clauses["topic"] = topic_clause(limits["topic"])
    return clauses


def limit_leaves(limits):
    total = 0
    for key, v in (limits or {}).items():
        if key == "topic":
            total += len(topic_ids(v))
        elif isinstance(v, list):
            total += len(v)
        else:
            total += 1
    return total


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
        if not isinstance(block, dict) or set(block) - {"id", "role", "field", "terms", "rationale", "has_abstract", "topics"}:
            raise Error("Unknown block property.")
        name = block.get("id", "")
        if not re.fullmatch(r"[A-Za-z][\w-]*", name) or name in ids or name.lower() in {"and", "or", "not"}:
            raise Error(f"Invalid or duplicate block ID: {name}")
        ids.add(name)
        if block.get("role") not in ROLES or block.get("field", "title-abstract") not in FIELDS:
            raise Error(f"Invalid field or role in {name}.")
        terms = block.get("terms", [])
        if not isinstance(terms, list):
            raise Error(f"terms must be a list in {name}.")
        if "has_abstract" in block:
            abstract_clause(block["has_abstract"])
        if "topics" in block:
            topic_ids(block["topics"])
        if block["role"] == "exclude" and ("has_abstract" in block or "topics" in block):
            raise Error(f"Exclude block {name} only supports text terms.")
        if block["role"] != "screen" and not terms and "topics" not in block and "has_abstract" not in block:
            raise Error(f"Define the vocabulary (or topics/has_abstract) for {name}.")
        for term in terms:
            term_text(term)
    if not any(b["role"] == "search" for b in blocks):
        raise Error("At least one concept must have the search role.")
    if "combine" in strategy:
        parse_combine(strategy["combine"], [b["id"] for b in core(strategy)])
    limit_clauses(strategy.get("limits", {}))


def clause(block, negate=False):
    parts = []
    if block.get("terms"):
        terms = " OR ".join(term_text(t) for t in block["terms"])
        field = block.get("field", "title-abstract")
        parts.append(f"{FIELD_OQL.get(field, field)} has ({terms})")
    if "topics" in block:
        parts.append(topic_clause(block["topics"]))
    if "has_abstract" in block:
        parts.append(abstract_clause(block["has_abstract"]))
    if negate:
        return "not " + parts[0]
    return parts[0] if len(parts) == 1 else "(" + " and ".join(parts) + ")"


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


def block_leaves(block):
    return (len(block.get("terms", []))
            + (len(topic_ids(block["topics"])) if "topics" in block else 0)
            + (1 if "has_abstract" in block else 0))


def expected_leaves(blocks, limits=None, excludes=()):
    return sum(block_leaves(b) for b in [*blocks, *excludes]) + limit_leaves(limits)


def term_tokens(term):
    if isinstance(term, dict):
        return [t for w in term["near"] for t in tokens(w)]
    return tokens(term[len("stemmed "):] if term.startswith("stemmed ") else term)


def block_tokens(block):
    out = [t for term in block.get("terms", []) for t in term_tokens(term)]
    if "topics" in block:
        out += [t for tid in topic_ids(block["topics"]) for t in tokens(tid)]
    if "has_abstract" in block:
        out += tokens(block["has_abstract"])
    return out


def limit_tokens(limits):
    out = []
    for key, v in (limits or {}).items():
        values = topic_ids(v) if key == "topic" else v if isinstance(v, list) else [v]
        out += [t for item in values for t in tokens(item)]
    return out


def expected_tokens(blocks, limits=None, excludes=()):
    """Words each strategy term, topic and limit must leave in OpenAlex's query object."""
    return [t for b in [*blocks, *excludes] for t in block_tokens(b)] + limit_tokens(limits)


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
        for t in b.get("terms", []):
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


def rescue_oql(strategy, direction="both", topics=None):
    """Build a conditional no-abstract citation-set rescue query from a strategy.

    Targets works that lack an abstract in OpenAlex, are linked by citation to the
    strategy's core Boolean query, and pass either an any-block text gate (default)
    or an explicit OpenAlex topic gate. Preserves corpus and eligibility limits.
    """
    check(strategy)
    if direction not in {"cited-by", "citing", "both"}:
        raise Error("Rescue direction must be cited-by, citing, or both.")
    blocks, ex, limits = core(strategy), excluded(strategy), strategy.get("limits", {})
    corpus = strategy.get("corpus", "core")
    # The nested set must be written as a full `get works <corpus> where` query: OpenAlex
    # silently drops a corpus written as `(works (all corpora) where ...)`.
    set_expr = "(" + oql(blocks, limits, corpus, ex, strategy.get("combine")) + ")"
    if direction == "cited-by":
        rel = f"it's cited by a work in the set {set_expr}"
    elif direction == "citing":
        rel = f"it cites a work in the set {set_expr}"
    else:
        rel = f"(it's cited by a work in the set {set_expr} or it cites a work in the set {set_expr})"
    gate = topic_clause(topics) if topics else "(" + " or ".join(clause(b) for b in blocks) + ")"
    clauses = ["it doesn't have an abstract", gate, rel, *limit_clauses(limits).values()]
    return "get works" + CORPORA[corpus] + " where " + " and ".join(clauses)


def rescue_expectation(strategy, direction="both", topics=None):
    """Leaf count and words the translated rescue query must keep (see check_translation)."""
    blocks, ex, limits = core(strategy), excluded(strategy), strategy.get("limits", {})
    sets = 2 if direction == "both" else 1
    gate_leaves = len(topic_ids(topics)) if topics else sum(block_leaves(b) for b in blocks)
    gate_tokens = [t for tid in topic_ids(topics) for t in tokens(tid)] if topics else \
        [t for b in blocks for t in block_tokens(b)]
    count = 1 + gate_leaves + sets * expected_leaves(blocks, limits, ex) + limit_leaves(limits)
    words = tokens(False) + gate_tokens + sets * expected_tokens(blocks, limits, ex) + limit_tokens(limits)
    return count, words

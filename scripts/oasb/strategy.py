"""A small, editable schema. Each term has an explicit matching mode."""
import re
from datetime import date

from .api import Error

FIELDS = {"title", "abstract", "title-abstract", "title-abstract-keywords"}
ROLES = {"search", "optional", "screen"}
TOKEN = re.compile(r"[\w][\w*?'-]*", re.UNICODE)


def term_text(term):
    if not isinstance(term, str) or not term.strip():
        raise Error("Terms must be nonempty strings.")
    if TOKEN.fullmatch(term) and term.upper() not in {"AND", "OR", "NOT"}:
        return term
    if re.fullmatch(r'(?:stemmed )?"[^"\n]+"', term):
        return term
    raise Error(f"Quote multiword phrases (or use stemmed quotes): {term!r}")


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


def check(strategy):
    if not isinstance(strategy, dict) or set(strategy) - {"blocks", "limits"}:
        raise Error("Expected a strategy with blocks and optional limits.")
    blocks = strategy.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise Error("Define at least one search block in strategy.json.")
    ids = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) - {"id", "role", "field", "terms", "rationale"}:
            raise Error("Unknown block property.")
        name = block.get("id", "")
        if not re.fullmatch(r"[A-Za-z][\w-]*", name) or name in ids:
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
    limit_clauses(strategy.get("limits", {}))


def oql(blocks, limits=None):
    clauses = []
    for b in blocks:
        terms = " OR ".join(term_text(t) for t in b["terms"])
        clauses.append(f"{b.get('field', 'title-abstract')} has ({terms})")
    clauses.extend(limit_clauses(limits or {}).values())
    return "get works" + (" where " + " and ".join(clauses) if clauses else "")


def core(strategy):
    return [b for b in strategy["blocks"] if b["role"] == "search"]

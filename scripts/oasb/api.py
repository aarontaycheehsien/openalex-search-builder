"""Read OpenAlex, validate OQL/OQO, and retain request evidence without credentials."""
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, unquote
from urllib.request import Request, urlopen

ROOT = "https://api.openalex.org"
FIELDS = "id,ids,display_name,publication_year,abstract_inverted_index,keywords"
WORK = re.compile(r"W[1-9][0-9]*", re.I)


class Error(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identifier(raw):
    raw = unquote(raw.strip()).rstrip("/")
    value = re.sub(r"^https?://(?:api\.)?openalex\.org/(?:works/)?", "", raw, flags=re.I)
    if WORK.fullmatch(value):
        return value.upper()
    value = re.sub(r"^(?:https?://pubmed\.ncbi\.nlm\.nih\.gov/|pmid:\s*)", "", raw, flags=re.I)
    if re.fullmatch(r"[1-9]\d*", value):
        return "pmid:" + value
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", raw, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/\S+", value, flags=re.I):
        return "doi:" + value.lower()
    raise Error(f"Expected an OpenAlex Work ID, DOI or PMID: {raw!r}")


def work_id(record):
    value = str(record.get("id", "")).rsplit("/", 1)[-1]
    if not WORK.fullmatch(value):
        raise Error("OpenAlex returned a work without a valid ID.")
    return value.upper()


def normalize_query(obj):
    """Ignore order only for the API's commutative Boolean lists."""
    if isinstance(obj, list):
        return [normalize_query(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    result = {k: normalize_query(v) for k, v in obj.items()}
    for key in ("filter_rows", "filters"):
        if isinstance(result.get(key), list):
            result[key] = sorted(result[key], key=lambda v: json.dumps(v, sort_keys=True))
    return result


def normalize_warnings(warnings):
    """Canonicalize warning order and OpenAlex's set-ordered 'Fields involved:' lists."""
    out = []
    for w in warnings or []:
        if isinstance(w, dict) and isinstance(w.get("message"), str) and "Fields involved: " in w["message"]:
            head, _, tail = w["message"].partition("Fields involved: ")
            fields = ", ".join(sorted(f.strip() for f in tail.split(",") if f.strip()))
            w = dict(w, message=f"{head}Fields involved: {fields}")
        out.append(w)
    return sorted(out, key=lambda v: json.dumps(v, sort_keys=True))


class API:
    def __init__(self, log=lambda event: None):
        self.log = log
        self.credits = 0
        self.cost_usd = 0.0
        self.unpriced = 0

    def request(self, path, params=None, body=None, allow_missing=False):
        url = ROOT + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "openalex-search-builder/0.1"}
        if key := os.environ.get("OPENALEX_API_KEY"):
            headers["Authorization"] = "Bearer " + key
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers)
        for attempt in range(3):
            event = {"at": now(), "method": req.get_method(), "path": path, "params": params, "body": body}
            try:
                with urlopen(req, timeout=35) as res:
                    payload = json.load(res)
                    if not isinstance(payload, dict):
                        raise Error("OpenAlex returned an unexpected JSON document.")
                    credits = res.headers.get("x-ratelimit-credits-used")
                    cost = res.headers.get("x-ratelimit-cost-usd")
                    self.credits += int(credits or 0)
                    self.cost_usd += float(cost or 0)
                    self.unpriced += int(cost is None)
                    self.log(dict(event, status=res.status, credits=credits, cost_usd=cost))
                    return payload
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:1200]
                self.log(dict(event, status=exc.code, detail=detail))
                if allow_missing and exc.code == 404:
                    return None
                delay = exc.headers.get("Retry-After")
                if exc.code in (429, 502, 503, 504) and attempt < 2:
                    wait = int(delay) if delay and delay.isdecimal() else 2 ** attempt
                    if wait <= 8 and not (exc.code == 429 and re.search(r"not_enough_credits|daily|budget|insufficient", detail, re.I)):
                        time.sleep(wait)
                        continue
                raise Error(f"OpenAlex HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                self.log(dict(event, error=str(exc)))
                if attempt == 2:
                    raise Error(f"OpenAlex request failed: {exc}") from exc
                time.sleep(2 ** attempt)
            except (UnicodeError, json.JSONDecodeError) as exc:
                self.log(dict(event, error="invalid JSON"))
                raise Error("OpenAlex returned unreadable JSON.") from exc

    def validate(self, query):
        key = "oql" if isinstance(query, str) else "oqo"
        value = self.request("/query", body={key: query})
        if value.get("validation", {}).get("valid") is not True or not value.get("oql") or not value.get("oqo"):
            raise Error(f"OpenAlex did not validate the strategy: {value.get('validation')}")
        return {k: value[k] for k in ("oql", "oqo", "validation")}

    def run(self, query, size=1, fields="id"):
        key = "oql" if isinstance(query, str) else "oqo"
        params = {key: query if key == "oql" else json.dumps(query, separators=(",", ":")),
                  "per-page": size, "select": fields}
        if len(urlencode(params)) <= 5500:
            reply = self.request("/", params=params)
        else:
            reply = self.request("/", body={key: query, "per_page": size, "select": fields})
        meta, rows = reply.get("meta") or {}, reply.get("results")
        echo = meta.get("x_query") or {}
        if type(meta.get("count")) is not int or not isinstance(rows, list) or not echo.get("oqo") or not echo.get("oql"):
            raise Error("Incomplete execution response from OpenAlex.")
        return {"count": meta["count"], "rows": rows, "oqo": echo["oqo"], "oql": echo["oql"]}

    def resolve(self, raw):
        alias = identifier(raw)
        record = self.request("/works/" + quote(alias, safe=":/"), {"select": FIELDS}, allow_missing=True)
        if record is not None:
            work_id(record)
        return alias, record

    def members(self, query, ids):
        hits = set()
        ordered = sorted(set(ids))
        for start in range(0, len(ordered), 25):
            batch = ordered[start:start + 25]
            choices = [{"column_id": "ids.openalex", "value": wid} for wid in batch]
            restriction = choices[0] if len(choices) == 1 else {"join": "or", "filters": choices}
            scoped = dict(query, filter_rows=[*query.get("filter_rows", []), restriction])
            result = self.run(scoped, len(batch))
            found = {work_id(row) for row in result["rows"]}
            if len(found) != result["count"] or not found.issubset(batch):
                raise Error("Incomplete or inconsistent OpenAlex ID intersection.")
            hits.update(found)
        return hits

    def page(self, oqo, cursor="*", size=100):
        """One cursor page of full work records for a validated query object."""
        reply = self.request("/", body={"oqo": oqo, "per_page": size, "cursor": cursor})
        meta, rows = reply.get("meta") or {}, reply.get("results")
        echo = meta.get("x_query") or {}
        if type(meta.get("count")) is not int or not isinstance(rows, list) or "next_cursor" not in meta:
            raise Error("Cursor response lacks a count, results or next_cursor; completion cannot be established.")
        if echo.get("oqo") and normalize_query(echo["oqo"]) != normalize_query(oqo):
            raise Error("OpenAlex executed a different query object during export.")
        return {"count": meta["count"], "rows": rows, "next_cursor": meta["next_cursor"]}

    def vocabulary(self, entity, query, size=10):
        """Look up OpenAlex keywords or topics for vocabulary brainstorming."""
        select = {
            "keywords": "id,display_name,description,display_name_alternatives,works_count,primary_topic",
            "topics": "id,display_name,description,keywords,domain,field,subfield,works_count",
        }
        if entity not in select:
            raise Error("Vocabulary entity must be keywords or topics.")
        q = (query or "").strip()
        if not q:
            raise Error("Provide a nonempty search query for vocab.")
        if type(size) is not int or not 1 <= size <= 50:
            raise Error("--size must be between 1 and 50.")
        reply = self.request(f"/{entity}", params={"search": q, "per-page": size, "select": select[entity]})
        meta, raw = reply.get("meta") or {}, reply.get("results")
        if not isinstance(raw, list):
            raise Error("Incomplete vocabulary response from OpenAlex.")
        rows = []
        for item in raw:
            short_id = re.sub(r"^https?://openalex\.org/", "", str(item.get("id") or ""))
            if entity == "keywords":
                rows.append({
                    "id": short_id,
                    "display_name": item.get("display_name"),
                    "description": item.get("description"),
                    "alternatives": item.get("display_name_alternatives") or [],
                    "works_count": item.get("works_count"),
                    "primary_topic": (item.get("primary_topic") or {}).get("display_name"),
                })
            else:
                rows.append({
                    "id": short_id,
                    "display_name": item.get("display_name"),
                    "description": item.get("description"),
                    "keywords": [k["display_name"] if isinstance(k, dict) and k.get("display_name") else k
                                 for k in item.get("keywords") or []],
                    "domain": (item.get("domain") or {}).get("display_name"),
                    "field": (item.get("field") or {}).get("display_name"),
                    "subfield": (item.get("subfield") or {}).get("display_name"),
                    "works_count": item.get("works_count"),
                })
        return {
            "entity": entity, "query": q, "count": meta.get("count"), "results": rows,
            "usage": {"credits_reported": self.credits, "usd_reported": round(self.cost_usd, 6)},
            "note": "OpenAlex keywords and topics are algorithmically assigned labels, not a controlled "
                    "thesaurus like MeSH. Test candidate terms empirically with eval.",
        }


def leaves(obj):
    """Count condition leaves in an OQO filter tree (one per term, value or date bound)."""
    if isinstance(obj, list):
        return sum(leaves(v) for v in obj)
    if not isinstance(obj, dict):
        return 0
    if "column_id" in obj:
        return 1
    return sum(leaves(v) for k, v in obj.items() if k in ("filter_rows", "filters"))


def check_translation(oqo, corpus, expected):
    """OpenAlex can drop an unrecognised clause without a validation error. Refuse that."""
    if (oqo.get("corpus") or "core") != corpus:
        raise Error(f"OpenAlex interpreted the corpus as {oqo.get('corpus') or 'core'}, not {corpus}.")
    found = leaves(oqo.get("filter_rows", []))
    if found != expected:
        raise Error(f"OpenAlex kept {found} of {expected} search terms and limit values. "
                    "Inspect the canonical OQL; a clause was dropped or split.")

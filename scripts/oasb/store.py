"""Versioned review evidence and serialized, atomic workspace writes."""
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .api import Error, now


def digest(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def read(path):
    return Path(path).read_text(encoding="utf-8")


MISSING = {
    "protocol": "Initialize this workspace first.",
    "strategy": "strategy.json is missing. Initialize the workspace or restore the file.",
    "records": "records.json is missing. Initialize the workspace or restore the file.",
    "scope": "Confirm the scope with the scope command first.",
    "latest-evaluations": "Run eval first.",
    "latest-reviews": "Attach a critique with the review command first.",
    "latest-discovery": "Run sample or neighbors first.",
}


def scope_changes(old, new):
    """Plain-language list of what differs between two scope keys (old may be absent)."""
    if not old:
        return ""
    out = []
    if old.get("protocol") != new.get("protocol"):
        out.append("question, eligibility or depth")
    before = {c["id"]: c["role"] for c in old.get("concepts", [])}
    after = {c["id"]: c["role"] for c in new.get("concepts", [])}
    if added := sorted(set(after) - set(before)):
        out.append("blocks added: " + ", ".join(added))
    if removed := sorted(set(before) - set(after)):
        out.append("blocks removed: " + ", ".join(removed))
    if roles := sorted(k for k in set(before) & set(after) if before[k] != after[k]):
        out.append("roles changed: " + ", ".join(f"{k} {before[k]}→{after[k]}" for k in roles))
    for key, label in (("limits", "limits"), ("corpus", "corpus"), ("pulled", "pulled-works setting")):
        if old.get(key) != new.get(key):
            out.append(label)
    return "; ".join(out)


class Run:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, name):
        path = self.root / (name + ".json")
        if not path.is_file() and name in MISSING:
            raise Error(MISSING[name])
        try:
            return json.loads(read(path))
        except json.JSONDecodeError as exc:
            raise Error(f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}). Fix or restore it.") from exc

    def put(self, name, value):
        write(self.root / (name + ".json"), value)

    def log(self, name, event):
        with (self.root / (name + ".jsonl")).open("a", encoding="utf-8") as out:
            out.write(json.dumps(event, ensure_ascii=False) + "\n")

    @contextmanager
    def locked(self):
        self.root.mkdir(exist_ok=True, parents=True)
        with (self.root / ".lock").open("a+b") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def init(self, question, eligibility, depth):
        if (self.root / "protocol.json").exists():
            raise Error("This workspace is already initialized.")
        self.put("records", {})
        self.put("strategy", {"blocks": [], "limits": {}})
        self.put("protocol", {"question": question, "eligibility": eligibility, "depth": depth})
        return {"workspace": str(self.root), "next": "Edit strategy.json and confirm the scope."}

    def scope_data(self):
        s = self.get("strategy")
        concepts = [{k: b.get(k) for k in ("id", "role", "rationale")} for b in s["blocks"]]
        data = {"protocol": self.get("protocol"), "concepts": concepts, "limits": s.get("limits", {}),
                "corpus": s.get("corpus", "core")}
        if "pulled" in s:
            data["pulled"] = s["pulled"]
        return data

    def scope_key(self):
        """What a scope decision covers. Rationale wording is documentation, not a decision:
        editing it does not need re-confirmation (it still changes the evaluation fingerprint)."""
        data = self.scope_data()
        return dict(data, concepts=[{k: c.get(k) for k in ("id", "role")} for c in data["concepts"]])

    def scope_confirmed(self):
        if not (self.root / "scope.json").exists():
            return False
        stored = self.get("scope")["hash"]
        # Scopes confirmed before 2026-11-26 hashed the rationale too; they stay valid until something changes.
        return stored in (digest(self.scope_key()), digest(self.scope_data()))

    def approved(self):
        if self.scope_confirmed():
            return
        if not (self.root / "scope.json").exists():
            raise Error('Confirm the scope first: scope --confirm --note "<the reviewer\'s decision>".')
        changed = scope_changes(self.get("scope").get("key"), self.scope_key())
        raise Error("The scope changed since it was confirmed" + (f" ({changed})" if changed else "")
                    + '. Check it with scope, then run: scope --confirm --note "<what changed and why>".')

    def fingerprint(self):
        return digest({k: self.get(k) for k in ("strategy", "protocol", "scope", "records")})

    def snapshot(self, name, data):
        uid = uuid.uuid4().hex
        self.put(f"{name}/{uid}", data)
        self.put("latest-" + name, {"id": uid, "at": now()})
        return uid

    def latest(self, name):
        uid = self.get("latest-" + name)["id"]
        return uid, self.get(f"{name}/{uid}")

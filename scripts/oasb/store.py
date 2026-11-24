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


class Run:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, name):
        return json.loads(read(self.root / (name + ".json")))

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
        return {"protocol": self.get("protocol"), "concepts": concepts, "limits": s.get("limits", {}),
                "corpus": s.get("corpus", "core")}

    def approved(self):
        path = self.root / "scope.json"
        if not path.exists() or self.get("scope")["hash"] != digest(self.scope_data()):
            raise Error("Confirm the current concepts, eligibility and limits with the scope command.")

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

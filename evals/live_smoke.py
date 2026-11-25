"""Exercise the public CLI against OpenAlex. This is a technical control, not a review."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = "W2618851878"


def smoke(folder):
    workspace = folder / "review"
    def run(*args):
        cmd = [sys.executable, str(ROOT / "scripts/oasb.py"), "-w", str(workspace), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip())
        return json.loads(proc.stdout)

    run("init", "--question", "Does the live backend retrieve the rural telemedicine control?")
    strategy = json.loads((ROOT / "examples/telehealth.json").read_text())
    strategy["blocks"][1]["terms"] = ["astrophysics"]
    path = workspace / "strategy.json"
    path.write_text(json.dumps(strategy))
    run("scope", "--confirm", "--note", "Technical control with an intentionally failing setting term.")
    run("resolve", SEED, "--role", "seed", "--source", "Technical retrieval control")
    first = run("eval")
    if first["misses"].get(SEED) != ["block/setting"]:
        raise RuntimeError("Expected the deliberately incorrect setting term to miss the control.")
    strategy["blocks"][1]["terms"] = ["rural", "remote"]
    path.write_text(json.dumps(strategy))
    second = run("eval")
    if SEED not in second["change"]["gained"]:
        raise RuntimeError("The revision did not recover the known work.")
    critique = {k: "Automated technical fixture; scientific review is outside this smoke test."
                for k in ["scope", "vocabulary", "boolean_and_fields", "limits", "retrieval", "limitations"]}
    critique["decision"] = "ready"
    file = folder / "fixture-critique.json"
    file.write_text(json.dumps(critique))
    run("review", str(file))
    delivery = run("report")
    dest = Path(delivery["report"])
    query = (dest / "final-query.oql").read_text().strip()
    if query != second["oql"] or json.loads((dest / "validation.json").read_text())["evaluation"]["sets"]["seed"]["hits"] != [SEED]:
        raise RuntimeError("Delivered artifacts disagree with validated retrieval.")
    # Bounded export: two single-page runs check live cursor paging and resumption without
    # paying for the complete result set.
    page1 = run("export", "--max-pages", "1")
    page2 = run("export", "--max-pages", "1")
    folder = Path(page2["directory"])
    lines = (folder / ("results.jsonl" if page2["complete"] else "results.partial.jsonl")).read_text().splitlines()
    if page1["pages"] != 1 or page1["received"] == 0 or (not page1["complete"] and page2["pages"] != 2):
        raise RuntimeError("Export did not resume from its saved cursor.")
    if len(lines) != page2["unique_records"] or len({json.loads(l)["id"] for l in lines}) != len(lines):
        raise RuntimeError("Export files disagree with the checkpointed unique records.")
    return {"validated_at": delivery["validated_at"], "query": query, "count": delivery["count"],
            "control": SEED, "failed_block": "setting", "recovered": True,
            "export": {k: page2[k] for k in ("pages", "received", "unique_records", "complete", "counts_seen_while_paging")}}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        print(json.dumps(smoke(Path(temp)), indent=2))

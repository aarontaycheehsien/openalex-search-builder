"""Score fixed comparator strategies under both seed assignments."""
import json
import sys
import tempfile
from pathlib import Path

from bench import API, CASES, ROOT, prepare, score


def run(output):
    output.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name in CASES:
            case = json.loads((ROOT / "evals/fixtures" / (name + ".json")).read_text())
            for condition in ["seedless", "seeded"]:
                folder = Path(tmp) / (name + "-" + condition)
                prepare(case, condition, folder)
                for reference in [False, True]:
                    api = API()
                    row = score(case, folder, api, reference=reference)
                    (output / f"{name}-{condition}-{row['mode']}.json").write_text(json.dumps(row, indent=2) + "\n")
                    print(f"{name} {condition} {row['mode']}: {row['hits']}/{row['tested']}, {row['screening_count']} works", flush=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]))

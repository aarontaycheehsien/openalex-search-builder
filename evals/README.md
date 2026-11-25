# Evaluate retrieval

The benchmark adapts the seeded/seedless, unseen-recall, and held-out `frozen` suite design of [PubMed Search Builder](https://github.com/aarontaycheehsien/pubmed-search-builder-next/tree/main/evals/fixtures) for OpenAlex. It includes **32 fixtures** spanning medicine, pharmacology, diagnostic accuracy, psychiatry, nursing education, reading/education, social policy, criminal justice, behavioural science, and bibliometrics:

| Suite | Count | Role | Highlights |
|---|---:|---|---|
| Core (`evals/fixtures/*.json`) | 2 | Comparator baselines & references | `Bos_2018` (dementia), `Anmarkrud_2021` (reading/sourcing) |
| `synergy` | 13 | Development / calibration (SYNERGY) | `Appenzeller-Herzog_2019`, `Brouwer_2019`, `Donners_2021`, `Jeyaraman_2020`, `Kwok_2020`, `Meijboom_2021`, `Menon_2022`, `Muthu_2022`, `Oud_2018`, `Smid_2020`, `Welling_2021`, `van_Dis_2020`, `van_de_Schoot_2018` |
| `frozen` | 10 | **Held-out evaluation only** | 5 Cohen drug reviews + 5 non-clinical/policy reviews (`housing-first-criminal-justice`, `social-prescribing-older-adults`, `work-directed-return-to-work`, `school-restorative-practice`, `healthcare-nudging`). See [frozen/README.md](fixtures/frozen/README.md). |
| `clef-tar-2018` | 3 | Diagnostic test accuracy (Cochrane) | `CD010657`, `CD011431`, `CD011926` |
| `education-health` | 1 | Health-professions education | `Liu_2023_VR_nursing` |
| `2022-custom` / `2026-custom` | 2 | Recent custom reviews | `Medeiros_2022_school_nutrition`, `Gao_2026_immune_checkpoint` |
| `openalex-native` | 1 | Multidisciplinary non-biomedical | `Langham-Putrow_2021_OACA` (134 included studies; 114 OpenAlex-resolved) |

List all fixtures (or filter by suite):

```sh
python evals/bench.py list
python evals/bench.py list --suite frozen
```

Each fixture defines an explicit `to_date` comparison cutoff. Retrospective runs use today's OpenAlex index and current metadata. Resolve gold works afresh, report unidentifiable rows, collapse duplicate work IDs, and report out-of-window works separately.

## Blind build

From the repository root:

```sh
python evals/bench.py prepare Bos_2018 /tmp/bos-blind --condition seedless
```

Start a separate agent session rooted only in the prepared directory. Give it `intake.json` and `skill/SKILL.md`. The prepared directory includes the skill, a fresh workspace and the assigned seeds. Run commands from that skill copy against the prepared workspace. Retain the date bound in all discovery and search steps. Keep the fixture, benchmark code, source review, published included-study list and reference strategies outside the agent's accessible context. Inspect the transcript for leakage before interpreting a run. A seeded run supplies three deterministic identifiers. A seedless run supplies the same question with no identifiers.

Back in the evaluator checkout, score the delivered snapshot:

```sh
python evals/bench.py score Bos_2018 /tmp/bos-blind --report /tmp/bos-blind/run/reports/DELIVERY_ID
python evals/bench.py baseline Bos_2018 /tmp/bos-blind
```

Repeat in a fresh directory for the other condition and topic. Record the agent/model, date, condition, number of runs and any leakage alongside scorecards. Scores include coverage counts, retrieval among resolved, in-window gold works, unseen retrieval after excluding development works, screening workload, works per test hit and reported API costs. Supplied seeds are excluded from both strategies' test sets. A scorecard is not a precision estimate.

## Live controls

`python evals/live_smoke.py` checks that an intentionally incorrect concept misses a real work, changing that concept recovers it, and the reported query is the one the API ran. The test uses a clearly labelled synthetic critique.

`python evals/reference_runs.py /tmp/openalex-scores` compares fixed expanded and simple keyword strategies across both topics and seed assignments. These comparators are hand-authored. Scores from a completed blind agent build require the separate `score` step above. The manual GitHub workflow can run the smoke test alone or run it with the comparator matrix. It uploads JSON results and requires a repository Actions secret named `OPENALEX_API_KEY`.

The latest checked-in comparator summary is in [RESULTS.md](RESULTS.md).

# Evaluate retrieval

The benchmark adapts the seeded/seedless, unseen-recall, and held-out `frozen` suite design of [PubMed Search Builder](https://github.com/aarontaycheehsien/pubmed-search-builder-next/tree/main/evals/fixtures) for OpenAlex. It includes **41 fixtures** divided into two layers:

- **OpenAlex-native (`core` + `openalex-native`, 12 fixtures):** Multidisciplinary and non-biomedical reviews using DOIs and OpenAlex Work IDs directly (not filtered to PubMed), testing OpenAlex across software engineering, ecology, climate, education, accessibility, bibliometrics, and missing-abstract regimes.
- **PubMed cross-comparison (`synergy`, `frozen`, `clef-tar-2018`, `education-health`, `2022-custom`, `2026-custom`, 29 fixtures):** Ported directly from `pubmed-search-builder-next` (`gold_pmids`) for apples-to-apples comparison against PubMed Search Builder. Note that these 29 fixtures represent the PubMed-indexed subset of each review.

| Suite | Count | Role | Highlights |
|---|---:|---|---|
| Core (`evals/fixtures/*.json`) | 2 | Comparator baselines & references | `Bos_2018` (dementia), `Anmarkrud_2021` (reading/sourcing) |
| `openalex-native` | 10 | **Multidisciplinary non-PubMed OpenAlex evals** | `Langham-Putrow_2021_OACA` (bibliometrics), `Lewowski_2021` (software engineering), `Moran_2020` (ecology), `Kapuka_2021` (climate), `Filges_2022` & `Noetel_2021` & `Verdugo-Castro_2022` (education/STEM), `Cinquin_2018` (e-learning accessibility), `Walker_2018`, `Ruggeri_2019` |
| `synergy` | 13 | Development / calibration (PubMed subset) | `Appenzeller-Herzog_2019`, `Brouwer_2019`, `Donners_2021`, `Jeyaraman_2020`, `Kwok_2020`, `Meijboom_2021`, `Menon_2022`, `Muthu_2022`, `Oud_2018`, `Smid_2020`, `Welling_2021`, `van_Dis_2020`, `van_de_Schoot_2018` |
| `frozen` | 10 | **Held-out evaluation only** (PubMed subset) | 5 Cohen drug reviews + 5 non-clinical/policy reviews (`housing-first-criminal-justice`, `social-prescribing-older-adults`, `work-directed-return-to-work`, `school-restorative-practice`, `healthcare-nudging`). See [frozen/README.md](fixtures/frozen/README.md). |
| `clef-tar-2018` | 3 | Diagnostic test accuracy (Cochrane) | `CD010657`, `CD011431`, `CD011926` |
| `education-health` | 1 | Health-professions education (PubMed subset) | `Liu_2023_VR_nursing` |
| `2022-custom` / `2026-custom` | 2 | Recent custom reviews (PubMed subset) | `Medeiros_2022_school_nutrition`, `Gao_2026_immune_checkpoint` |

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

For a delivered report, `score` also gives the agent's capture–recapture estimate beside the number of gold works in the date window (`capture_recapture.gold_in_window`); the estimate counts works by the agent's eligibility judgements, so compare with care. Add `--explain` to `score` to see, as the evaluator only, why each missed gold work was missed: the blocks it fails, any exclusion or limit that removed it, and whether OpenAlex holds an abstract, with a summary by cause. Never run it inside the build workspace or show it to the building agent. If the agent also ran the optional no-abstract rescue, add `--with-rescue` to `score`. It scores the delivered search together with the report's saved rescue queries: union recall (overall and unseen) with 95% Wilson intervals, the hits the rescue adds, and an upper bound on combined screening volume, since rescue records can overlap the delivered search.

Repeat in a fresh directory for the other condition and topic. Record the agent/model, date, condition, number of runs and any leakage alongside scorecards. Scores include coverage counts, retrieval among resolved, in-window gold works, unseen retrieval after excluding development works, screening workload, `original_screening_load` and `workload_ratio` (`screening_count / original_screening_load`, when the original review's screening pool is publicly verifiable; kept out of `intake.json`), works per test hit and reported API costs. Supplied seeds are excluded from both strategies' test sets. A scorecard is not a precision estimate.

## Live controls

`python evals/live_smoke.py` checks that an intentionally incorrect concept misses a real work, changing that concept recovers it, and the reported query is the one the API ran. The test uses a clearly labelled synthetic critique.

`python evals/reference_runs.py /tmp/openalex-scores` compares fixed expanded and simple keyword strategies across both topics and seed assignments. These comparators are hand-authored. Scores from a completed blind agent build require the separate `score` step above. The manual GitHub workflow can run the smoke test alone or run it with the comparator matrix. It uploads JSON results and requires a repository Actions secret named `OPENALEX_API_KEY`.

The latest checked-in comparator summary is in [RESULTS.md](RESULTS.md).

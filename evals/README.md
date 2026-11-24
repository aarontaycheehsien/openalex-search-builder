# Evaluate retrieval

The benchmark adapts the seeded/seedless and unseen-recall design of [PubMed Search Builder](https://github.com/aarontaycheehsien/pubmed-search-builder-next/tree/main/evals). It uses included-study labels from two published reviews. Both fixtures come from the [SYNERGY dataset, pinned release tree](https://github.com/asreview/synergy-dataset/tree/7efde4a37bed9e8d71f19421b90eae3485184c4d). Each fixture includes the original row numbers, identifiers and a link to its source CSV. Label data are offered under the upstream [CC0 dedication](https://github.com/asreview/synergy-dataset/blob/7efde4a37bed9e8d71f19421b90eae3485184c4d/LICENSE).

| Case | Domain | Included source rows |
|---|---|---:|
| [Bos et al.](https://doi.org/10.1016/j.jalz.2018.04.007) | Cerebral small vessel disease and dementia | 10 |
| [Anmarkrud et al.](https://doi.org/10.1007/s10648-021-09640-7) | Individual differences in readers' use of source information | 64 |

The benchmark uses December 31 of the year preceding each review's publication year as an explicit comparison cutoff. This is a benchmark convention. Retrospective runs use today's OpenAlex index and current metadata. Resolve gold works afresh, report unidentifiable rows, collapse duplicate work IDs, and report out-of-window works separately.

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

# Benchmark results

The fixed comparator strategies were run against the current OpenAlex index on
2026-11-24. Each search retained the fixture's publication cutoff. Supplied
seeds are excluded from the seeded condition's denominator.

| Review | Condition | Strategy | Retrieved | Relative recall | Works retrieved |
|---|---|---|---:|---:|---:|
| Bos 2018 | Seedless | Simple keyword baseline | 1/9 | 11.1% | 1,030 |
| Bos 2018 | Seedless | Expanded reference | 8/9 | 88.9% | 21,002 |
| Bos 2018 | Seeded | Simple keyword baseline | 1/6 | 16.7% | 1,030 |
| Bos 2018 | Seeded | Expanded reference | 5/6 | 83.3% | 21,002 |
| Anmarkrud 2021 | Seedless | Simple keyword baseline | 23/64 | 35.9% | 82,152 |
| Anmarkrud 2021 | Seedless | Expanded reference | 48/64 | 75.0% | 697,450 |
| Anmarkrud 2021 | Seeded | Simple keyword baseline | 22/61 | 36.1% | 82,152 |
| Anmarkrud 2021 | Seeded | Expanded reference | 45/61 | 73.8% | 697,450 |

These are comparator results from hand-authored queries. They show the recall
and workload tradeoff that the skill's iterative workflow is designed to
measure. They do not evaluate an agent-built strategy or estimate absolute
recall. The Bos fixture has ten included source rows. Nine distinct resolved
works fell within the benchmark date bound.

The accompanying live smoke test deliberately made the setting block miss
OpenAlex work `W2618851878`, changed that block from `astrophysics` to
`rural OR remote`, recovered the work, and verified that the delivered OQL and
validation archive matched the query executed by OpenAlex.

Run: [OpenAlex live checks #45370705846](https://github.com/aarontaycheehsien/openalex-search-builder/actions/runs/45370705846)

---

## Blind agent pilot runs (2026-11-25)

Single-run pilot evaluations built autonomously by an agent using `SKILL.md` and `oasb.py`, scored against each fixture's resolved gold set via `evals/bench.py` (or direct OQO intersection for `Langham-Putrow_2021_OACA`).

| Review | Suite | Corpus | Condition | Resolved gold | Base recall | Unseen recall | Works retrieved | Works / hit | Build API cost | No-abstract rescue (`cited-by`) |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Bos 2018 | `core` | `core` | Seedless | 9/10 | 9/9 (100.0%) | 6/6 (100.0%) | 8,506 | 945 | \$0.094 | — |
| Bos 2018 | `core` | `core` | Seeded | 6/10 | 6/6 (100.0%) | 6/6 (100.0%) | 6,972 | 1,162 | \$0.076 | — |
| Anmarkrud 2021 | `core` | `core` | Seedless | 64/64 | 43/64 (67.2%) | 38/59 (64.4%) | 4,526 | 105 | \$0.068 | — |
| Anmarkrud 2021 | `core` | `core` | Seeded | 61/64 | 40/61 (65.6%) | 36/57 (63.2%) | 6,673 | 167 | \$0.129 | — |
| Langham-Putrow 2021 (OACA) | `openalex-native` | `all` | Seedless | 114/134 | 100/114 (87.7%) | 97/111 (87.4%) | 2,915 (≤2020-11) / 6,570 (all) | 29 | ~\$0.03 | +6/6 no-abs misses → **106/114 (93.0%)** (+3,569 ≤2020) |
| Langham-Putrow 2021 (OACA) | `openalex-native` | `core` | Seedless (blind) | 113/134 | **105/113 (92.9%)** | **87/95 (91.6%)** | **3,590** (0.893× orig 4,019) | 34 | \$0.104 | — (baseline: 90/113 = 79.6%, 3,848 works, 0.957×) |
| Lewowski 2021 | `openalex-native` | `core` | Seedless | 40/46 | 33/40 (82.5%) | 20/27 (74.1%) | 1,144 | 35 | \$0.130 | +1/1 no-abs miss → **34/40 (85.0%)** (+1,374 works) |
| Moran 2020 | `openalex-native` | `core` | Seedless | 139/142 | 93/139 (66.9%) | 90/136 (66.2%) | 9,383 | 101 | \$0.073 | `query_too_slow` (>11s on 238-leaf subquery) |
| Walker 2018 | `openalex-native` | `core` | Seedless (blind) | 148/148 | 70/148 (47.3%, CI [39.4%, 55.3%]) | 62/140 (44.3%, CI [36.3%, 52.6%]) | 9,124 (0.187× orig 48,786) | 130 | \$0.211 | — |

### Methodological notes
- **Isolation caveat:** The `Bos_2018`, `Anmarkrud_2021`, `Lewowski_2021`, and `Moran_2020` runs used prompt-enforced subagent isolation inside prepared directories (`evals/bench.py prepare`) on the same VM rather than separate filesystems. `Langham-Putrow_2021_OACA` was built interactively in seedless mode before inspecting the review's 134 included studies (91 resolved by DOI/PMID + 23 matched by strict title+author+year; 18 unmatched conference/thesis/working-paper items).
- **Single-run variance:** Each row is a single agent run (`n=1`). Multi-run repeats and strict filesystem-isolated runs on the `frozen` held-out suite remain to be run.
- **Not precision:** `Works retrieved` and `Works / hit` reflect screening volume, not precision across the full result set.

### Recall intervals and workload (derived)

Derived from the counts in the table above: 95% Wilson score intervals for relative recall, and the workload ratio (works retrieved ÷ the records the original review screened, `original_screening_load` in each fixture). A ratio below 1 means fewer records to screen than the original review, which searched several sources; it says nothing about precision.

| Review | Suite | Corpus | Condition | Relative recall (95% CI) | Unseen recall (95% CI) | Works retrieved | Original screening load | Workload ratio |
|---|---|---|---|---|---|---:|---:|---:|
| Bos 2018 | `core` | `core` | Seedless | 9/9 (100.0%; 70.1–100.0%) | 6/6 (100.0%; 61.0–100.0%) | 8,506 | 5,726 | 1.49× |
| Bos 2018 | `core` | `core` | Seeded | 6/6 (100.0%; 61.0–100.0%) | 6/6 (100.0%; 61.0–100.0%) | 6,972 | 5,726 | 1.22× |
| Anmarkrud 2021 | `core` | `core` | Seedless | 43/64 (67.2%; 55.0–77.4%) | 38/59 (64.4%; 51.7–75.4%) | 4,526 | 2,566 | 1.76× |
| Anmarkrud 2021 | `core` | `core` | Seeded | 40/61 (65.6%; 53.0–76.2%) | 36/57 (63.2%; 50.2–74.5%) | 6,673 | 2,566 | 2.60× |
| Langham-Putrow 2021 (OACA) | `openalex-native` | `all` | Seedless | 100/114 (87.7%; 80.4–92.5%) | 97/111 (87.4%; 79.9–92.3%) | 2,915 (works ≤2020-11) | 4,019 | 0.73× |
| Langham-Putrow 2021 (OACA) | `openalex-native` | `core` | Seedless (blind) | 105/113 (92.9%; 86.7–96.4%) | 87/95 (91.6%; 84.2–95.7%) | 3,590 | 4,019 | 0.89× |
| Lewowski 2021 | `openalex-native` | `core` | Seedless | 33/40 (82.5%; 68.0–91.2%) | 20/27 (74.1%; 55.3–86.8%) | 1,144 | 1,720 | 0.67× |
| Moran 2020 | `openalex-native` | `core` | Seedless | 93/139 (66.9%; 58.7–74.2%) | 90/136 (66.2%; 57.9–73.6%) | 9,383 | 5,453 | 1.72× |
| Walker 2018 | `openalex-native` | `core` | Seedless (blind) | 70/148 (47.3%; 39.4–55.3%) | 62/140 (44.3%; 36.3–52.6%) | 9,124 | 48,786 | 0.19× |

With no-abstract rescue (`cited-by`): Langham-Putrow 2021 (`all`) 106/114 (93.0%; 86.8–96.4%); Lewowski 2021 34/40 (85.0%; 70.9–92.9%).

- **Fixture layers:** every agent-built run so far is on the OpenAlex-native layer (the `core` and `openalex-native` suites, 12 fixtures). None of the 29 PubMed-subset fixtures has an agent-built run yet; report those separately when they exist, because their gold sets are only the PubMed-indexed part of each review.
- **Width of the intervals:** with 6 to 9 gold works (Bos 2018), 100% retrieval is compatible with a true relative recall near 61–70%. Differences between runs smaller than the overlap of their intervals are not evidence that one strategy is better.
- **Scoring rescue:** `bench.py score CASE DIR --report REPORT --with-rescue` now scores the delivered search together with the report's saved rescue queries (union recall with intervals, and an upper bound on screening volume).

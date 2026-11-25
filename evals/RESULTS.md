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
| Lewowski 2021 | `openalex-native` | `core` | Seedless | 40/46 | 33/40 (82.5%) | 20/27 (74.1%) | 1,144 | 35 | \$0.130 | +1/1 no-abs miss → **34/40 (85.0%)** (+1,374 works) |
| Moran 2020 | `openalex-native` | `core` | Seedless | 139/142 | 93/139 (66.9%) | 90/136 (66.2%) | 9,383 | 101 | \$0.073 | `query_too_slow` (>11s on 238-leaf subquery) |

### Methodological notes
- **Isolation caveat:** The `Bos_2018`, `Anmarkrud_2021`, `Lewowski_2021`, and `Moran_2020` runs used prompt-enforced subagent isolation inside prepared directories (`evals/bench.py prepare`) on the same VM rather than separate filesystems. `Langham-Putrow_2021_OACA` was built interactively in seedless mode before inspecting the review's 134 included studies (91 resolved by DOI/PMID + 23 matched by strict title+author+year; 18 unmatched conference/thesis/working-paper items).
- **Single-run variance:** Each row is a single agent run (`n=1`). Multi-run repeats and strict filesystem-isolated runs on the `frozen` held-out suite remain to be run.
- **Not precision:** `Works retrieved` and `Works / hit` reflect screening volume, not precision across the full result set.


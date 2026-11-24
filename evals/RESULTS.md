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

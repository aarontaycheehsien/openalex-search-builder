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
| Kapuka 2021 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium) | 27/27 | 21/27 (77.8%, CI [59.2%, 89.4%]) | 21/27 (77.8%, CI [59.2%, 89.4%]) | 7,722 (16.535× orig 467) | 368 | \$0.112 | — |
| Filges 2022 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium) | 28/28 | 19/28 (67.9%, CI [49.3%, 82.1%]) | 15/24 (62.5%, CI [42.7%, 78.8%]) | 30,933 (4.964× orig 6,232) | 1628 | \$0.095 | — |
| Noetel 2021 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium) | 27/29 | 24/27 (88.9%, CI [71.9%, 96.2%]) | 11/14 (78.6%, CI [52.4%, 92.4%]) | 1,510 (1.281× orig 1,179) | 63 | \$0.119 | — |
| Verdugo-Castro 2022 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium) | 26/26 | 21/26 (80.8%, CI [62.1%, 91.5%]) | 20/25 (80.0%, CI [60.9%, 91.1%]) | 29,810 (7.031× orig 4,240) | 1420 | \$0.101 | — |
| Cinquin 2018 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium) | 27/27 | 16/27 (59.3%, CI [40.7%, 75.5%]) | 15/26 (57.7%, CI [39.0%, 74.5%]) | 5,091 (5.243× orig 971) | 318 | \$0.125 | — |
| Kapuka 2021 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium, skill `d0f174f`) | 27/27 | 23/27 (85.2%, CI [67.5%, 94.1%]) | 23/27 (85.2%, CI [67.5%, 94.1%]) | 16,820 (36.017× orig 467) | 731 | \$0.141 | — |
| Filges 2022 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium, skill `d0f174f`) | 28/28 | 18/28 (64.3%, CI [45.8%, 79.3%]) | 17/27 (63.0%, CI [44.2%, 78.5%]) | 19,003 (3.049× orig 6,232) | 1056 | \$0.142 | — |
| Noetel 2021 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium, skill `d0f174f`) | 27/29 | 25/27 (92.6%, CI [76.6%, 97.9%]) | 17/19 (89.5%, CI [68.6%, 97.1%]) | 7,293 (6.186× orig 1,179) | 292 | \$0.128 | — |
| Verdugo-Castro 2022 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium, skill `d0f174f`) | 26/26 | 21/26 (80.8%, CI [62.1%, 91.5%]) | 21/26 (80.8%, CI [62.1%, 91.5%]) | 54,212 (12.786× orig 4,240) | 2582 | \$0.143 | — |
| Cinquin 2018 | `openalex-native` | `core` | Seedless (blind, Opus 5.5 medium, skill `d0f174f`) | 27/27 | 17/27 (63.0%, CI [44.2%, 78.5%]) | 14/24 (58.3%, CI [38.8%, 75.5%]) | 4,918 (5.065× orig 971) | 289 | \$0.144 | — |

### Methodological notes
- **Isolation caveat:** The `Bos_2018`, `Anmarkrud_2021`, `Lewowski_2021`, and `Moran_2020` runs used prompt-enforced subagent isolation inside prepared directories (`evals/bench.py prepare`) on the same VM rather than separate filesystems. `Langham-Putrow_2021_OACA` was built interactively in seedless mode before inspecting the review's 134 included studies (91 resolved by DOI/PMID + 23 matched by strict title+author+year; 18 unmatched conference/thesis/working-paper items).
- **Single-run variance:** Each row is a single agent run (`n=1`). Multi-run repeats and strict filesystem-isolated runs on the `frozen` held-out suite remain to be run.
- **Batch of 2026-11-26 (Kapuka 2021, Filges 2022, Noetel 2021, Verdugo-Castro 2022, Cinquin 2018):** five blind seedless builds with the skill as of commit `78399d7` (before the SKILL.md changes in `e4ca1f8`), one run each, by Opus 5.5 at medium effort as separate subagents, each confined by its instructions to a directory prepared with `bench.py prepare` on the same VM (prompt-enforced isolation, as above), with web and literature tools forbidden, a $0.15 OpenAlex budget, a self-written internal critique, and no rescue. A log check of every run found all development works traceable to the run's own discovery batches, every single-work lookup to a work those batches surfaced, and no DOI or PMID in any query. In Kapuka 2021 a related-works lookup surfaced what may be the source review (W4200017961); the agent screened it out as post-cutoff and did not use it or follow its citations (confirmed in the log). In every run the agent's own development set was retrieved in full (100%), while gold recall was 59–89%: development recall from a seedless build says little about recall. All five retrieved more records than the original review screened (1.3× to 16.5×).
- **Citation links changed on 25 November 2026:** OpenAlex stopped counting links inherited from Microsoft Academic Graph for works whose own reference list it holds (https://help.openalex.org/data/works/references/). Runs on 24–25 November may straddle the change, so their citation-neighbour discovery is not strictly comparable with the 26 November batches; both 26 November batches used the revised links.
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
| Kapuka 2021 | `openalex-native` | `core` | Seedless (blind) | 21/27 (77.8%; 59.2–89.4%) | 21/27 (77.8%; 59.2–89.4%) | 7,722 | 467 | 16.54× |
| Filges 2022 | `openalex-native` | `core` | Seedless (blind) | 19/28 (67.9%; 49.3–82.1%) | 15/24 (62.5%; 42.7–78.8%) | 30,933 | 6,232 | 4.96× |
| Noetel 2021 | `openalex-native` | `core` | Seedless (blind) | 24/27 (88.9%; 71.9–96.2%) | 11/14 (78.6%; 52.4–92.4%) | 1,510 | 1,179 | 1.28× |
| Verdugo-Castro 2022 | `openalex-native` | `core` | Seedless (blind) | 21/26 (80.8%; 62.1–91.5%) | 20/25 (80.0%; 60.9–91.1%) | 29,810 | 4,240 | 7.03× |
| Cinquin 2018 | `openalex-native` | `core` | Seedless (blind) | 16/27 (59.3%; 40.7–75.5%) | 15/26 (57.7%; 39.0–74.5%) | 5,091 | 971 | 5.24× |
| Kapuka 2021 | `openalex-native` | `core` | Seedless (blind, skill `d0f174f`) | 23/27 (85.2%; 67.5–94.1%) | 23/27 (85.2%; 67.5–94.1%) | 16,820 | 467 | 36.02× |
| Filges 2022 | `openalex-native` | `core` | Seedless (blind, skill `d0f174f`) | 18/28 (64.3%; 45.8–79.3%) | 17/27 (63.0%; 44.2–78.5%) | 19,003 | 6,232 | 3.05× |
| Noetel 2021 | `openalex-native` | `core` | Seedless (blind, skill `d0f174f`) | 25/27 (92.6%; 76.6–97.9%) | 17/19 (89.5%; 68.6–97.1%) | 7,293 | 1,179 | 6.19× |
| Verdugo-Castro 2022 | `openalex-native` | `core` | Seedless (blind, skill `d0f174f`) | 21/26 (80.8%; 62.1–91.5%) | 21/26 (80.8%; 62.1–91.5%) | 54,212 | 4,240 | 12.79× |
| Cinquin 2018 | `openalex-native` | `core` | Seedless (blind, skill `d0f174f`) | 17/27 (63.0%; 44.2–78.5%) | 14/24 (58.3%; 38.8–75.5%) | 4,918 | 971 | 5.07× |

With no-abstract rescue (`cited-by`): Langham-Putrow 2021 (`all`) 106/114 (93.0%; 86.8–96.4%); Lewowski 2021 34/40 (85.0%; 70.9–92.9%).

- **Fixture layers:** every agent-built run so far is on the OpenAlex-native layer (the `core` and `openalex-native` suites, 12 fixtures). None of the 29 PubMed-subset fixtures has an agent-built run yet; report those separately when they exist, because their gold sets are only the PubMed-indexed part of each review.
- **Width of the intervals:** with 6 to 9 gold works (Bos 2018), 100% retrieval is compatible with a true relative recall near 61–70%. Differences between runs smaller than the overlap of their intervals are not evidence that one strategy is better.
- **Scoring rescue:** `bench.py score CASE DIR --report REPORT --with-rescue` now scores the delivered search together with the report's saved rescue queries (union recall with intervals, and an upper bound on screening volume).

### Skill versions compared: `78399d7` → `d0f174f` (2026-11-26)

The same five fixtures, rebuilt blind with the latest committed skill (`d0f174f`: multi-seed `neighbors`, `sample --from-strategy`, the guidance against enumerating open-ended facets and against pruning core synonyms to fit a volume ceiling, protected `--role validation` allocation, and the no-abstract fallback guidance). Same model (Opus 5.5, medium effort), instructions, $0.15 budget and scoring. One run per fixture per version.

| Review | Gold retrieved | Works retrieved | Workload ratio | Agent's protected validation (run 2) |
|---|---:|---:|---:|---:|
| Kapuka 2021 | 21/27 → 23/27 | 7,722 → 16,820 | 16.5× → 36.0× | 8/8 |
| Filges 2022 | 19/28 → 18/28 | 30,933 → 19,003 | 5.0× → 3.0× | 4/4 |
| Noetel 2021 | 24/27 → 25/27 | 1,510 → 7,293 | 1.3× → 6.2× | 5/5 |
| Verdugo-Castro 2022 | 21/26 → 21/26 | 29,810 → 54,212 | 7.0× → 12.8× | 6/8 |
| Cinquin 2018 | 16/27 → 17/27 | 5,091 → 4,918 | 5.2× → 5.1× | 8/8 |
| **Total** | **101/135 → 104/135** | **75,066 → 102,246** | | |

- **Recall:** pooled 101/135 (74.8%; 66.9–81.4%) → 104/135 (77.0%; 69.2–83.3%). Three more gold works across five reviews, with overlapping intervals: no evidence yet that the newer skill changes recall. Pooling gold works across different reviews is a rough summary only.
- **Workload:** 36% more records in total. The newer guidance against pruning core synonyms produced broader searches in three reviews (Noetel 1.3× → 6.2×, Kapuka 16.5× → 36.0×, Verdugo-Castro 7.0× → 12.8×) for at most two more gold works each; Filges narrowed (5.0× → 3.0×) and Cinquin was unchanged.
- **Protected validation is not a recall estimate:** four of the five agents scored 100% on their own validation works (the fifth 75%), while gold recall was 63–93%. Works the agent finds through its own citation searches share its blind spots; SKILL.md's label "protected, not blinded" is accurate and should stay prominent.
- **Blinding:** log checks as for the first batch found no leakage. Kapuka 2021 (again W4200017961) and Verdugo-Castro 2022 (W4292874629) surfaced possible source reviews in citation lists; Filges 2022 surfaced a review protocol (W3146159216). None was used (logs confirm). Two agents wrote scratch files to `/tmp` outside their folder, a minor departure from the instructions that gives no access to answers.

### Capture–recapture and the agents' eligibility judgements (2026-11-26)

Computed afterwards, with no API calls, from each blind run's own discovery batches and screening decisions: eligible works found by text routes (keyword samples, related works) and by citation routes, the overlap, Chapman's estimate of the total with a 95% interval, and the share of that estimate discovery reached. The last two columns compare the works each agent screened as eligible with the review's included studies (resolved in OpenAlex).

| Review | Skill | Text route | Citation route | Both | Estimated eligible works (95% CI) | Discovery completeness | Agent's eligible works that are gold | Gold |
|---|---|---:|---:|---:|---|---:|---:|---:|
| Kapuka 2021 | `78399d7` | 9 | 3 | 0 | 39 (12–84) | 31% | 0/12 | 27 |
| Kapuka 2021 | `d0f174f` | 9 | 14 | 1 | 74 (22–145) | 30% | 0/22 | 27 |
| Filges 2022 | `78399d7` | 7 | 11 | 1 | 47 (17–90) | 36% | 4/17 | 28 |
| Filges 2022 | `d0f174f` | 7 | 7 | 1 | 31 (13–58) | 42% | 1/13 | 28 |
| Noetel 2021 | `78399d7` | 15 | 17 | 4 | 57 (28–89) | 50% | 13/28 | 28 |
| Noetel 2021 | `d0f174f` | 10 | 11 | 2 | 43 (19–75) | 44% | 12/19 | 28 |
| Verdugo-Castro 2022 | `78399d7` | 20 | 5 | 0 | 125 (25–281) | 20% | 1/25 | 26 |
| Verdugo-Castro 2022 | `d0f174f` | 13 | 22 | 2 | 106 (33–193) | 31% | 1/33 | 26 |
| Cinquin 2018 | `78399d7` | 16 | 3 | 0 | 67 (19–146) | 28% | 1/19 | 27 |
| Cinquin 2018 | `d0f174f` | 24 | 23 | 5 | 99 (43–155) | 42% | 3/42 | 27 |

- **Low overlap with gold is ambiguous.** Of the works each agent judged eligible, 0–63% (median 6%) are among the review's included studies. That could mean over-inclusion, or that a review's included set is only part of the eligible literature (its final inclusion also reflects full-text screening and scope decisions not stated in the fixture). The screening check below separates the two.
- **The estimate is very uncertain here.** Only 0–5 eligible works were found by both routes, so the intervals are wide, and the five reruns predate the recording of hidden validation works, which can push their estimates up. Low completeness (20–50%) consistently signalled that discovery was incomplete by the agent's own standard; it cannot be checked against gold because the agents' eligibility differs from the reviews'.

### Agent screening of gold works (2026-11-26)

From `bench.py score`'s `screening` measure: how many of each review's included studies discovery surfaced, and how the agent decided on those it screened (an include in any batch counts).

| Review | Skill | Gold | Seen in discovery | Screened | Included | Unclear | Excluded | Agent's eligible works that are gold |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Kapuka 2021 | `78399d7` | 27 | 0 | 0 | 0 | 0 | 0 | 0/12 |
| Kapuka 2021 | `d0f174f` | 27 | 1 | 1 | 0 | 1 | 0 | 0/22 |
| Filges 2022 | `78399d7` | 28 | 6 | 5 | 4 | 1 | 0 | 4/17 |
| Filges 2022 | `d0f174f` | 28 | 2 | 1 | 1 | 0 | 0 | 1/13 |
| Noetel 2021 | `78399d7` | 28 | 14 | 14 | 13 | 1 | 0 | 13/28 |
| Noetel 2021 | `d0f174f` | 28 | 13 | 13 | 12 | 1 | 0 | 12/19 |
| Verdugo-Castro 2022 | `78399d7` | 26 | 1 | 1 | 1 | 0 | 0 | 1/25 |
| Verdugo-Castro 2022 | `d0f174f` | 26 | 1 | 1 | 1 | 0 | 0 | 1/33 |
| Cinquin 2018 | `78399d7` | 27 | 1 | 1 | 1 | 0 | 0 | 1/19 |
| Cinquin 2018 | `d0f174f` | 27 | 4 | 3 | 3 | 0 | 0 | 3/42 |
| **Total** | | | **43** | **40** | **36** | **4** | **0** | |

- **Screening is not the problem.** The agents included 36 of the 40 gold works they screened, marked 4 unclear and excluded none. The unclear ones were borderline (a meta-analysis of a learner trait, a review of eye-tracking studies, a reef study whose countries were uncertain), so stricter screening would have lost them.
- **Discovery reach is.** Discovery surfaced 0–14 of each review's 26–28 included studies (Kapuka 2021: 0 and 1), while the final searches retrieved 59–93% of them. The development set is a small, vocabulary-bound corner of the eligible literature, which is why development and protected-validation recall said nothing about gold recall.
- **Correction:** an earlier version of this section concluded that the agents screen too generously and suggested stricter screening; the screening data above do not support that, and SKILL.md no longer says it.

### Why discovery reaches so few included studies (2026-11-26)

For each blind run: the agent's development works, the review's included studies (gold, resolved), gold works one citation step from the development works (cited by or citing them), gold works that cite or are cited by another gold work, and gold works among the first 200 records of the delivered search in OpenAlex's relevance order compared with a random sample of 200. Citation checks used matched references (`referenced_works`).

| Review | Skill | Development works | Gold | Gold one step from development | Gold linked to other gold | Gold in top 200 | Random 200 would hold |
|---|---|---:|---:|---:|---:|---:|---:|
| Kapuka 2021 | `78399d7` | 12 | 27 | 2 | 6 | 2 | 0.5 |
| Kapuka 2021 | `d0f174f` | 22 | 27 | 4 | 6 | 0 | 0.3 |
| Filges 2022 | `78399d7` | 17 | 28 | 7 | 9 | 0 | 0.1 |
| Filges 2022 | `d0f174f` | 13 | 28 | 5 | 9 | 0 | 0.2 |
| Noetel 2021 | `78399d7` | 28 | 28 | 20 | 21 | 16 | 3.2 |
| Noetel 2021 | `d0f174f` | 19 | 28 | 17 | 21 | 13 | 0.7 |
| Verdugo-Castro 2022 | `78399d7` | 25 | 26 | 4 | 8 | 1 | 0.1 |
| Verdugo-Castro 2022 | `d0f174f` | 33 | 26 | 4 | 8 | 0 | 0.1 |
| Cinquin 2018 | `78399d7` | 19 | 27 | 0 | 6 | 5 | 0.6 |
| Cinquin 2018 | `d0f174f` | 42 | 27 | 4 | 6 | 2 | 0.7 |

- **Random samples cannot reach the included studies.** They make up 0.1–1.6% of these searches, so a 20–100 record screening sample rarely holds one.
- **Snowballing helps only where the studies cite each other.** In four of the five reviews just 6–9 of 26–28 included studies are linked to another included study, and one citation step from the agents' development works reached 0–7 of them. Noetel 2021, an umbrella review whose included meta-analyses cite each other, is the exception (17–20).
- **Relevance ranking helps, modestly.** The top 200 held 39 included studies against 6.5 expected from random samples (6 times as many), but 29 of the 39 were Noetel's; for the other four reviews it was 10 against 2.6 (about 4 times), 0–5 per run. `sample --top` now screens best matches first.
- **The structural remedy is seeds:** known eligible studies from the reviewer. A seedless run's development set is a vocabulary aid, not a recall check; the seeded benchmark condition has not yet been run blind.

- **Capture–recapture demoted (2026-11-26):** discovery completeness tracked gold recall only weakly across these ten runs (Spearman 0.31, n = 10), the intervals spanned 1.1–2 times the estimate, and two runs on one review differed up to twofold. The tool now reports it only with at least 20 eligible works from each route and 10 found by both, as a diagnostic outside the audit.

### Recall levers tested on the blind deliveries (2026-11-26)

**Dropping a whole block.** 21 of the archived `without/` lines gained gold works, but most multiplied the search 15–500 times. The only ones under five times the volume:

| Review | Run | Block dropped | Gold gained | Volume | Extra records per gold work |
|---|---:|---|---:|---:|---:|
| Filges 2022 | 1 | `population` | +3 | 1.8× | 8,214 |
| Verdugo-Castro 2022 | 2 | `hied` | +4 | 2.3× | 17,050 |
| Noetel 2021 | 2 | `learning` | +1 | 3.1× | 14,988 |
| Filges 2022 | 2 | `k12` | +4 | 4.5× | 16,787 |

These are population or setting blocks, and even they take the searches to 9–29 times the original reviews' screening loads. Dropping the blocks where most misses concentrate (region in Kapuka, e-learning in Cinquin) costs 39–241 times the volume. No rule to move setting blocks to screening is justified.

**Adding OpenAlex keywords to a block** (`title-abstract-keywords`), tested on every title or title-abstract search block of the five reruns (13 blocks):

| Review | Block | Field before | Gold gained | Volume | Extra records per gold work |
|---|---|---|---:|---:|---:|
| Filges 2022 | `sl` | title-abstract | +2 | 1.33× | 3,166 |
| Cinquin 2018 | `cognitive` | title-abstract | +2 | 1.10× | 248 |
| Cinquin 2018 | `disab_title` | title | +2 | 3.01× | 4,949 |

The other 10 blocks gained nothing (most at little or no extra volume; two title-only blocks rose 3–3.7 times). This supports SKILL.md's advice to pilot the keywords field block by block.

**No-abstract citation rescue** (reference-list method, `cited-by`), run on the two Cinquin 2018 deliveries, each with 7 of its misses lacking an abstract:

| Review | Run | Gold before | Gold with rescue (95% CI) | Abstract-less misses recovered | Records (search + rescue) | Rescue records per gold work |
|---|---:|---:|---|---:|---:|---:|
| Cinquin 2018 | 1 | 16/27 | 18/27 (67%; 48–81%) | +2 of 7 | 5,091 + 3,763 | 1,882 |
| Cinquin 2018 | 2 | 17/27 | 19/27 (70%; 52–84%) | +2 of 7 | 4,918 + 4,083 | 2,042 |

Each run cost about $0.22, matching `rescue --estimate`. The other eight deliveries' rescues are estimated at $0.07–2.48 each ($7.6 in total) and are queued in ROADMAP.md.

### Batch 3: current skill, seedless and seeded (2026-11-27)

The same five fixtures, rebuilt blind with the skill on main at `8e85d78` (the rewritten SKILL.md, reference-list rescue, `sample --top` and `--added`, the date window on discovery, capture–recapture demoted to a diagnostic). Each fixture was run twice: seedless as before, and seeded with three of the review's included studies supplied as reviewer-known works. Same model (Opus 5.5, medium effort), instructions, $0.15 budget and scoring (`bench.py score --explain`). One run per fixture per condition. In the seeded condition the three seeds are removed from the test set, so gold is 23–25 works per review instead of 26–28.

| Review | Batch 1 (`78399d7`) | Batch 2 (`d0f174f`) | Batch 3 seedless | Batch 3 seeded |
|---|---:|---:|---:|---:|
| Kapuka 2021 | 21/27 · 7,722 | 23/27 · 16,820 | 23/27 · 13,966 | 24/24 · 21,204 |
| Filges 2022 | 19/28 · 30,933 | 18/28 · 19,003 | 22/28 · 35,076 | 21/25 · 41,496 |
| Noetel 2021 | 24/27 · 1,510 | 25/27 · 7,293 | 25/27 · 5,217 | 22/24 · 8,531 |
| Verdugo-Castro 2022 | 21/26 · 29,810 | 21/26 · 54,212 | 10/26 · 19,119 | 20/23 · 46,646 |
| Cinquin 2018 | 16/27 · 5,091 | 17/27 · 4,918 | 16/27 · 7,016 | 14/24 · 4,035 |
| **Pooled gold recall** | 101/135, 74.8% (66.9–81.4%) | 104/135, 77.0% (69.2–83.3%) | 96/135, 71.1% (63.0–78.1%) | 101/120, 84.2% (76.6–89.6%) |
| **Records, total** | 75,066 | 102,246 | 80,394 | 121,912 |
| **Build spend, total** | $0.55 | $0.70 | $0.76 | $0.72 |

Each cell gives gold retrieved and works retrieved.

- **Seedless, current skill against batch 2:** no evidence of change. The pooled drop (77.0% → 71.1%, overlapping intervals) comes from one run. The Verdugo-Castro seedless agent ANDed a higher-education block, a defensible reading of the question ("gender gap in higher education STEM studies"), but 14 of its 16 misses are school-level or all-level works that fail that block. Batch 2's agent lost 4 gold works to the same kind of block. Without Verdugo-Castro, the seedless batches are 83/109 (76.1%) and 86/109 (78.9%).
- **Seeded against seedless, scored on the same test set (gold minus the seeds):**
  - Pooled: 86/120 (71.7%; 63.0–79.0%) seedless against 101/120 (84.2%; 76.6–89.6%) seeded.
  - Per review: Kapuka +3, Filges +1, Noetel 0, Verdugo-Castro +10, Cinquin +1.
  - Most of the gain is scope correction, not vocabulary:
    - Verdugo-Castro: one seed is a school-level study, so the agent treated the higher-education context as a focus rather than a block.
    - Kapuka: one seed is an Africa-wide study naming no southern African country, so the agent added an Africa-wide branch, recovering the four region-block misses of the seedless run.
  - Where the misses are vocabulary or missing abstracts (Cinquin, Noetel), seeds changed little.
  - Seeded searches were 52% larger in total (121,912 against 80,394 records).
  - With one run per condition, this is a signal, not an estimate of the effect of seeds.
- **Misses by cause (`--explain`):**
  - Seedless: 39 misses, 17 of them without an abstract.
  - Seeded: 19 misses, 9 without an abstract.
  - In both conditions they concentrate in one block per review: region (Kapuka), core service-learning labels (Filges), higher education (Verdugo-Castro seedless), and the cognitive-disability or e-learning block (Cinquin).
- **Screening of gold works:**
  - Across the ten runs the agents screened 41 gold works: 38 included, 3 unclear, 0 excluded (92.7%; 80.6–97.5% included).
  - They again reached few gold works during discovery: 0–17 per run, median 2.5.
- **Agents' own recall figures were again uninformative.** Every reported seed, development and validation figure was 100% in 6 of 10 runs; the lowest were 67% (Verdugo-Castro seeded, seeds 2/3; Cinquin seeded, validation 2/3). Gold recall was 38–100%. Where the agents' own validation missed works (Verdugo-Castro seedless, 3/4; Kapuka seeded development, 4/5), the misses matched the gold failure mode (higher-education block; climate phrasing).
- **Blinding:**
  - The leak check found no registered work outside discovery except each seeded run's intake seeds.
  - The Filges seeded agent saw a protocol title (W3146159216) in a citation batch and excluded it as a protocol without reading it, as in batch 2.
  - The Kapuka seedless agent's one attempt to read the `oasb` wrapper was blocked by the permission check, and it did not retry.
- **Operational problems found:**
  - **Budget overruns:** four runs exceeded the $0.15 budget ($0.150, $0.164, $0.168, $0.228). The agents did not reserve for `report`, which re-runs the full evaluation (about $0.04), and the seedless key ran out ($1.00 for five runs plus scoring). SKILL.md should say what `report` costs, or `eval` should print the cost of the delivery evaluation.
  - **Placeholder refusal:** four agents had `{without_abstract_pct}` refused by `review`. SKILL.md lists it as an example, but `review` offers it only after an `eval --abstract-stats`. Either SKILL.md should say so or `review` should fill it as "not measured".
  - **Skipped checks:** seven of ten agents skipped `--abstract-stats`, `term-counts` or the keywords pilot to save budget.

### Gold-set index profile via `evals/replay.py gold-profile` (2026-11-27)

Zero-credit single-work lookups across all 135 resolved, in-window gold works of the five `openalex-native` fixtures above (`python evals/replay.py gold-profile CASE`; `frozen` held-out fixtures are refused):

| Review | In-window gold | Without abstract | Abstract-less with OpenAlex `keywords` | Abstract-less with `has_fulltext` | Non-`article` work types | Top-1 topic share (`topic` primary → `topics` any) |
|---|---:|---:|---:|---:|---|---|
| Kapuka 2021 | 27 | 11 (40.7%) | **11/11 (100%)** | 2/11 (18%) | 0 (`article`: 27) | `T10895`: 25.9% → 37.0% |
| Filges 2022 | 28 | 12 (42.9%) | **12/12 (100%)** | 1/12 (8%) | 12 (`report`: 4, `dissertation`: 4, `conference-paper`: 2, `book-chapter`: 2) | `T11806`: **32.1% → 67.9%** |
| Noetel 2021 | 27 | 19 (70.4%) | **19/19 (100%)** | 0/19 (0%) | 9 non-`review` (`review`: 18, `article`: 7, `book-chapter`: 1, `conference-paper`: 1) | `T11516`: 88.9% → 88.9% |
| Verdugo-Castro 2022 | 26 | 2 (7.7%) | **2/2 (100%)** | 1/2 (50%) | 10 (`conference-paper`: 9, `book`: 1) | `T10726`: **57.7% → 80.8%** |
| Cinquin 2018 | 27 | 12 (44.4%) | **12/12 (100%)** | 1/12 (8%) | 15 (`conference-paper`: 15) | `T12481`: **40.7% → 55.6%** |
| **Pooled** | **135** | **56 (41.5%)** | **56/56 (100%)** | **5/56 (8.9%)** | | |

What this establishes before paying for new agent runs:
- **`keywords`, not `fulltext`, is the index signal on abstract-less misses:** 56 of 135 (41.5%) gold works lack an abstract in OpenAlex, and only 5 of those 56 (8.9%) have `has_fulltext: true` (`0/19` on Noetel, `1/12` on Filges and Cinquin), ruling out `fulltext` as a general remedy for missing abstracts. All **56 of 56 (100%)** carry OpenAlex `keywords`, directly motivating `rescue --keywords-gate` and `title-abstract-keywords` on no-abstract fallback branches.
- **`limits.type` is unsafe even on reviews-of-reviews:** on `Noetel_2021` (an umbrella review restricted to systematic reviews and meta-analyses), OpenAlex types 9 of 27 (33%) included reviews as `article`, `book-chapter`, or `conference-paper` rather than `review`; on `Filges`, `Verdugo-Castro`, and `Cinquin`, 38–56% of gold works are conference papers, reports, dissertations, or books/chapters. Review-only eligibility should combine `type` with review text terms rather than applying a hard `limits.type: ["review"]` filter.
- **`topics` (`any_topic`) beats `topic` (`primary_topic`) on multidisciplinary reviews:** OpenAlex assigns up to three topics per work, and OQL `topic is T...` matches `primary_topic.id` only. Matching any assigned topic (`topics is T...`, supported via `any_topic` / `any_topics`) raises single-topic gold coverage from 32.1% to 67.9% on `Filges_2022` and from 57.7% to 80.8% on `Verdugo-Castro_2022`.

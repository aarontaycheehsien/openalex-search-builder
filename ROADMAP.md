# Roadmap, Bug Fixes & Improvement Log

Running record of implemented fixes/features and open improvements surfaced during live OpenAlex runs and blind benchmark evaluations.

## Implemented & fixed (2026-11-24 – 2026-11-25)

| Area | Change | Commit |
|---|---|---|
| Core search & export | Added resumable cursor-paged `export` (CSV/RIS/JSONL + SQLite checkpoint), `corpus` (`core`/`all`/`expansion`) in scope & queries, native OQL proximity (`{"near": [...], "within": N}`), `fulltext` field, `exclude` blocks, `combine` expressions, leaf-count translation guard (`check_translation`), `miss_details`, and push/PR offline CI workflow. | `2b37901` |
| Bug fix (live drift) | Added `normalize_warnings()` in `evidence()` to canonicalize OpenAlex's non-deterministic `Fields involved: ...` ordering in `url_not_representable` warnings, fixing false `Live validation changed the retrieval evidence` errors during `report`. | `82c2c54` |
| Vocabulary | Added `vocab keywords|topics "<query>"` for live OpenAlex keyword alternatives (`alternatives`) and topic keyword lists. | `b27eb4f` |
| Snowballing | Added cursor paging (`--size` > 100, `--all`, `--cursor`, `--max-pages`) and `--direction related` to `neighbors`. | `6a37c80` |
| Offline workflow | Added zero-API `lint` (heuristic checks for unquoted hyphens, short wildcards, stemmed wildcards, duplicates, missing rationales, `>=4` ANDed blocks, exclusions) and `draft` (`draft/draft-query.oql`, `draft-notes.md`, `draft-manifest.json`). | `cd4e0de` |
| Skill guidance | Folded empirical findings into `SKILL.md`: brainstorming older/adjacent sub-domain labels in seedless runs (preprints/e-prints/arXiv) and checking `sample` noise checks for CC-BY / repository abstract boilerplate. | `c4e3b72` |
| Missing-abstract rescue | Added optional post-delivery `rescue` command (`it doesn't have an abstract` + any-block or `--topic` gate + citation-set links to delivered Boolean query) and `export --rescue`, always offered after `report`. | `e9f290b` |
| Missing-abstract diagnostics & filters | Added `eval --abstract-stats` (missing-abstract attrition table in `eval` and `audit.md`) and `has_abstract` / `topic` / `topics` filters in `strategy.json` (`limits` and per-block) for conditional no-abstract fallback branches via `combine`. | `e588467` |
| Benchmarks | Ported 29 fixtures from `pubmed-search-builder-next` (including 10 `frozen` held-out) plus 10 non-biomedical `openalex-native` fixtures (9 zero-PMID SYNERGY datasets + `Langham-Putrow_2021_OACA`), bringing the suite to 41 fixtures with `bench.py list`. | `74e4970`, `fd82826` |
| Benchmark screening loads | Added verified `original_screening_load` (39/41 verified, 2 explicitly `null`) + `original_screening_load_source` to all 41 fixtures and `workload_ratio` to `bench.py score`. | `050301e` |
| Bug fix (silent corpus drop in rescue) | OpenAlex accepts `(works (all corpora) where ...)` as a nested set but drops its corpus without a warning, so `rescue` on a `corpus: all` strategy drew citation links from the core corpus only. Nested sets are now written as `(get works <corpus> where ...)`. The translation guard now descends into nested sets (leaf count and corpus at every level), compares strategy words with the query object to catch a dropped term hidden by a split, and is applied to `rescue`. | `252e968` |
| Recall uncertainty | 95% Wilson score intervals on relative recall in `eval`, `audit.md` and benchmark scorecards (`relative_recall_ci95`, `unseen_recall_ci95`). | `252e968` |
| Holdout exposure | Each `report` records the validation/benchmark works its audit revealed (`exposure.json`, outside the input fingerprint). Later reports are labelled post-exposure and score holdouts added since then separately; `eval` warns when holdouts are exposed. | `252e968` |
| Export cost estimate | `export --estimate` (free): pages, credits and USD from OpenAlex's cost check where returned, otherwise the documented per-request price (1 credit filter-only, 10 with a search, whatever the page size). | `252e968` |
| Bug fix (pulled works scored as retrieved) | Since 2026-11-19 OpenAlex leaves pulled (retracted, withdrawn, removed) works out of lists and counts, but an ID filter brings them back, so `members()` scored pulled known works as retrieved although the search and export left them out. Every request now sends `include_pulled` explicitly from a new strategy/scope setting `"pulled": "exclude"|"include"`; pulled known works are misses with reason `pulled`; `eval`/`audit.md` count the pulled works the search leaves out or includes; CSV gains `is_pulled`/`is_retracted`; benchmarks report pulled gold works separately. This also explained the "round" citation-set counts (36,000 = 36,023 existing references − 23 pulled): there is no cap on citation walks. | `19a24b6` |

---

## Open bugs & improvements (prioritized)

### 1. High — `rescue` hits OpenAlex `query_too_slow` (>11s) on large Boolean strategies (mostly done)
- **Observed:** On `Lewowski_2021` (~180 leaves), `rescue --direction both` failed with HTTP 400 `query_too_slow` because `both` embeds the full Boolean query twice inside `set (works where ...)`, whereas `--direction cited-by` succeeded in seconds and rescued 1/1 abstract-less miss. On `Moran_2020` (238 leaves), even single-direction subquery materialization timed out.
- **Also observed (2026-11-25):** with or without an API key, rescue intermittently fails with HTTP 500 `es_error` "Elasticsearch error: Connection timed out", even on the small `examples/telehealth.json` strategy and on `cited-by` alone. Failures are erratic, not size-driven (a 47k-work set failed while a 63k-work set succeeded), and OpenAlex's own pre-check reported these queries `within_budget` (6–9 s of 10 s), so `check.estimate` does not predict them. `api.request` does not retry HTTP 500.
- **Done (see Implemented):** retry of HTTP 500 `es_error` timeouts with 2/4/8 s backoff (`query_too_slow` refusals are not retried); warning when OpenAlex's estimate is within 80% of its time budget; default `--direction cited-by`; `both` run as two single-direction queries, deduplicated on export; actionable advice when a direction still times out.
- **Live retest (2026-11-25):** `examples/telehealth.json` (29,673 works) now completes in both directions. `examples/telehealth-advanced.json` (40,622 works, all corpora, proximity, exclusion) still times out on every attempt, with or without `--topic`: OpenAlex's estimate stays at 8.0 s of 10 s, so the cost is rebuilding the delivered set, not the outer gate.
- **Export of rescue records (open, important):** paging a citation-walk query re-runs the walk for every 100-record page. For the basic example the rescue counts succeeded, but all 12 cursor-paging attempts (3 runs × 4 retries) timed out, so `export --rescue` could not download a single page. The export fails safely (incomplete, resumable), but rescue records are currently unobtainable for searches of this size.
- **Not done yet — reference-list rescue for `cited-by`:** avoid rebuilding the delivered set inside the query. Take each delivered work's `referenced_works` from the primary export's `results.jsonl`, keep the IDs absent from the delivered set, look them up by ID in batches of 100 with `has_abstract:false` (1 credit per 100 IDs), then apply the any-block or `--topic` gate with OQL on those IDs (about 10 credits per 100 candidates). The 2026-11-25 ground-truth test showed this reproduces OpenAlex's own walk exactly once pulled works are excluded. Estimated cost for `examples/telehealth.json` (29,673 works): about $1; much less for smaller searches. `export --estimate` should show the cost first, and the `citing` direction would still need the walk.
- **Still open:** a live retest on `Lewowski_2021` and `Moran_2020`.

### 2. Done — Stemming & single-token hyphen quote-stripping hazards
- **Observed:** In `Moran_2020`, stemming and quoted single-token hyphenated phrases (`"open-field"`, `"risk-prone"`, `"anti-predator"`) inflated initial hits to 42,154 before fixing brought them to 5,969.
- **Verified live (2026-11-25):** OpenAlex keeps a quoted hyphenated term on the exact field but runs it as an AND of its parts anywhere, not as a phrase: `"open-field"` 10,487 titles = `"open" AND "field"` 10,487, while the phrase `"open field"` gives 4,872 and also matches hyphenated titles. `"risk-prone"` 958 against 131; `"COVID-19"` is unaffected (968,014 against 967,735). `stemmed "risk-prone"` is a phrase (158 = `stemmed "risk prone"`). Stemming collisions are word-specific: `fasting` 471,555 stemmed against 33,935 exact (it stems to `fast`), but `ration` does not collide with `rational` (16,830 stemmed; `rational` alone 113,619). The translation guard cannot see these, because the term and its words survive translation.
- **Done:** `lint` warning `hyphenated_phrase` suggesting the spaced phrase; clearer `unquoted_hyphen` message; lint warnings returned as `strategy_warnings` by `validate` and `eval`; new `term-counts [--block ID]` command that counts each term alone (with the strategy's corpus, limits and pulled view) and flags `stemming_expands` (stemmed ≥ 3× exact and ≥ 100 more works) and `hyphen_not_phrase` (≥ 1.5× the spaced phrase and ≥ 50 more); guidance in `SKILL.md` step 5.

### 3. Medium — Stronger vocabulary & discovery tools
- **Enrichment-ranked `terms`:** Rank mined terms by frequency in development records vs. a background sample rather than raw document frequency (the objective approach of Hausner et al., 2012, https://doi.org/10.1186/2046-4053-1-19). Worth it mainly at `thorough` depth: with two or three seeds neither raw counts nor lift say much.
- **Multi-seed `neighbors`:** Allow passing multiple work IDs to `neighbors` in one deduplicated snowballing batch.
- **Post-export abstract enrichment (for screening):** Optional enrichment of missing abstracts in `results.csv` / `results.ris` via PubMed / Europe PMC / Crossref by DOI/PMID, with provenance logged in `export-manifest.json`.

### 4. Medium — Stricter critique & workspace ergonomics
- **Multi-round critique continuity:** Numbered findings across critique rounds with required dispositions and a `report --diagnostic` dry-run mode.
- **`doctor` command & response caching:** Quick check of `OPENALEX_API_KEY`, remaining credits, and API connectivity; optional per-workspace cache for repeated read-only calls.
- **`eval` irrelevant-set hit count:** Report how many `exclude`-screened development works are matched by the query alongside `sets.relevant` / `sets.seed` so agents can track development specificity directly (surfaced in blind `Langham-Putrow_2021_OACA` run).
- **`SKILL.md` `sample --oql` syntax example:** Add a 1-line canonical `sample --oql` example (`get works where title-abstract has (...) and publication_date <= ...`) to prevent initial HTTP 400 syntax trial-and-error during seedless discovery (surfaced in blind `Langham-Putrow_2021_OACA` run).

### 5. Medium — Benchmark expansion & rigor
- Run blind builds across the remaining `openalex-native` fixtures (`Walker_2018`, `Kapuka_2021`, `Filges_2022`, `Noetel_2021`, `Verdugo-Castro_2022`, `Cinquin_2018`, `Ruggeri_2019`) and the 10 `frozen` held-out fixtures (with strict filesystem isolation and 2–3 repeats per condition to measure variance).
- Add a `bench.py score --with-rescue` option to automatically score `final-query.json` + `rescue/rescue-query.json` union recall and deduplicated volume.

### 6. Medium — Ablation and reporting gaps
- **Ablation for `combine` strategies:** `without/BLOCK` lines are skipped when `combine` is used. Because the expression is monotone (and/or only), dropping a block is equivalent to setting it true and simplifying the tree, so ablation can be computed.
- **PRISMA-S mapping in `audit.md`:** Map existing `validation.json` fields to PRISMA-S items (database/platform, date searched, full strategy, limits, peer review, deduplication) (Rethlefsen et al., 2021, https://doi.org/10.1186/s13643-020-01542-z).
- **Agent-built benchmark results:** `RESULTS.md` should report blind agent-built strategies with workload (`works_per_test_hit`, `workload_ratio`) and recall intervals beside recall, and keep the 12 OpenAlex-native fixtures separate from the 29 PubMed-subset fixtures.

### 7. Done — Small fixes
- The audit's revision history is ordered by each evaluation's stored `at` (file time only breaks ties), so copying or cloning a workspace no longer reorders it.
- `check()` refuses, offline, the wildcard forms OpenAlex rejects (verified 2026-11-25): fewer than three leading characters, also inside phrases (`"te* care"`), and wildcards in a stemmed phrase. `lint` and `draft` now report these as errors rather than warnings.
- Readable errors: a missing step names the command to run (`Run eval first.`, `Attach a critique with the review command first.`), a corrupted workspace file names the file and position, and a missing field names the command and field instead of printing a bare `KeyError`.
- `resolve` accepts PMCIDs (`PMC123`, `pmcid:123`, PMC article URLs), normalised to `pmcid:PMC123`; bare digits stay PMIDs.
- RIS export adds `SN` (each ISSN), `PB` (publisher) and `KW` (OpenAlex keywords); the CSV gains `issn`, `publisher` and `keywords`. Author names stay as OpenAlex displays them: reversing them to `Last, First` would guess wrongly for many names, and reference managers parse either order.

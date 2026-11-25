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
| Bug fix (silent corpus drop in rescue) | OpenAlex accepts `(works (all corpora) where ...)` as a nested set but drops its corpus without a warning, so `rescue` on a `corpus: all` strategy drew citation links from the core corpus only. Nested sets are now written as `(get works <corpus> where ...)`. The translation guard now descends into nested sets (leaf count and corpus at every level), compares strategy words with the query object to catch a dropped term hidden by a split, and is applied to `rescue`. | `252e968` |
| Recall uncertainty | 95% Wilson score intervals on relative recall in `eval`, `audit.md` and benchmark scorecards (`relative_recall_ci95`, `unseen_recall_ci95`). | `252e968` |
| Holdout exposure | Each `report` records the validation/benchmark works its audit revealed (`exposure.json`, outside the input fingerprint). Later reports are labelled post-exposure and score holdouts added since then separately; `eval` warns when holdouts are exposed. | `252e968` |
| Export cost estimate | `export --estimate` (free): pages, credits and USD from OpenAlex's cost check where returned, otherwise the documented per-request price (1 credit filter-only, 10 with a search, whatever the page size). | `252e968` |
| Bug fix (pulled works scored as retrieved) | Since 2026-11-19 OpenAlex leaves pulled (retracted, withdrawn, removed) works out of lists and counts, but an ID filter brings them back, so `members()` scored pulled known works as retrieved although the search and export left them out. Every request now sends `include_pulled` explicitly from a new strategy/scope setting `"pulled": "exclude"|"include"`; pulled known works are misses with reason `pulled`; `eval`/`audit.md` count the pulled works the search leaves out or includes; CSV gains `is_pulled`/`is_retracted`; benchmarks report pulled gold works separately. This also explained the "round" citation-set counts (36,000 = 36,023 existing references − 23 pulled): there is no cap on citation walks. | `19a24b6` |

---

## Open bugs & improvements (prioritized)

### 1. High — `rescue` hits OpenAlex `query_too_slow` (>11s) on large Boolean strategies
- **Observed:** On `Lewowski_2021` (~180 leaves), `rescue --direction both` failed with HTTP 400 `query_too_slow` because `both` embeds the full Boolean query twice inside `set (works where ...)`, whereas `--direction cited-by` succeeded in seconds and rescued 1/1 abstract-less miss. On `Moran_2020` (238 leaves), even single-direction subquery materialization timed out.
- **Also observed (2026-11-25):** with or without an API key, rescue intermittently fails with HTTP 500 `es_error` "Elasticsearch error: Connection timed out", even on the small `examples/telehealth.json` strategy and on `cited-by` alone. Failures are erratic, not size-driven (a 47k-work set failed while a 63k-work set succeeded), and OpenAlex's own pre-check reported these queries `within_budget` (6–9 s of 10 s), so `check.estimate` does not predict them. `api.request` does not retry HTTP 500.
- **Suggested fix:**
  0. Treat HTTP 500 `es_error` timeouts as retryable with backoff, and warn when `check.estimate.seconds` is close to `budget_seconds`.
  1. Change `rescue`'s default `--direction` from `both` to `cited-by` (halving subquery cost).
  2. When `--direction both` is requested, either run `cited-by` and `citing` as two separate single-set queries and union/deduplicate their counts/hits, or automatically retry `cited-by` on `query_too_slow`.
  3. When even a single `set (works where ...)` times out on very large strategies (200+ leaves), fail with an actionable fallback suggesting `neighbors --direction references|citing` on top screened/development works (or narrowing the rescue set by year/topic).

### 2. High — Stemming & single-token hyphen quote-stripping hazards
- **Observed:** In `Moran_2020`, Porter stemming mapped `fasted`/`fasting` → `fast` and `ration` → `rational`, and OpenAlex OQL stripped quotes on single-token hyphenated phrases (`"open-field"`, `"risk-prone"`, `"anti-predator"`), inflating initial hits to 42,154 before fixing brought them to 5,969.
- **Suggested fix:**
  - Add a `lint` warning for quoted single-token hyphenated strings (e.g., `"risk-prone"` → suggest space-separated `"risk prone"` or checking canonical OQL).
  - Document high-risk Porter-stemming collisions in `SKILL.md` step 5 (advise quoting ambiguous stems like `"fasting"`, `"ration"` or inspecting per-term counts when a block unexpectedly explodes).

### 3. Medium — Stronger vocabulary & discovery tools
- **Enrichment-ranked `terms`:** Rank mined terms by frequency in development records vs. a background sample rather than raw document frequency (the objective approach of Hausner et al., 2012, https://doi.org/10.1186/2046-4053-1-19). Worth it mainly at `thorough` depth: with two or three seeds neither raw counts nor lift say much.
- **Multi-seed `neighbors`:** Allow passing multiple work IDs to `neighbors` in one deduplicated snowballing batch.
- **Post-export abstract enrichment (for screening):** Optional enrichment of missing abstracts in `results.csv` / `results.ris` via PubMed / Europe PMC / Crossref by DOI/PMID, with provenance logged in `export-manifest.json`.

### 4. Medium — Stricter critique & workspace ergonomics
- **Multi-round critique continuity:** Numbered findings across critique rounds with required dispositions and a `report --diagnostic` dry-run mode.
- **`doctor` command & response caching:** Quick check of `OPENALEX_API_KEY`, remaining credits, and API connectivity; optional per-workspace cache for repeated read-only calls.

### 5. Medium — Benchmark expansion & rigor
- Run blind builds across the remaining `openalex-native` fixtures (`Walker_2018`, `Kapuka_2021`, `Filges_2022`, `Noetel_2021`, `Verdugo-Castro_2022`, `Cinquin_2018`, `Ruggeri_2019`) and the 10 `frozen` held-out fixtures (with strict filesystem isolation and 2–3 repeats per condition to measure variance).
- Add a `bench.py score --with-rescue` option to automatically score `final-query.json` + `rescue/rescue-query.json` union recall and deduplicated volume.

### 6. Medium — Ablation and reporting gaps
- **Ablation for `combine` strategies:** `without/BLOCK` lines are skipped when `combine` is used. Because the expression is monotone (and/or only), dropping a block is equivalent to setting it true and simplifying the tree, so ablation can be computed.
- **PRISMA-S mapping in `audit.md`:** Map existing `validation.json` fields to PRISMA-S items (database/platform, date searched, full strategy, limits, peer review, deduplication) (Rethlefsen et al., 2021, https://doi.org/10.1186/s13643-020-01542-z).
- **Agent-built benchmark results:** `RESULTS.md` should report blind agent-built strategies with workload (`works_per_test_hit`, `workload_ratio`) and recall intervals beside recall, and keep the 12 OpenAlex-native fixtures separate from the 29 PubMed-subset fixtures.

### 7. Low — Small fixes
- Sort the audit's revision history by each evaluation's stored `at`, not file modification time (`evaluate.py`), which copying or cloning a workspace resets.
- Make `check()` refuse wildcards with fewer than three leading characters, as `lint` warns and OpenAlex rejects (today only offline `draft` output is affected).
- Replace bare `KeyError` messages from the CLI (for example `"'hits'"`) with readable errors.
- Accept PMCIDs in `resolve` (OpenAlex supports them).
- RIS export: add `SN` (ISSN), `PB` (publisher) and `KW` (keywords), and consider `Last, First` author names.

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

---

## Open bugs & improvements (prioritized)

### 1. High — `rescue` hits OpenAlex `query_too_slow` (>11s) on large Boolean strategies
- **Observed:** On `Lewowski_2021` (~180 leaves), `rescue --direction both` failed with HTTP 400 `query_too_slow` because `both` embeds the full Boolean query twice inside `set (works where ...)`, whereas `--direction cited-by` succeeded in seconds and rescued 1/1 abstract-less miss. On `Moran_2020` (238 leaves), even single-direction subquery materialization timed out.
- **Suggested fix:**
  1. Change `rescue`'s default `--direction` from `both` to `cited-by` (halving subquery cost).
  2. When `--direction both` is requested, either run `cited-by` and `citing` as two separate single-set queries and union/deduplicate their counts/hits, or automatically retry `cited-by` on `query_too_slow`.
  3. When even a single `set (works where ...)` times out on very large strategies (200+ leaves), fail with an actionable fallback suggesting `neighbors --direction references|citing` on top screened/development works (or narrowing the rescue set by year/topic).

### 2. High — Stemming & single-token hyphen quote-stripping hazards
- **Observed:** In `Moran_2020`, Porter stemming mapped `fasted`/`fasting` → `fast` and `ration` → `rational`, and OpenAlex OQL stripped quotes on single-token hyphenated phrases (`"open-field"`, `"risk-prone"`, `"anti-predator"`), inflating initial hits to 42,154 before fixing brought them to 5,969.
- **Suggested fix:**
  - Add a `lint` warning for quoted single-token hyphenated strings (e.g., `"risk-prone"` → suggest space-separated `"risk prone"` or checking canonical OQL).
  - Document high-risk Porter-stemming collisions in `SKILL.md` step 5 (advise quoting ambiguous stems like `"fasting"`, `"ration"` or inspecting per-term counts when a block unexpectedly explodes).

### 3. Medium — Stronger vocabulary & discovery tools
- **Enrichment-ranked `terms`:** Rank mined terms by frequency in development records vs. a background sample rather than raw document frequency.
- **Multi-seed `neighbors`:** Allow passing multiple work IDs to `neighbors` in one deduplicated snowballing batch.
- **Post-export abstract enrichment (for screening):** Optional enrichment of missing abstracts in `results.csv` / `results.ris` via PubMed / Europe PMC / Crossref by DOI/PMID, with provenance logged in `export-manifest.json`.

### 4. Medium — Stricter critique & workspace ergonomics
- **Multi-round critique continuity:** Numbered findings across critique rounds with required dispositions and a `report --diagnostic` dry-run mode.
- **`doctor` command & response caching:** Quick check of `OPENALEX_API_KEY`, remaining credits, and API connectivity; optional per-workspace cache for repeated read-only calls.

### 5. Medium — Benchmark expansion & rigor
- Run blind builds across the remaining `openalex-native` fixtures (`Walker_2018`, `Kapuka_2021`, `Filges_2022`, `Noetel_2021`, `Verdugo-Castro_2022`, `Cinquin_2018`, `Ruggeri_2019`) and the 10 `frozen` held-out fixtures (with strict filesystem isolation and 2–3 repeats per condition to measure variance).
- Add a `bench.py score --with-rescue` option to automatically score `final-query.json` + `rescue/rescue-query.json` union recall and deduplicated volume.

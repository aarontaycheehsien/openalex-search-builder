# CLI Reference, Strategy Schema & Walkthrough

Every command is invoked through `scripts/oasb.py` with a workspace path (`-w PATH`) before the subcommand (`doctor` also runs standalone without `-w`):

```sh
python scripts/oasb.py -w runs/my-review <subcommand> [options]
python scripts/oasb.py doctor
```

Python 3.10+ is required, with no third-party dependencies. When `OPENALEX_API_KEY` is not set in the shell environment, the CLI automatically checks `.env` in the workspace, current directory, or repository root.

---

## 1. Command Reference (by Workflow Phase)

### Phase A — Intake & Scope Lock

| Command | Purpose |
|---|---|
| `doctor [--offline]` | Check Python version, `OPENALEX_API_KEY` configuration (`environment` or `.env`, without printing key material), `/query` connectivity, `/works` credit/budget status (or `429` reset window), and optional `-w` workspace state. |
| `init --question "..." [--eligibility "..."] [--depth quick\|standard\|thorough]` | Create a workspace and save `protocol.json`. Repeat `--eligibility` for multiple criteria. |
| `scope` | Print the current concept blocks, roles, limits, corpus, pulled-work setting, and whether scope is confirmed. |
| `scope --confirm --note "..."` | Record reviewer approval of the current scope. Required before `eval`. Re-confirm only if blocks, roles, protocol, limits, corpus, or `pulled` change (rationale edits do not require re-confirmation). |

### Phase B — Known Works & Dual-Route Discovery

| Command | Purpose |
|---|---|
| `resolve ID ... --role seed\|relevant\|validation\|benchmark --source "..."` | Resolve OpenAlex Work IDs, DOIs, PMIDs, or PMCIDs into `records.json`. `validation` and `benchmark` stay hidden from development output and term mining. |
| `develop KEY --reason "..."` | Move a protected (`validation`/`benchmark`) work into `relevant` if it informed strategy development. |
| `sample --oql "get works where ..." [--size 10] [--seed 42] [--top] [--prior-reviews] [--any-date]` | Draw a reproducible random sample, top relevance-ordered matches (`--top`), or pre-cutoff review works with matched-reference counts (`--prior-reviews`). |
| `sample --from-strategy [--size 20] [--seed 42] [--top] [--prior-reviews]` | Sample the active `strategy.json` randomly (for precision), in relevance order (`--top`), or restricted to reviews (`--prior-reviews` for wording-independent reference snowballing). |
| `sample --added [--size 10] [--seed 42]` | Sample *only* records newly added by the latest evaluated revision (to judge whether a broadening earned its records). |
| `sample --removed [--size 10] [--seed 42]` | Sample *only* records dropped by the latest evaluated revision (to verify that narrowing or tightening a noisy term kept recall safe). |
| `neighbors WORK_ID [WORK_ID ...] --direction citing\|references\|related [--size 10] [--all] [--cursor C] [--max-pages N] [--any-date]` | Follow citation neighbors (or up to 10 text-similarity `related` works) from one or more development works. Multi-seed calls deduplicate and rank co-linked candidates first. |
| `screen BATCH WORK_ID include\|exclude\|unclear --reason "..." [--role relevant\|validation]` | Record an eligibility decision for a discovered candidate. `include` adds to `relevant` (or protected local `validation`); `exclude` feeds irrelevant-set specificity tracking in `eval`. |

### Phase C — Vocabulary & Trap Checks

| Command | Purpose |
|---|---|
| `terms` | Mine recurring words, bigrams, and OpenAlex keywords from development records (`seed` + `relevant` only). |
| `vocab keywords\|topics "query" [--size 10]` | Look up OpenAlex keyword `alternatives` or topic keyword lists live from the API. |
| `term-counts [--block ID]` | Count each term individually (~10 credits per count) to flag stemming collisions (`stemming_expands`) and quoted hyphenated terms running as `AND` (`hyphen_not_phrase`). |

### Phase D — Validation, Evaluation & Ablation

| Command | Purpose |
|---|---|
| `validate` | Compile `strategy.json`, validate OQL with OpenAlex `/query`, run `check_translation()`, and return canonical OQL + `strategy_warnings`. |
| `eval [--quick] [--abstract-stats] [--term-impact] [--no-cache]` | Execute the strategy (reusing unchanged lines from `query-cache.json` unless `--no-cache`), run per-block / exclusion / limit counts, leave-one-out ablation (`without/ID`, `drop-route/ID`, `with/ID`), miss diagnostics (`miss_details`), independence tiers, delivery budget estimate, revision cost, and exact-search pilot precision. Add `--term-impact` to measure marginal volume and hits per term; add `--abstract-stats` to measure missing-abstract attrition. |

### Phase E — Critique & Audited Delivery

| Command | Purpose |
|---|---|
| `review critique.json` | Attach a 6-domain PRESS-structured critique against the latest full `eval`. Interpolates `{placeholders}` from live evidence and rejects stale counts from earlier revisions. |
| `report` | Re-execute the query live, verify the critique's evidence fingerprint still matches, and deliver `reports/<evaluation-id>/` (`final-query.oql`, `final-query.json`, `audit.md`, `prisma-s.json`, `validation.json`). Records holdout exposure in `exposure.json`. |

### Phase F — Post-Delivery No-Abstract Rescue & Export

| Command | Purpose |
|---|---|
| `rescue --estimate [--direction cited-by\|citing\|both] [--method references\|walk] [--min-links N] [--keywords-gate] [--topic T...]` | Estimate credits for post-delivery no-abstract citation rescue without running it. |
| `rescue [--report ID] [--direction cited-by\|citing\|both] [--method references\|walk] [--min-links N] [--keywords-gate] [--topic T...]` | Recover abstract-less works linked by citation to the delivered set. Default `--direction cited-by --method references` extracts matched reference lists without running a timeout-prone nested citation query, reports co-citation counts (`--min-links N` filters by ≥N citing delivered works; `--keywords-gate` also matches OpenAlex keywords), and saves `unlinked-references.json`. |
| `export --estimate [--report ID] [--rescue]` | Estimate pages, credits, and USD cost without downloading (free). |
| `export [--report ID] [--max-pages N] [--rescue]` | Cursor-paged, SQLite-checkpointed, Work-ID-deduplicated export into `exports/<report-id>/` (`results.csv`, `results.ris`, `results.jsonl`, `export-manifest.json`). Resumable on interruption; incomplete runs write `results.partial.*`. |

### Phase G — Separate-Context Blinded Holdout Validation

Run in a **separate reviewer workspace outside the development agent's filesystem/context**:

| Command | Purpose |
|---|---|
| `holdout-split --development-workspace PATH [--seed 42]` | Deterministically split eligible citation-discovered works ~50/50, sealing the holdout set privately and outputting only development IDs + holdout count. |
| `holdout-score --report PATH` | One-time post-delivery scoring of sealed holdouts against a delivered report, excluding works independently rediscovered by the builder. |

See [SKILL.md — Separate-context holdout validation](../SKILL.md#separate-context-holdout-validation) for full isolation requirements.

### Phase H — Offline Draft Mode (No API Key or Network)

| Command | Purpose |
|---|---|
| `lint` | Offline syntax check, local OQL compilation, and heuristic warnings (invalid wildcards, hyphenated phrases, duplicates, missing rationales, ≥4 ANDed blocks, exclusions). |
| `draft` | Compile and save clearly labelled untested artifacts under `draft/` (`draft-query.oql`, `draft-notes.md`, `draft-manifest.json`). |

---

## 2. `strategy.json` Schema Reference

Start from [`examples/telehealth.json`](../examples/telehealth.json) (minimal) or [`examples/telehealth-advanced.json`](../examples/telehealth-advanced.json) (every option):

```json
{
  "corpus": "core",
  "pulled": "exclude",
  "combine": "(delivery or apps) and setting",
  "blocks": [
    {
      "id": "delivery",
      "role": "search",
      "field": "title-abstract",
      "terms": [
        "telemedicine",
        "\"remote consultation\"",
        "stemmed \"video visit\"",
        {"near": ["video", "consultation"], "within": 3}
      ],
      "rationale": "Remote clinical care delivered at a distance."
    },
    {
      "id": "apps",
      "role": "search",
      "field": "title-abstract-keywords",
      "terms": ["mhealth", "\"mobile health\""],
      "rationale": "Alternative entry route via mobile health."
    },
    {
      "id": "setting",
      "role": "search",
      "field": "title-abstract",
      "terms": ["rural", "remote", {"near": ["underserved", "communit*"], "within": 2, "exact": true}],
      "rationale": "Rural or remote populations."
    },
    {
      "id": "veterinary",
      "role": "exclude",
      "field": "title",
      "terms": ["veterinary"],
      "rationale": "Animal health is out of scope."
    }
  ],
  "limits": {
    "from_date": "2015-01-01",
    "to_date": "2025-12-31",
    "language": ["en"],
    "type": ["article", "review"]
  }
}
```

### Top-Level Fields
- **`corpus`**: `"core"` (default), `"all"` (core + expansion corpus), or `"expansion"`. Scope-locked.
- **`pulled`**: `"exclude"` (default; OpenAlex's standard view since 2026-11-19) or `"include"`. Scope-locked.
- **`combine`** *(optional)*: Monotone AND/OR expression over every `role: "search"` block ID (e.g., `"(a and b) or fallback_no_abs"`). `NOT` is not allowed here — use `role: "exclude"` blocks.
- **`blocks`**: List of concept block objects.
- **`limits`**: Optional dictionary supporting `from_date`, `to_date` (ISO `YYYY-MM-DD`), `language` (list of codes), `type` (list of OpenAlex work types), `topic` (primary topic only: `topic is T...`), `any_topic` (any of a work's 3 assigned topics: `topics is T...`), and `has_abstract` (`true` or `false`).

### Block Properties
- **`id`**: Unique alphanumeric/hyphen/underscore identifier (cannot be `and`, `or`, `not`).
- **`role`**:
  - `search` — Included in the final query (ANDed by default, or governed by `combine`).
  - `optional` — Tested individually alongside the core (`with/<id>`) without entering the final query.
  - `screen` — Documented in scope and audit, not searched (e.g., outcomes or study design deferred to screening).
  - `exclude` — Always ANDed as `not <field> has (...)`; `eval` tracks known-work losses in `exclude/<id>` and recovery in `without/<id>`.
- **`field`**: `title`, `abstract`, `title-abstract` (default), `title-abstract-keywords`, or `fulltext` (searches title, abstract, and full text where available).
- **`terms`**: List of terms ORed inside the block:
  - Single word (stemmed): `"telemedicine"`
  - Wildcard (≥3 leading chars, exact text): `"communit*"`
  - Exact phrase: `"\"remote care\""`
  - Stemmed phrase (no wildcards): `"stemmed \"remote care\""`
  - Proximity object: `{"near": ["video", "consultation"], "within": 3}` (`within` 1–50; single stemmed words unless `"exact": true`, which permits phrases and wildcards).
- **`has_abstract`** *(optional, boolean)*, **`topics`** *(primary topic IDs)*, and **`any_topics`** *(any assigned topic IDs)*: Allowed on non-exclude blocks, enabling conditional no-abstract fallback branches via `combine`.
- **`rationale`**: Human-readable justification for peer review.

---

## 3. `critique.json` Schema & Placeholders

Start from [`examples/critique-template.json`](../examples/critique-template.json). Every critique must include non-empty strings for six PRESS domains plus `"decision": "ready"`:

- `scope`
- `vocabulary`
- `boolean_and_fields`
- `limits`
- `retrieval`
- `limitations`
- `decision` (`"ready"` after resolving required changes)

Use brace placeholders instead of typing counts by hand — `review` substitutes them from the current full evaluation and refuses unknown placeholders or stale counts from earlier evaluations:

| Placeholder | Filled with |
|---|---|
| `{evaluation_id}` | Current evaluation snapshot ID |
| `{evaluated_at}` | UTC timestamp of the evaluation |
| `{final_count}` | Formatted final work count (e.g., `4,280`) |
| `{pulled_count}` | Pulled works excluded from (or included in) the final count |
| `{development_retrieved}` / `{development_resolved}` / `{development_missed}` | Counts across `seed` + `relevant` |
| `{development_recall}` | Relative recall with 95% Wilson CI across `seed` + `relevant` |
| `{seed_retrieved}` / `{seed_resolved}` / `{seed_recall}` | Same for `seed` set only |
| `{relevant_retrieved}` / `{relevant_resolved}` / `{relevant_recall}` | Same for `relevant` set only |
| `{without_abstract_pct}` | Final `% without abstract` *(or `"not measured (eval ran without --abstract-stats)"` when `--abstract-stats` was omitted)* |

*(Holdout `validation` and `benchmark` figures are intentionally never offered to `review`.)*

---

## 4. Annotated Session Walkthrough

Here is what an interactive session looks like when you open this repository in Claude Code or Codex:

1. **You state your question and optional seeds:**
   > Read SKILL.md. Develop an OpenAlex search strategy for a scoping review of telemedicine delivery in rural communities (2015–2025). Treat W2618851878 as a seed paper. Use standard depth and discuss the concept blocks with me.

2. **Checkpoint 1 — Scope approval:**
   The agent runs `init`, proposes a short concept table (e.g., `delivery` = search, `setting` = search, `outcomes` = screen; `corpus: core`; `pulled: exclude`), and pauses for your confirmation. Once you approve, it writes `strategy.json` and runs `scope --confirm --note "..."`.

3. **Discovery & vocabulary building:**
   The agent resolves your seed (`resolve`), follows its citations (`neighbors --direction citing` / `references`) and pilot samples (`sample`) within your date window, screens candidates against your eligibility criteria (`screen`), mines terms (`terms`, `vocab`), and checks for stemming/hyphenation traps (`term-counts`).

4. **Iterative testing & miss diagnosis:**
   The agent runs `validate` and `eval --abstract-stats`. Suppose an initial query returns 2,840 works and misses a known study:
   - `miss_details` shows *which* block failed (`block/delivery`) and whether the missed study has an abstract in OpenAlex.
   - Adding `"video consultation"` and `telehealth` recovers the paper; `eval` shows the exact revision delta (+1,440 works, +1 development hit) and leave-one-out ablation (`without/delivery`, `without/setting`).
   - If a broadening grows the query by >20% and >500 works with zero new development hits, `eval` flags `broadening_without_gain` and the agent runs `sample --added` to verify whether the new records contain eligible studies before keeping the change.

5. **Internal critique & delivery:**
   The agent writes a 6-domain PRESS critique using `{final_count}` and `{development_recall}`, attaches it via `review critique.json`, and runs `report`. The CLI re-executes the query live against OpenAlex and writes `reports/<id>/` — see [`examples/sample-report/audit.md`](../examples/sample-report/audit.md) for the exact output format.

6. **Checkpoint 2 — Optional no-abstract rescue & export:**
   Because multi-block `AND` queries drop abstract-less records, the agent reports the missing-abstract attrition rate and offers `rescue --estimate` followed by `rescue`. When you are ready to screen records in Zotero, EndNote, Rayyan, or Covidence, it runs `export --estimate` (free) and `export` (plus `export --rescue` if rescued) to generate deduplicated `results.csv`, `results.ris`, and `results.jsonl`.

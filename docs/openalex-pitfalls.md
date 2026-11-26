# OpenAlex Search Pitfalls (and How the CLI Guards Against Them)

Prompting an LLM to write an OpenAlex Boolean search in one shot fails in predictable, often invisible ways. Most of these pitfalls were observed live against the OpenAlex OQL/OQO API during development and blind benchmark runs (see [`ROADMAP.md`](../ROADMAP.md) and [`evals/RESULTS.md`](../evals/RESULTS.md)).

This guide documents each pitfall with concrete evidence and explains what `scripts/oasb.py` checks automatically.

---

## 1. Silent OQL Translation Drops

**The trap:** OpenAlex's `/query` translator can return `valid: true` while silently dropping a clause, a term, or a corpus selector.
- *Observed 2026-11-24:* The translator dropped `and is_xpac is false` without raising a validation error.
- *Observed 2026-11-25:* OpenAlex accepted `(works (all corpora) where ...)` inside a nested citation set without error, but silently dropped `(all corpora)` — causing a `corpus: all` rescue query to walk citations in the core corpus only.

**How the CLI guards against it:**
- Nested citation sets are compiled as full `(get works <corpus> where ...)` expressions.
- Every `validate`, `eval`, `report`, and `rescue` call runs `check_translation()` on the returned OpenAlex Query Object (OQO): it recursively verifies the corpus at every level (including inside nested sets), confirms the exact leaf count of terms and limit values, and checks that every strategy word survives into the OQO (catching a dropped term hidden by a split elsewhere). If anything is lost, the run fails rather than silently delivering a weaker query.

---

## 2. Quoted Hyphenated Terms Run as `AND`, Not Phrases

**The trap:** In OpenAlex, wrapping a single hyphenated token in quotes (such as `"open-field"` or `"risk-prone"`) keeps it on the exact field, **but executes it as an `AND` of its parts anywhere in the field**, not as an adjacent phrase.
- *Verified live 2026-11-25:*
  - `"open-field"` matched **10,487 titles** — identical to `"open" AND "field"` (**10,487**).
  - The spaced phrase `"open field"` matched **4,872 titles** and also matches the hyphenated spelling.
  - `"risk-prone"` matched **958 titles** vs. **131** for `"risk prone"`.
  - (`stemmed "risk-prone"` already executes as a phrase: 158 = `stemmed "risk prone"`, and tokens like `"COVID-19"` were unaffected: 968,014 vs. 967,735.)
- In the `Moran_2020` benchmark, stemming and quoted hyphenated terms (`"open-field"`, `"risk-prone"`, `"anti-predator"`) inflated initial retrieval from 5,969 to **42,154 works**.

**How the CLI guards against it:**
- Offline `lint` emits `hyphenated_phrase` and `unquoted_hyphen` warnings suggesting the spaced phrase (also surfaced as `strategy_warnings` in `validate` and `eval`).
- Live `term-counts [--block ID]` counts every quoted hyphenated term alongside its spaced phrase and flags `hyphen_not_phrase` when the hyphenated form matches ≥ 1.5× the spaced phrase and ≥ 50 more works.

---

## 3. Stemming Collisions on Bare Words

**The trap:** Bare unquoted words in OpenAlex are stemmed. Occasionally a stem collides with a much more common unrelated word.
- *Verified live 2026-11-25:* Bare `fasting` stems to `fast`, matching **471,555 titles** stemmed vs. **33,935** for exact `"fasting"`. (By contrast, `ration` did not collide with `rational`: 16,830 stemmed vs. 113,619 for `rational` alone.)

**How the CLI guards against it:**
- `term-counts [--block ID]` counts each bare word both stemmed and exact (`"word"`) under the strategy's corpus, limits, and pulled view, flagging `stemming_expands` whenever the stemmed count is ≥ 3× the exact count and adds ≥ 100 works.

---

## 4. Invalid Wildcards and Unsupported Proximity Syntax

**The trap:** LLMs routinely emit PubMed/Scopus-style wildcards (`a*`, `"te* care"`, `stemmed "tele*"`) or Lucene slop (`"video consultation"~3`) that OpenAlex rejects.
- OpenAlex requires at least **three leading characters** before `*` (including inside phrases) and rejects wildcards inside `stemmed "..."` phrases because wildcards run on exact text.

**How the CLI guards against it:**
- Offline schema validation (`check` / `lint` / `draft`) rejects short wildcards, wildcards inside stemmed phrases, and `"a b"~N` slop before any API request is made.
- Native OpenAlex proximity is written cleanly in JSON as `{"near": ["video", "consultation"], "within": 3}` (compiling to `(video and consultation within 3 words of each other)`), with `"exact": true` for phrases or wildcards.

---

## 5. Pulled Works (Retracted, Withdrawn, Removed) Disappear from Counts but Reappear on ID Lookups

**The trap:** Since **2026-11-19**, OpenAlex excludes pulled (retracted, withdrawn, removed) works from lists, searches, and counts by default — **but filtering by Work ID brings them back**. A naive ID-intersection recall check will score a pulled known paper as "retrieved" even though the actual search and export leave it out.

**How the CLI guards against it:**
- `"pulled": "exclude"` (default) or `"include"` is an explicit scope decision in `strategy.json`.
- Every search, known-work check, rescue, and export passes `include_pulled=false|true` explicitly so recall checks see exactly what the search sees.
- Known pulled works under `exclude` are reported as misses with reason `pulled` (not silently counted as hits, and not misdiagnosed as vocabulary gaps).
- `eval` and `audit.md` report the exact number of pulled works excluded from (or included in) the final result set, and CSV exports include `is_pulled` and `is_retracted` columns.

---

## 6. Missing-Abstract Attrition in Multi-Block Boolean Queries

**The trap:** Many works in OpenAlex — especially older articles, conference papers, reports, and non-biomedical literature — have **no abstract indexed**. When you `AND` two or three `title-abstract` concept blocks, an abstract-less work can only match on its short title. If any concept is absent from the title, the work is silently dropped.
- *Observed in blind benchmark reruns (2026-11-26):* Out of **31** missed gold works across five reviews, **20 lacked abstracts** in OpenAlex.
- *Observed on `Langham-Putrow_2021_OACA` and `Lewowski_2021`:* Post-delivery citation rescue recovered **6/6** missing-abstract misses (raising recall from 100/114 = 87.7% to **106/114 = 93.0%**) and **1/1** missing-abstract miss (33/40 = 82.5% to **34/40 = 85.0%**), respectively.

**How the CLI guards against it:**
- `eval --abstract-stats` measures `% without abstract` for `final` and each search block, flagging attrition when `final` has a lower share than individual blocks.
- `miss_details` explicitly flags missed development works that have no abstract in OpenAlex so the agent does not over-broaden vocabulary trying to rescue an indexing gap.
- `strategy.json` supports `has_abstract` and `topics` at both global and block level, enabling low-volume conditional fallback branches via `combine` (e.g., `(a and b) or fallback_no_abs`).
- Post-delivery `rescue` finds abstract-less works cited by the delivered set using the delivered works' matched reference lists (`--method references`, avoiding OpenAlex citation-set query timeouts that occurred on large strategies) gated by any-block text or `--topic`, and saves unlinked printed reference strings to `unlinked-references.json` as leads for other sources.

---

## 7. Incomplete Citation Links and `related` Works Limits

**The trap:** OpenAlex citation snowballing is not exhaustive, and OpenAlex's `related_works` endpoint is **not** citation searching:
- About **82%** of references to works in OpenAlex are linked (`referenced_works`); unmatched references are dropped and many older works, reports, and theses have no reference list. OpenAlex also changed its link graph on **25 November 2026** (stopping inheritance of Microsoft Academic Graph links when a work's own reference list is held).
- `related_works` returns at most **10** nearest neighbors by text embeddings and **never includes reviews or retracted works**.

**How the CLI guards against it:**
- `neighbors` inspects each seed's `referenced_works_count` and `cited_by_count` first (free lookup) and refuses dead-end seeds before spending paid requests.
- `neighbors` labels `--direction related` as `text-similarity` (not a citation route), caps it at 10, and records link-coverage caveats automatically in the PRISMA-S item 5 draft.
- `neighbors` and `sample` automatically enforce the strategy's publication-date window by default (`--any-date` to override), preventing post-cutoff reviews from leaking into candidate pools.

---

## 8. Broadening Inflation and Abstract Boilerplate Collisions

**The trap:** Adding broad synonyms to chase recall can explode screening volume for near-zero gain, and OpenAlex abstracts sometimes contain publisher/repository boilerplate (e.g., CC-BY license footers like *"open access article ... properly cited"* or *"Preferred citation:"*) that collide with generic single words.
- *Observed in blind benchmark reruns (2026-11-26):* Broader searches across five reviews produced **36% more records** (75,066 → 102,246) for only **3 additional gold works** out of 135.

**How the CLI guards against it:**
- Every `eval` after the first computes `revision` (count delta, development works gained/lost, works per gained development work) and raises `broadening_without_gain` if the query grows by >20% and >500 works without gaining a development work.
- `sample --added` samples *only* the records added by the latest revision so the reviewer/agent can screen whether the extra volume actually contains eligible studies.
- `sample --from-strategy` + `screen` computes strict and lenient precision estimates with 95% Wilson intervals directly on the exact current query.

---

## 9. The Development-Recall Illusion, Discovery Reach, and Stale Numbers

**The trap:**
- **Seedless discovery reach limits:** In blind seedless runs (2026-11-26), agents achieved **100%** recall on their own development sets and **100%** on protected validation sets in 4 of 5 reviews — while actual gold recall was **59–93%**. Follow-up calibration showed that **agent screening itself was sound** (agents included **36 of 40** gold works they screened, marked 4 unclear, and excluded 0); the gap was **discovery reach** (seedless discovery surfaced only **0–14** of 26–28 gold works because included studies make up 0.1–1.6% of search results and in 4 of 5 reviews only 6–9 gold works cited another gold work).
- **Small-sample overconfidence:** Retrieving 3 of 3 known works has a 95% Wilson interval down to **44%**.
- **Stale narratives:** In three benchmark reruns, agent-written critiques accidentally quoted hit counts from earlier revisions instead of the final search.

**How the CLI guards against it:**
- Supports `sample --from-strategy --top` to screen best-matching records first in OpenAlex relevance order (which surfaced ~4–6× more gold works in the top 200 than random samples), while emphasizing reviewer-supplied **seeds** as the structural remedy for seedless blind spots.
- Reports `seed`, `relevant`, `validation`, and `benchmark` sets separately, always with **95% Wilson score intervals**.
- Tracks holdout exposure in `exposure.json` and labels subsequent deliveries **post-exposure** if validation/benchmark results were already shown.
- Supports true **separate-context blinded validation** (`holdout-split` / `holdout-score`) in an isolated reviewer workspace.
- Keeps Chapman (1951) / Seber (1970) capture–recapture as a diagnostic outside `audit.md` (reported only when ≥20 eligible works come from each route and ≥10 from both, after benchmark analysis showed weak correlation with gold recall at small overlaps).
- `review` fills live `{placeholder}` values (`{final_count}`, `{development_recall}`, etc.) from the current full evaluation, rejects critiques citing earlier revision counts, and `report` re-executes the query live against OpenAlex to verify the evidence fingerprint before writing the final audit.

# OpenAlex search audit

How is telemedicine delivered in rural and remote communities?

Executed 2026-11-26T07:07:50+00:00 via the OpenAlex OQL API. Works retrieved: 4280.
Corpus: core. Block ablation: assessed.
Pulled works: excluded (request parameter include_pulled=false, OpenAlex's default view since 2026-11-19). 12 retracted, withdrawn or removed works matching this search are left out of the count and the export.

## Search as executed

```text
get works where title-abstract has (telemedicine OR telehealth OR "video consultation") and title-abstract has (rural OR remote) and publication_date >= 2015-01-01 and publication_date <= 2025-12-31
```

## Scope and eligibility

{
  "protocol": {
    "question": "How is telemedicine delivered in rural and remote communities?",
    "eligibility": [
      "Empirical study of remote clinical care delivery",
      "Rural, remote, or underserved geographic setting",
      "Published 2015-01-01 to 2025-12-31"
    ],
    "depth": "standard"
  },
  "concepts": [
    {
      "id": "delivery",
      "role": "search",
      "rationale": "Remote healthcare delivery via telecommunications."
    },
    {
      "id": "setting",
      "role": "search",
      "rationale": "Rural or remote community setting."
    },
    {
      "id": "outcomes",
      "role": "screen",
      "rationale": "Access, utilization, and clinical outcomes assessed during screening to protect recall."
    }
  ],
  "limits": {
    "from_date": "2015-01-01",
    "to_date": "2025-12-31"
  },
  "corpus": "core",
  "pulled": "exclude"
}

## Counts and known records

| Line | Works | Known works retrieved |
|---|---:|---:|
| Final | 4280 | 5 |
| block/delivery | 64500 | 5 |
| block/setting | 412000 | 6 |
| limit/from_date | 28500000 | 6 |
| limit/to_date | 28500000 | 6 |
| without/delivery | 412000 | 6 |
| without/setting | 64500 | 5 |

## Missing-abstract attrition

| Line | Without abstract | Total | % without abstract |
|---|---:|---:|---:|
| Final | 390 | 4280 | 9.1% |
| block/delivery | 10320 | 64500 | 16.0% |
| block/setting | 74160 | 412000 | 18.0% |

Final query has 9.1% works without abstracts vs 16.0%-18.0% in individual blocks; ANDing text blocks is disproportionately dropping abstract-less records. Consider `rescue` or a no-abstract fallback branch.

| Set | Eligible | Resolved | Retrieved | Relative recall (95% CI) |
|---|---:|---:|---:|---|
| seed | 2 | 2 | 2 | 100% (95% CI 34–100%) |
| relevant | 3 | 3 | 2 | 67% (95% CI 21–94%) |
| validation | 1 | 1 | 1 | 100% (95% CI 21–100%) |
| benchmark | 0 | 0 | 0 | no estimate |

Coverage uses all eligible records. Relative recall uses resolved records within each set.
A set with zero resolved works supplies no recall estimate. Intervals are 95% Wilson score
intervals; with only a few known works they are wide, and they describe the known works only.

## Holdout exposure

No validation or benchmark result had been shown in an earlier delivery.


## Holdout selection and blinding

Local validation and benchmark roles are protected from intermediate CLI output and mining, not from an agent with filesystem access. A local holdout is not necessarily blinded.
For separate-context validation use holdout-split and holdout-score in an isolated reviewer workspace.

## Precision

From screened random samples of exactly this search (`sample --from-strategy`). 2 of 4 screened records eligible: 50% (95% CI 15–85%), about 2,140 eligible records (range 642–3,638). Counting 0 unclear as eligible: 50% (15–85%).

## Missed works

- W2900000304 — block/delivery

## Search lines as executed

### block/delivery

```text
get works where title-abstract has (telemedicine OR telehealth OR "video consultation")
```

### block/setting

```text
get works where title-abstract has (rural OR remote)
```

### limit/from_date

```text
get works where publication_date >= 2015-01-01
```

### limit/to_date

```text
get works where publication_date <= 2025-12-31
```

### without/delivery

```text
get works where title-abstract has (rural OR remote) and publication_date >= 2015-01-01 and publication_date <= 2025-12-31
```

### without/setting

```text
get works where title-abstract has (telemedicine OR telehealth OR "video consultation") and publication_date >= 2015-01-01 and publication_date <= 2025-12-31
```

## Revision history

- 2026-11-26T07:07:50+00:00 — 2840 works. Known works gained: none. Lost: none.
- 2026-11-26T07:07:50+00:00 — 4280 works. Known works gained: W2900000102, W2900000405. Lost: none.
- 2026-11-26T07:07:50+00:00 — 4280 works. Known works gained: W2900000203, W2900000506. Lost: none.
- 2026-11-26T07:07:50+00:00 — 4280 works. Known works gained: none. Lost: none.

## PRESS-structured internal critique

- **scope** — Two search blocks (delivery, setting) match the approved review question. Outcomes stay at screening to preserve sensitivity. Core corpus and pulled-work exclusion (12 pulled works excluded) follow the confirmed scope.
- **vocabulary** — Adding telehealth and "video consultation" recovered seed W2900000102. Remaining miss W2900000304 lacks an abstract in OpenAlex and uses 'teleconsultation' in the title; flagged for optional post-delivery no-abstract citation rescue.
- **boolean_and_fields** — Both blocks search title-abstract with ORed synonyms and AND across concepts. Leave-one-out ablation confirms both blocks are necessary to keep volume manageable.
- **limits** — Date range 2015-01-01 to 2025-12-31 matches the protocol. No language or work-type exclusions applied.
- **retrieval** — Final search retrieves 4,280 works and 80% (95% CI 38–96%) on development works (1 missed). Pilot screening shows 2/4 exact-search records eligible.
- **limitations** — Single-database OpenAlex search; development recall is not absolute recall and live counts drift over time. Final set has 9.1% works without abstracts (lower than individual blocks), indicating missing-abstract attrition; recommend optional citation rescue and human librarian peer review.

Figures in braces in the critique template were filled in by `review` from evaluation c146ff07a1c7474c87538d25e275c11e: `{development_missed}` = 1; `{development_recall}` = 80% (95% CI 38–96%); `{final_count}` = 4,280; `{pulled_count}` = 12; `{without_abstract_pct}` = 9.1%.

## PRISMA-S search reporting checklist (draft)

Draft wording for the 16 PRISMA-S items (Rethlefsen et al. (2021), https://doi.org/10.1186/s13643-020-01542-z). Items marked for the reviewer cover sources and steps outside this tool.

| # | Item | Status | Draft text |
|---:|---|---|---|
| 1 | Database name | filled | OpenAlex (https://openalex.org), searched through the OpenAlex API's OQL interface (https://api.openalex.org), corpus: core (OpenAlex's default set of works). |
| 2 | Multi-database searching | not applicable | Not applicable: one database (OpenAlex) was searched by this tool. |
| 3 | Study registries | reviewer to complete | This tool searches no study registries. List any registries searched separately. |
| 4 | Online resources and browsing | reviewer to complete | Describe any online or print sources searched or browsed outside this tool. |
| 5 | Citation searching | partly filled | Citation searching was used during strategy development: 1 batch(es) of OpenAlex citation links (1 citing) from 1 development work(s), with 7 candidate decision(s) recorded and 4 included. These informed the strategy; they are not a separate set of records in this report. The links are OpenAlex's matched references, as of 2026-11-26: about 82% of references to works in OpenAlex are linked, unmatched references are dropped and many records carry no reference list (OpenAlex documentation, November 2026); OpenAlex revised its links on 25 November 2026, so a rerun on another date can differ. If the optional no-abstract rescue (`rescue`) is run, report it here as a supplementary citation search, with its manifest. |
| 6 | Contacts | reviewer to complete | State whether authors, experts or others were contacted for additional studies or data. |
| 7 | Other methods | partly filled | 1 random-sample pilot(s) of candidate queries were screened during development to check precision and terminology; they were not a source of records. Report any other methods. |
| 8 | Full search strategies | filled | The OpenAlex search exactly as run is under 'Search as executed' and in final-query.oql (OQL) and final-query.json (the OpenAlex query object), executed with include_pulled=false. |
| 9 | Limits and restrictions | partly filled | Limits applied: publication date 2015-01-01 to 2025-12-31. Corpus: core (OpenAlex's default set of works). Pulled works: retracted, withdrawn and removed works excluded (include_pulled=false, OpenAlex's default view). Scope decision recorded at approval: "Reviewer approved delivery and setting search blocks, outcomes at screening, core corpus, pulled excluded, 2015-2025 window.". Confirm that it justifies each limit. |
| 10 | Search filters | partly filled | The tool builds no published search filter into the strategy. State whether any block reproduces or adapts a published filter, and cite it. |
| 11 | Prior work | reviewer to complete | State whether search strategies from earlier reviews were adapted or reused, and cite them. |
| 12 | Updates | partly filled | To update, rerun final-query.json against the OpenAlex API (POST https://api.openalex.org/ with the query object and include_pulled=false), or rerun `report` in this workspace. A rerun searches the live index on that date, so counts can change. State the update method actually used. |
| 13 | Dates of searches | filled | OpenAlex was last searched on 2026-11-26 (at 07:07:50 UTC). |
| 14 | Peer review | partly filled | An internal PRESS-structured critique (six domains) was recorded on 2026-11-26. It is not formal peer review; describe any human peer review of the search. |
| 15 | Total records | partly filled | OpenAlex: 4,280 records on 2026-11-26. Add the records identified from each other source. |
| 16 | Deduplication | partly filled | Within OpenAlex, records are unique by OpenAlex Work ID; `export` deduplicates by Work ID, including across the optional rescue queries. Describe the process and software used to deduplicate records across databases and other sources. |


Draft for human peer review. OpenAlex metadata and counts can change.

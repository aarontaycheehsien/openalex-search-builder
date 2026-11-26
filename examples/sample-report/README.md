# Sample Delivery Output (Illustrative)

> **Illustrative sample — not a live OpenAlex index snapshot.**
> These files were generated offline by running `scripts/oasb.py`'s real `report` pipeline against an illustrative synthetic telehealth dataset so you can inspect the exact structure and formatting of a delivered report without needing an API key first.

When you run `oasb.py report` against the live OpenAlex API, each delivery under `reports/<evaluation-id>/` contains:

- [`final-query.oql`](final-query.oql) — The exact canonical OQL query as executed by OpenAlex.
- [`final-query.json`](final-query.json) — The structured OpenAlex Query Object (OQO) for exact programmatic re-execution and cursor export.
- [`audit.md`](audit.md) — Human-readable audit report for peer review, including:
  - Scope and eligibility record (blocks, roles, limits, corpus, pulled-work view)
  - Block-by-block counts and leave-one-out ablation (`without/<block>`)
  - Missing-abstract attrition table (when run with `--abstract-stats`)
  - Separate relative recall tables with 95% Wilson score intervals for `seed`, `relevant`, `validation`, and `benchmark` sets
  - Holdout exposure and blinding status
  - Pilot precision estimate (from screened `sample --from-strategy` batches)
  - Missed-work diagnosis and timestamped revision history
  - 6-domain PRESS-structured internal critique with live-filled `{placeholder}` figures
  - 16-item draft **PRISMA-S** checklist (Rethlefsen et al., 2021)
- [`prisma-s.json`](prisma-s.json) — Machine-readable version of the 16-item draft PRISMA-S checklist.
- `validation.json` *(omitted from this sample for brevity)* — Full JSON archive of counts, role-specific retrieval, unresolved identifiers, screening decisions, critique, and revision history.

# OpenAlex Search Builder

An agent skill and Python CLI for researchers developing a high-sensitivity OpenAlex search. Give Codex or Claude Code a review question and optional known papers. The skill builds a strategy, tests it against OpenAlex, diagnoses missed papers by concept, and delivers a reproducible OQL query with its audit.

The workflow adapts Aaron Tay's [PubMed Search Builder](https://github.com/aarontaycheehsien/pubmed-search-builder-next). Search execution, field interpretation, work identity and citation discovery use OpenAlex. Python 3.10+ is sufficient. The CLI has no third-party dependencies.

## Use the skill

Clone the repository, open the folder in your agent, and ask:

> Read SKILL.md. Develop an OpenAlex search strategy for a scoping review of telemedicine delivery in rural communities. Treat W2618851878 as a seed paper. Use standard depth and discuss the concept blocks with me.

Supply a free OpenAlex key through the environment variable `OPENALEX_API_KEY` for repeated searches. The CLI sends it in the authorization header. Anonymous calls have a small shared allowance. Block counts, known-record intersections and final revalidation each consume requests. [OpenAlex authentication](https://help.openalex.org/api/authentication/) · [OQL API](https://help.openalex.org/api/oql/)

## Try the CLI

From the repository root, run:

```sh
python scripts/oasb.py -w runs/demo init --question "How is telemedicine used in rural communities?"
cp examples/telehealth.json runs/demo/strategy.json
python scripts/oasb.py -w runs/demo scope
python scripts/oasb.py -w runs/demo scope --confirm --note "Reviewer approved delivery and setting blocks"
python scripts/oasb.py -w runs/demo resolve W2618851878 --role seed --source "Reviewer"
python scripts/oasb.py -w runs/demo validate
python scripts/oasb.py -w runs/demo eval
```

Edit the vocabulary and rerun `eval` to see gained or lost known works and the change in workload. Compare the `title-abstract` and `title-abstract-keywords` fields. Missing abstracts can make a restrictive block costly. The `without/BLOCK` lines quantify the impact of dropping one block.

| Task | Command after the workspace argument |
|---|---|
| Mine terminology from development papers | `terms` |
| Find works that cite a seed | `neighbors W2618851878 --direction citing --size 10` |
| Follow its reference list | `neighbors W2618851878 --direction references` |
| Save a random screening pilot | `sample --oql "get works about telemedicine" --seed 42 --size 10` |
| Record a candidate's eligibility | `screen BATCH WORK_ID include --reason "Meets population and delivery criteria"` |
| Attach a completed internal critique | `review critique.json` |
| Revalidate and deliver | `report` |

See [SKILL.md](SKILL.md) for the workflow, strategy schema and critique format. Search terms are grouped inside concept blocks. The CLI supports explicit publication-date, language and work-type limits. Broader OQL queries can be used for discovery pilots.

## Outputs

Each delivery under `reports/<evaluation-id>/` includes the exact executable `final-query.oql`, its OpenAlex query object in `final-query.json`, a human-readable `audit.md` and a `validation.json` archive with counts, role-specific retrieval, unresolved citations, critique and revision history. Earlier deliveries remain identifiable as earlier snapshots.

Coverage is the number of distinct resolved eligible works divided by resolved works plus unresolved eligible entries. Relative recall is the fraction of resolved eligible works retrieved. Reviewers should reconcile citations that have several unresolved aliases. The development, validation and external benchmark sets are reported separately. Work IDs provide the deduplication unit. The workflow does not estimate total recall across unknown eligible works.

## Verification

```sh
python -m unittest discover -s tests -v
```

Use the [benchmark guide](evals/README.md) for blind seeded and seedless runs against biomedical and education reviews. A manual **OpenAlex live checks** workflow can run the integration smoke test and benchmark reference queries using a repository Actions secret named `OPENALEX_API_KEY`.

[OpenAlex systematic reviews](https://help.openalex.org/tutorials/systematic-reviews/) · [License](LICENSE)

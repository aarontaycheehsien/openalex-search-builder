# Frozen held-out fixtures (OpenAlex)

Ported from [pubmed-search-builder-next/evals/fixtures/frozen](https://github.com/aarontaycheehsien/pubmed-search-builder-next/tree/main/evals/fixtures/frozen).
These ten fixtures are reserved for **final, blind held-out evaluation**—do not inspect their gold lists or tune prompts/strategies against them during skill development.

| Fixture | Domain | Gold PMIDs |
|---|---|---:|
| `housing-first-criminal-justice` | Housing / criminal justice | 5 |
| `social-prescribing-older-adults` | Social care / community health | 10 |
| `work-directed-return-to-work` | Labour / occupational policy | 11 |
| `school-restorative-practice` | Education / school climate | 6 |
| `healthcare-nudging` | Behavioural science / healthcare | 86 |
| `Cohen_2006_ADHD` | ADHD pharmacotherapy | 20 |
| `Cohen_2006_UrinaryIncontinence` | Urinary-incontinence treatment | 40 |
| `Cohen_2006_Antihistamines` | Antihistamine pharmacotherapy | 16 |
| `Cohen_2006_Estrogens` | Estrogen pharmacotherapy | 80 |
| `Cohen_2006_ACEInhibitors` | ACE-inhibitor pharmacotherapy | 41 |

All questions and eligibility criteria were scrubbed upstream so they do not name the source review or answer key. `bench.py score` resolves PMIDs live in OpenAlex, reports any unresolved PMIDs, and evaluates conditional relative recall within `to_date`.

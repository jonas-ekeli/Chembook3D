# Chembook3D specification pack

Specifications (no code) for Chembook3D, a local-first notebook for computational chemistry mechanism investigations. Written 2026-09-29 from the alignment interview that Jonas confirmed.

| # | Document | Contents |
|---|---|---|
| 01 | [product-brief](01-product-brief.md) | Problem, user, goals, workflows, MVP scope, exclusions |
| 02 | [domain-model](02-domain-model.md) | Glossary, entities, identity rules, statuses, energy rules, pathway invariants, the Ru-CAAC worked example |
| 03 | [workflows-and-ux](03-workflows-and-ux.md) | Journeys WF-01…11, canvas behaviour, 3D, resume overview |
| 04 | [functional-requirements](04-functional-requirements.md) | FR-… requirements with acceptance criteria and phases |
| 05 | [data-and-calculation-integrations](05-data-and-calculation-integrations.md) | Records, file copying, parsers, thermochemistry, units, provenance |
| 06 | [architecture-and-operations](06-architecture-and-operations.md) | Stack, storage, packaging, NFRs |
| 07 | [validation-and-test-plan](07-validation-and-test-plan.md) | T-… tests: lineage, identity, import, energies, portability |
| 08 | [decisions-and-open-questions](08-decisions-and-open-questions.md) | D1–D58, assumptions, confirmed proposals P1–P30, open questions, exclusions, risks |
| 09 | [implementation-roadmap](09-implementation-roadmap.md) | Phases 0–6 with requirements, tests and prerequisites |

Background: [alignment-record.md](alignment-record.md) (interview log) and [alignment-summary.md](alignment-summary.md) (confirmed summary).

**How to read the IDs:** a Dnn is confirmed by Jonas; a Pnn is a proposal Jonas has confirmed (D51), kept under its own ID; a Qnn is open; an Ann is a working assumption. When a decision changes, update 08 first, then every document that cites it.

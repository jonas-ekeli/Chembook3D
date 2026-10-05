# 01 · Product brief

_Chembook3D specification pack · v1.1 · 2026-09-29 · Source of truth for decisions: [08-decisions-and-open-questions.md](08-decisions-and-open-questions.md) (decision IDs Dnn)._

## 1. Problem

Jonas investigates catalytic reaction mechanisms computationally. A single investigation involves many related intermediates, conformers, configurations, transition structures and repeated calculations. Today the reaction map lives in ChemDraw and the calculations live in folders. This causes:

1. Structures that look identical in 2D but differ in 3D or computational detail.
2. Lost reasoning about why similar structures are distinct.
3. A reaction map disconnected from inputs, outputs, methods, results and notes.
4. No clear view of which structures and pathways are complete, incomplete, rejected, superseded or speculative.
5. Slow recovery of context after weeks or months away.
6. Hard-to-track alternative or repeated calculations on the same geometry.
7. Diagrams, reported energies and underlying files drifting out of sync.

## 2. Product

Chembook3D is a **local-first computational chemistry notebook** (D1). It combines:

- an **infinite 2D canvas** of structure nodes joined by typed edges, forming branches and pathways (D2, D33);
- **structured records** behind every node: geometry, charge, spin, calculations, energies, files, notes, status and history;
- an **interactive 3D viewer** for inspecting geometries (D20).

Its central scientific promise is that **pathway lineage is preserved**: branches of conformers and configurations are tracked as distinct lineages across consecutive reaction steps, and energies never rewrite or collapse them (D4).

## 3. Users

| User | Role | Status |
|---|---|---|
| Jonas | Primary and only user of the first version: computational chemist, Python-literate | Confirmed (D6) |
| Colleagues | May receive a zipped investigation or use the tool later | Future; not designed for now (D6, X2) |

## 4. Reference use case

Olefin metathesis with **asymmetric Ru-CAAC catalysts** carrying bulky fragments with hindered rotation. Rotamers and binding configurations persist across several steps of the catalytic cycle and may later be reconnected by the user (D11, D12, D13). The worked example lives in [02-domain-model.md §6](02-domain-model.md).

## 5. Goals

| ID | Goal |
|---|---|
| G1 | Keep every structure's exact 3D geometry, calculations and files together with its place on the reaction map. |
| G2 | Make it explicit why two similar structures are distinct (branch, status, notes, derivation history). |
| G3 | Preserve branch lineage across steps, including splits, reconnections and interconversions, under user control. |
| G4 | Show energies only where they are comparable (same level of theory and energy type). |
| G5 | Let the user resume an investigation after a long gap from one overview. |
| G6 | Keep an investigation self-contained and shareable as one folder. |

## 6. Primary workflows (detail in [03-workflows-and-ux.md](03-workflows-and-ux.md))

- WF-01 Create and open an investigation
- WF-02 Create a node manually
- WF-03 Plan a node, then import its finished calculation
- WF-04 Import a Gaussian (or ORCA/xTB) output
- WF-05 Import a CREST conformer ensemble
- WF-06 Build reaction steps, branches and transitions on the canvas
- WF-07 Reconnect branches at a group node
- WF-08 View energies: edges, profile and table
- WF-09 Inspect a structure in 3D
- WF-10 Resume an investigation after a gap
- WF-11 Export and share

## 7. First-version scope (MVP)

In scope:

- Manual node creation with notes and text-editable xyz coordinates (D7, D20, D37)
- Import of Gaussian (priority), ORCA, xTB and CREST outputs, delivered in phases (D8, D48)
- Copying imported files into the investigation, with origin metadata (D16)
- Reaction steps, branches, transitions, group nodes and statuses (D12–D19)
- Energies on edges, energy profiles and an energy table, all at a single chosen level and energy type (D27, D32, D44)
- Quasi-harmonic free energy using Truhlar's 100 cm⁻¹ raise (D41, D49)
- View-only 3D viewer (D20)
- Canvas view modes, canvas image export, easy xyz access (D39, D40, D43)
- Change history and a resume overview (D29, D45)
- Local-only operation on Windows and Linux (D10, D42)

## 8. Explicit exclusions (first version)

| ID | Exclusion |
|---|---|
| X1 | No launching, queueing or monitoring of calculations on this computer. Calculations Claude sets up may be handed to the user's Claude Code cloud sessions (D93). |
| X2 | No multi-user collaboration. Syncing one person's investigations between their own computers through a private Git repository is in scope (D71). |
| X3 | No geometry editing in the 3D viewer (text editing of coordinates is in scope). |
| X4 | No internet hosting, login or access control. |
| X5 | The app never chooses a pathway, representative or branch on the user's behalf from energies. |
| X6 | The app does not claim scientific correctness. Parsing and validation warnings report what the files say, not whether the chemistry is right. |

## 9. Success criteria

The first version succeeds if Jonas can rebuild the Ru-CAAC four-branch example ([02 §6](02-domain-model.md)) from real Gaussian outputs, then:

- see each branch's lineage and status on the canvas;
- open any node's geometry in 3D and copy its xyz;
- view a free-energy profile of one branch at one level of theory;
- reopen the investigation later and find the open items from the overview;

all without consulting the original folders.

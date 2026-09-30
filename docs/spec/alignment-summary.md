# Chembook3D: alignment summary for confirmation

_2026-09-29. Condenses [alignment-record.md](alignment-record.md) (decision numbers D1–D47 in brackets). Once Jonas confirms, this becomes the basis for the nine specification documents listed in HANDOFF.MD._

## 1. Product
- A **local-first computational chemistry notebook** for catalytic mechanism investigations. It brings together an infinite 2D reaction map, structured records behind every node, and a 3D viewer. [D1, D2]
- **User:** Jonas, working alone. It should be shareable with others later, but collaboration is not a goal of the first version. [D6, X2]
- **Reference chemistry:** olefin metathesis with asymmetric Ru-CAAC catalysts whose bulky fragments have hindered rotation. [D11]
- **Not in scope:** launching, queueing or monitoring calculations [X1], editing geometry in the 3D viewer [X3], internet hosting or login in the first version [X4].

## 2. Core concepts (agreed terminology)
| Term | Meaning |
|---|---|
| Node | One distinct 3D geometry, with its charge, spin, notes and status. [D15] |
| Calculation | A job on a node's geometry (program, method, files, results). A node can have many; single points at other levels stay on the same node. [D15] |
| Reaction step | A conceptual step of the mechanism, shared by all branches. [D3] |
| Concrete transition | An edge between two specific nodes that realizes a reaction step in one branch. Interconversion between branches (for example a rotation TS) is also an ordinary transition. [D3, D17] |
| Branch | A lineage of nodes and transitions that keeps its conformational or configurational identity across steps. [D12] |
| Reconnection | A user-declared point where branches meet. It is shown as a **group node** holding the conformers from the incoming branches, and the user marks one as the representative that the path continues from. TSs leading into it are optional. [D13, D14, D18] |
| Level of theory | Program + method + basis (custom sets named by the user) + dispersion + solvation. A single point's level includes its geometry level, as in QZ//DZ. [D30, D31, D35] |

**Branch invariants**
- Pathways are never assembled from per-step energy minima. Energies never rewrite or collapse lineage. [D4]
- Only the user splits, reconnects or picks representatives. The app may sort by energy but never chooses. [D13, D18]
- Relative energies are only ever computed at one level of theory and one energy type, never across levels. [D27, D32]

**Worked example (accepted)** [D12]: CAAC N-aryl rotamers A and B split at initiation and persist. Olefin coordination splits them again, into A1/A2/B1/B2. All four branches share five reaction steps (coordination, [2+2] TS, metallacyclobutane, retro-[2+2] TS, product π-complex) but have their own concrete transitions. After product release, the user may reconnect them into a group node at the regenerated alkylidene, with or without computed connecting TSs. An A↔B rotation TS is an ordinary transition between branches.

## 3. Nodes, identity and history
- You can create nodes by hand; no field is mandatory and free-text notes are supported. [D7, D37]
- You can edit coordinates as text while a node has no calculations. Once it has one, an edit creates a new derived node, and the original keeps its results. [D20, D23]
- **Planned-node flow:** create a planned node with a guess geometry, then import the finished output onto it. The guess stays in the change history. [D24, D29]
- **Statuses** (nodes, transitions, branches): planned, running externally, done, failed, rejected, superseded. Validation warnings (for example imaginary-frequency counts) never change a status. [D19]
- If an import nearly matches an existing geometry, the app warns that it may be a duplicate and you decide. It never merges automatically. [D47]

## 4. Calculations, files and energies
- **Imports:** Gaussian (priority), ORCA, xTB, CREST. The app extracts geometry, charge and multiplicity, method, basis, solvation, E, ZPE, H, G (with T and P), frequencies and imaginary-mode count, and termination status. Partial parses import what they can and flag the rest. [D8]
- **Multi-step jobs:** the app takes the last geometry from the highest-level step. **Failed optimizations:** it imports the last geometry and tags the node "optimization incomplete". [D25, D26]
- **CREST:** keeps the lowest N conformers (N configurable) inside a group node, and makes it easy to remove unwanted ones. [D34, D46]
- **Files are copied** into the investigation. Each copy records the external device or server name and the path it came from. [D16]
- **Free energy** at a composite level is the single-point E plus the thermal correction from the geometry-level frequency job. A quasi-harmonic variant, with low modes raised to 100 cm⁻¹ as in GoodVibes, is recomputed by the app and offered as its own energy type. [D35, D36, D41]
- **Energy view:** you choose the level and energy type from a drop-down (free energy preferred, electronic energy as fallback). The relative energy lives on the edge, and profiles add up from a chosen reference node. The unit defaults to kcal/mol and is configurable. [D27, D28, D32]

## 5. Canvas, 3D and outputs
- **Canvas:** a colour per branch, status badges, energies on edges (can be hidden), manual layout plus a helper that lays out one branch step by step, and filtering by branch, status or step. [D33]
- **Node view modes:** compact, energy, structure (a small rendered image). [D40, D43]
- **3D viewer:** rotate, measure, animate imaginary modes, overlay two geometries. [D20]
- **Outputs:** energy profiles, canvas image, easy access to xyz coordinates, and an energy table (one row per node, CSV export). [D39, D44]
- **Resume overview:** branch status, open items, recent changes and notes per step. [D45]

## 6. Architecture and operations
- **Stack direction:** a Python backend (FastAPI, SQLite) with a TypeScript web interface (React Flow canvas, 3Dmol.js), running locally on Windows and Linux. [D10, D22, D38]
- Each investigation is **one self-contained folder** (database plus copied files) that you can zip and share. Backup is your responsibility. [D21]

## 7. Still open
| # | Question | Blocks? |
|---|---|---|
| Q26b | What waits until after the first version (see the proposal below) | Roadmap |
| Q30 | Whether the 100 cm⁻¹ raise applies to entropy only, or also to H and ZPE. GoodVibes's default is Grimme's interpolation, while "raise" matches Truhlar's method. | Thermochemistry requirement |
| Q31 | Standard-state (1 M) correction and temperature: none, fixed or configurable | No; can be a setting |
| Q32 | Numerical tolerance for a near-duplicate geometry | No; setting |
| Q33 | Desktop window or browser tab, and UI component libraries | No; implementation choice |

## 8. Proposed phasing (proposal, not yet agreed)
1. **Foundation:** investigation folder, SQLite schema, manual nodes with notes, xyz text editing, change history, statuses.
2. **Gaussian import:** parsing, multi-step and failed-job handling, file copying with origin metadata, duplicate warning.
3. **Canvas and 3D:** nodes, transitions, reaction steps, branches, group nodes, view modes, filtering, the 3D viewer.
4. **Energies:** level-of-theory matching, composite levels, quasi-harmonic correction, edge energies, profiles, energy table, exports.
5. **ORCA, xTB and CREST import**, then the resume overview.
6. **Later:** hosting, collaboration.

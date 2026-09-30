# 02 · Domain model

_Chembook3D specification pack · v1.1 · 2026-09-29. This document defines meaning and rules, not a database schema. The storage mapping is in [06-architecture-and-operations.md](06-architecture-and-operations.md)._

## 1. Glossary

| Term | Definition | Source |
|---|---|---|
| **Investigation** | A self-contained project folder holding all records, copied files and history for one research question. | D21 |
| **Node** | One distinct 3D geometry, with its charge, multiplicity, role, status, notes and calculations. The unit shown on the canvas. | D15 |
| **Geometry** | The atoms and Cartesian coordinates (Å) of a node. A node has exactly one current geometry. | D15, D23 |
| **Node role** | *Minimum*, *transition state* or *unspecified*. Set by the user; used for validation warnings only. | P1 (supports D19 warnings) |
| **Calculation** | One job run on a node's geometry (optimization, TS optimization, frequency, single point, conformer search, other). It has a program, a level of theory, source files and parsed results. A node can have many. | D15 |
| **Level of theory** | Program + method or functional + basis set + dispersion correction + solvation model (model and solvent). Custom basis sets are identified by a user-given name. | D30, D35 |
| **Composite level** | A single-point level written with the level its geometry came from, e.g. `ORCA DLPNO-CCSD(T)/cc-pVQZ // Gaussian B3LYP-D3/cc-pVDZ`. Two single points at the same method on geometries from different levels are different composite levels. | D31 |
| **Energy type** | Electronic energy (E), enthalpy (H), free energy (G) or quasi-harmonic free energy (G_qh, Truhlar). | D32, D41, D49 |
| **Reaction step** | A conceptual position in the mechanism shared by all branches, e.g. "olefin π-complex", "[2+2] TS", "metallacyclobutane". Steps have a user-defined order. Each node may be assigned to one step. | D3, D12; reading of the accepted example, P2 |
| **Concrete transition** | A directed edge between two specific nodes. It realizes the move between their reaction steps in one branch. | D3, D12 |
| **Direct connection** | A concrete transition where neither endpoint is a transition-state node (e.g. intermediate → intermediate). It is always marked "no TS" on the canvas, in the overview and in energy profiles. | D53 |
| **Interconversion** | A concrete transition between nodes of *different* branches, typically within the same reaction step (e.g. an A↔B rotation). It is an ordinary transition edge; nothing about it changes branch membership. | D17 |
| **Branch** | A named, coloured lineage of nodes that carries a conformational or configurational identity across steps. Branches have parent branches, so lineage can always be traced back. | D12 |
| **Split** | The point where one branch's node has transitions to nodes that start new child branches. | D12 |
| **Free species** | A molecule that joins or leaves the mechanism on a transition, such as a substrate, a released ethylene or a product. It has a geometry, calculations and energies like a node, but no step, branch, group or edges, and is not drawn on the canvas. Transitions list the species that join or leave on them, so relative energies stay mass-balanced. | D69 |
| **Group node** | A canvas node that contains several member nodes (conformers) and one optional user-chosen **representative**. Used for reconnections and CREST ensembles. | D18, D46 |
| **Reconnection** | A user-declared group node where incoming branches end and one outgoing branch starts. Members keep their branch (D66). | D13, D14, D18, D66 |
| **Pathway** | A connected sequence of concrete transitions, following edges only. Energy profiles are drawn along pathways. | D4 |
| **Status** | One of *planned*, *running externally*, *done*, *failed*, *rejected*, *superseded*; set by the user on nodes, transitions and branches. | D19 |
| **Warning** | An automatic, non-blocking finding about a record (e.g. imaginary-frequency count). Warnings never change a status. | D19 |
| **Tag** | A label on a node. The system tag `optimization-incomplete` is set on import of a failed optimization. | D26 |
| **Source file** | A copy of an imported file stored inside the investigation, with origin metadata. | D16 |
| **Change history** | The append-only record of changes to records. | D29 |

## 2. Entities and relationships

```
Investigation
 ├── ReactionStep (ordered)
 ├── Branch ── parents: Branch[0..n]      (split: 1 parent; reconnection outgoing: n parents)
 ├── Node ── branch: Branch[0..1]
 │     ├── step: ReactionStep[0..1]
 │     ├── group: GroupNode[0..1]          (membership)
 │     ├── derived_from: Node[0..1]        (coordinate edit after calculations, D23)
 │     ├── Geometry (current, 1)
 │     └── Calculation[0..n]
 │            ├── LevelOfTheory (+ geometry level → composite)
 │            ├── Results (E, ZPE, H, G, T, P, frequencies, termination …)
 │            └── SourceFile[0..n] (copied, with origin metadata)
 ├── GroupNode ── members: Node[1..n], representative: Node[0..1]
 │              incoming branches: Branch[0..n], outgoing branch: Branch[0..1]
 ├── Transition (source Node|GroupNode → target Node|GroupNode, status, notes)
 │     └── species: [free-species Node, joins|leaves, count][0..n]   (D69)
 ├── LevelOfTheory registry, named custom basis sets
 └── ChangeHistory entries
```

Notes:
- _(P5)_ A **node belongs to at most one branch**. Nodes before any split sit in the trunk branch the user creates (or in no branch).
- Group members keep their branch, so one group can hold members from several branches (D66); each also records the branch it came from.
- A node is either a pathway node or a **free species** (D69). A free species has no step, branch, group or transitions of its own; it is attached to transitions as joining or leaving.
- A transition may end or start at a group node. A transition *out of* a group node is drawn from the group; for energies it uses the representative (see §5).

## 3. Identity rules

| Rule | Statement |
|---|---|
| ID-1 | Every entity has a stable internal ID that never changes. Labels are user text and need not be unique. |
| ID-2 | A node's identity is its geometry. Different geometries are different nodes (D15). |
| ID-3 | Calculations on the same geometry stay on the node: e.g. single points at other levels, frequency jobs (D15). |
| ID-4 | While a node has **no calculations**, its geometry can be edited in place (D23). |
| ID-5 | Once a node has a calculation, editing its coordinates creates a **new node** with `derived_from` set to the original. The original and its calculations are unchanged (D23). |
| ID-6 | Importing a calculation onto a planned node with no calculations replaces its provisional geometry with the imported one. The previous geometry is kept in the change history (D24). |
| ID-7 | A calculation imported onto an existing node must match that node's atoms and coordinates. If it does not, the app warns and offers to create a new node instead (see FR-IMP-08). |
| ID-8 | If an import nearly matches an existing node (same composition, charge and multiplicity, and geometry within a configurable tolerance), the app warns of a possible duplicate. The user decides. The app never merges (D47). |

## 4. Lifecycle

**Status** (nodes, transitions, branches; D19): `planned → running externally → done | failed`, and any status → `rejected | superseded`. The user may set any status at any time; the app suggests but never sets a status automatically.

**Warnings** (computed, never stored as status):

| Code | Condition |
|---|---|
| W-IMAG-MIN | Node role *minimum* and its frequency calculation has ≥1 imaginary frequency |
| W-IMAG-TS | Node role *transition state* and its frequency calculation does not have exactly 1 imaginary frequency |
| W-TERM | A calculation did not terminate normally |
| W-OPT-INC | Node carries the `optimization-incomplete` tag |
| W-NOFREQ | Free energy requested but the node has no frequency calculation at the required level |
| W-LINEAR | G_qh requested for a linear molecule (D58, A14) |
| W-QH | G_qh cannot be computed at the chosen temperature or cutoff (A14) |
| W-CHG | Calculations on one node disagree on charge or multiplicity |
| W-GEOM | A calculation's geometry does not match its node's geometry |
| W-PARSE | A file was only partly parsed; the missing fields are listed |
| W-DUP | Possible duplicate of another node (ID-8) |
| W-BALANCE | A transition's atoms or total charge differ before (source + joining species) and after (target + leaving species); checked only when all of them have coordinates (D69) |

## 5. Energies

| Rule | Statement |
|---|---|
| EN-1 | Energies are computed from parsed calculation results. They are never typed on the canvas. |
| EN-2 | A view has exactly one **(composite level, energy type)** selection, chosen from those available in the investigation (D32). |
| EN-3 | An edge's value is ΔX = X(target) − X(source) at the selected level and type, plus X of each free species leaving on it and minus X of each joining (D69). If either end or a species lacks it, the edge shows "n/a". It never falls back to another level or type (D27). |
| EN-4 | G at a composite level = E(single point) + [G − E] from the frequency calculation at the geometry level on the same node (D35). |
| EN-5 | G_qh = E(single point) + [G − E]_qh. The quasi-harmonic correction is recomputed from the frequency step's parsed data following Jonas's reference script (`thermochem_corr_G16`): real frequencies only; ZPE and vibrational thermal energy from the **unmodified** frequencies; vibrational **entropy** with every frequency below the cutoff raised to the cutoff (Truhlar); plus translational, rotational and electronic terms and PV (D41, D49, D56). |
| EN-6 | G_qh uses a temperature and a cutoff that are parameters, defaulting to 298.15 K and 100 cm⁻¹, and stored with each computed value. The job-printed G keeps the job's own T and P. No concentration or standard-state correction is applied: translational entropy is for an ideal gas at 1 atm (D57, D58). |
| EN-7 | A group node's energy for edges is its representative's energy. With no representative, its edges show "n/a". |
| EN-8 | A profile is relative to a user-chosen reference node on the pathway: value(n) = X(n) − X(ref), plus X of every free species that left and minus X of every one that joined between the reference and n (D69). Node cards in energy mode use the same balance, along the route from the reference that D72 defines. |
| EN-9 | Units: stored in hartree, displayed in kcal/mol by default, configurable (D28). |
| EN-10 | Energies may be sorted and displayed, but never used by the app to pick nodes, representatives or pathways (D4, X5). |

## 6. Worked example: Ru-CAAC olefin metathesis (accepted, D12)

**Reaction steps (ordered):** S0 14e alkylidene · S1 olefin π-complex · S2 [2+2] TS · S3 metallacyclobutane · S4 retro-[2+2] TS · S5 product π-complex · S6 regenerated alkylidene.

**Branches:**
- Trunk `T`: precatalyst initiation up to the split.
- Split 1 at initiation: `A` and `B` (N-aryl rotamers), parents = `T`.
- Split 2 at olefin coordination: `A1`, `A2` (parent `A`) and `B1`, `B2` (parent `B`), syn and anti olefin binding.

```
 T ──► A-S0 ──► A1-S1 ──► A1-S2(TS) ──► A1-S3 ──► A1-S4(TS) ──► A1-S5 ─┐
            └─► A2-S1 ──► A2-S2(TS) ──► A2-S3 ──► A2-S4(TS) ──► A2-S5 ─┤
   └► B-S0 ──► B1-S1 ──► …                                   B1-S5 ─┤──► [Group G6 at S6]──► R …
            └─► B2-S1 ──► …                                   B2-S5 ─┘      rep = user choice
         A-S3 ◄──rot-TS──► B-S3   (interconversion; ordinary transitions)
```

Checks against the model:

| Question | Answer from the model |
|---|---|
| Do A1 and B1 share a reaction step? | Yes. A1-S3 and B1-S3 are both assigned to S3, and the step definition is shared. |
| Can the app build "lowest-energy path" by picking the minimum at each step? | No. Pathways follow edges only (EN-10, INV-2). |
| What is A1's lineage? | A1 → A → T, from branch parents. |
| Where do branches reconnect? | Only where the user creates group G6 (D13). Its members are the S6 conformers from A1…B2. The outgoing branch R has parents A1, A2, B1, B2. |
| Are TSs into G6 required? | No. Transitions into G6 may be plain connections with no TS node (D14). |
| How is an A↔B rotation shown? | As transitions A-S3 → rot-TS → B-S3 between branches. Membership of A-S3 and B-S3 is unchanged (D17). |
| xTB pre-optimization of A1-S3, then DFT re-optimization? | Two nodes; the DFT node is created from the import and linked `derived_from` (ID-2, ID-5). |
| Single point cc-pVQZ//cc-pVDZ on A1-S3? | A second calculation on the same node, with a composite level (ID-3, D31). |

## 7. Pathway invariants

| ID | Invariant |
|---|---|
| INV-1 | Every node's branch lineage can be traced to its root through branch parents, and this lineage never changes because of energy values. |
| INV-2 | A pathway is a sequence of existing transitions. No operation creates a pathway by selecting nodes per step without edges. |
| INV-3 | Splitting, reconnecting, choosing a representative and changing branch membership happen only through explicit user actions, each recorded in the change history. |
| INV-4 | An interconversion transition never changes the branch of either endpoint. |
| INV-5 | All energy values shown in one view share one composite level and one energy type. |
| INV-6 | A calculation's results always remain attached to the geometry they were computed on (ID-5). |
| INV-7 | _(P3)_ Deleting or rejecting a node never silently rewires transitions. Transitions touching a deleted node are deleted with it, after a confirmation that lists them; rejected nodes keep their edges. |
| INV-8 | A direct connection is never displayed as if a barrier had been computed: profiles and edges mark it "no TS" (D53). |

## 8. Edge cases

1. **Branch with a gap:** A2 has no S4 TS yet. The profile shows the path up to S3; the S3→S5 edge exists only if the user drew it, with status *planned*.
2. **Two TS candidates for the same step in one branch:** both are nodes in branch A1 at S2; the user marks one *superseded*. Both stay visible (filterable).
3. **Group with no representative:** edges out of it show "n/a" energies (EN-7).
4. **Mixed levels:** A1 has G at B3LYP-D3/def2-TZVP, B1 only at def2-SVP. With the TZVP view selected, B1's edges show "n/a".
5. **Failed optimization imported onto a planned TS node:** the geometry is replaced, the node is tagged `optimization-incomplete`, and the calculation shows W-TERM.
6. _(P4, D54)_ **Reconnection later undone:** the user removes G6 and chooses either **dissolve** (members stay as nodes and keep their branch or have it cleared, by user choice; A26) or **delete with contents** (the group and all its member nodes, with their calculations and edges, are deleted after a confirmation that lists them). The history shows both actions.
7. _(D53)_ **Direct connection:** the user connects A1-S1 straight to A1-S3 without a TS node. The edge is a direct connection: the canvas, the overview and the energy profile all show it as "no TS", and the profile does not imply a barrier.
8. _(D52)_ **Frequency added later:** A1-S3 was optimized without a frequency step. Later a file with an opt + freq job is imported onto it. If the opt step changed the geometry beyond tolerance, the app offers a derived node (ID-7); otherwise each step is recorded as a calculation on A1-S3, and the freq becomes its thermal-correction source.

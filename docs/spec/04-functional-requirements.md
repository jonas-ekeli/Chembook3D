# 04 · Functional requirements

_Chembook3D specification pack · v1.1 · 2026-09-29. Each requirement has a stable ID, a trace to decisions (Dnn) or confirmed proposals (Pnn) in [08](08-decisions-and-open-questions.md), a workflow (WF-nn in [03](03-workflows-and-ux.md)), and acceptance criteria (AC). Phase = build phase in [09](09-implementation-roadmap.md). Operational and quality requirements (NFR) are in [06](06-architecture-and-operations.md)._

## FR-INV · Investigations

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-INV-01 | The app creates an investigation as one folder containing its database and a `files/` directory for copied source files. | D21, WF-01 | 1 |
| FR-INV-02 | The app opens an investigation by folder, and keeps a list of recently opened ones. | WF-01 | 1 |
| FR-INV-03 | An investigation folder copied or unzipped to another machine (Windows ↔ Linux) opens with all records and files intact. | D10, D21 | 1 |

**AC:** create an investigation, add a node and import a file, zip the folder, unzip it on the other OS and open it: all nodes, calculations, files and history are present, and no absolute paths are needed.

## FR-SYNC · Versions and sync (D71)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SYNC-01 | Opening an investigation saved by a newer app version (a schema revision this app does not know) stops before anything is written and says to update the app, with the commands. | D71 | after 5 |
| FR-SYNC-02 | An open investigation can be linked to an empty Git repository by its address. The app writes `.gitignore` (lock file, journal, `backups/`) and `.gitattributes` (no line-ending conversion), commits and pushes. A repository that is not empty is refused. | D71 | after 5 |
| FR-SYNC-03 | "Open from GitHub" clones a linked investigation into a new folder and opens it. | D71 | after 5 |
| FR-SYNC-04 | Opening a linked investigation pulls first (fast-forward only) and pushes anything left unpushed. Without a connection it opens anyway and says so. | D71 | after 5 |
| FR-SYNC-05 | Closing the investigation, the Sync button and stopping the app commit everything and push. The header shows the sync state: up to date, not pushed, newer on GitHub, both changed, or not reachable. | D71 | after 5 |
| FR-SYNC-06 | When both this computer and GitHub have changes, nothing is merged. The user sees when and where each copy was last changed and keeps one; the other is saved (database in `backups/`, commits in the history or on a `set-aside/…` branch). | D71 | after 5 |
| FR-SYNC-07 | Before a schema upgrade of a linked investigation, the app asks for confirmation. | D71 | after 5 |
| FR-SYNC-08 | Sign-in is left to git's credential helper; the app stores no credentials, and a missing sign-in fails at once with a message saying how to sign in. | D71 | after 5 |
| FR-SYNC-09 | The import preview warns when a file over 100 MB is imported into a linked investigation. | D71 | after 5 |

**AC:** link an investigation to an empty bare repository, clone it into a second folder, change it there and sync, then open the first copy: it shows the change. Change both copies and sync: nothing is merged, the chosen copy is kept, and the other is saved.

## FR-NODE · Nodes and geometry

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-NODE-01 | The user can create a node with no required fields. Available fields: label, role, charge, multiplicity, status, step, branch, tags, notes, coordinates. | D7, D37, P1, WF-02 | 1 |
| FR-NODE-02 | Nodes have free-text notes (Markdown rendering is P11). | D37 | 1 |
| FR-NODE-03 | Coordinates can be viewed and edited as xyz text (element symbol and x y z in Å per line, with an optional count and comment header). Invalid lines are reported with line numbers and are not saved. | D20 | 1 |
| FR-NODE-04 | While a node has no calculations, saving edited coordinates updates the node in place, and saving empty text removes its coordinates (D90). | D23, ID-4, D90 | 1 |
| FR-NODE-05 | When a node has ≥1 calculation, saving edited coordinates creates a new node with `derived_from` set to the original, placed next to it on the canvas. The original's geometry and calculations are unchanged. | D23, ID-5, INV-6 | 1 |
| FR-NODE-06 | Composition (formula) is derived from the coordinates and is not editable separately. | P12 | 1 |
| FR-NODE-07 | The user can set a node's status to planned, running externally, done, failed, rejected or superseded. | D19 | 1 |
| FR-NODE-08 | The user can add and remove free tags. The system tag `optimization-incomplete` can be removed by the user, and the removal is recorded in history. | D26 | 1 |

**AC:**
- Paste valid 3-atom xyz into a new node: it saves, and 3D shows 3 atoms.
- Paste a line `Ru 0.0 abc 1.0`: the error names that line, and nothing is saved.
- Edit coordinates on a node with a single-point calculation: a new node appears with a "derived from" link, and the original's energy is unchanged.

## FR-CALC · Calculations and levels of theory

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-CALC-01 | A node holds any number of calculations. Each has a type (optimization, TS optimization, frequency, single point, conformer search, other), program, level of theory, results, source files and notes. | D15 | 2 |
| FR-CALC-02 | A level of theory is program + method + basis + dispersion + solvation (model and solvent). Two levels are equal only if all five match. | D30 | 2 |
| FR-CALC-03 | The user can register a named custom basis set, and map a parsed basis description to that name. Equality uses the name. | D35 | 2 |
| FR-CALC-04 | A single-point calculation records its geometry level: the level of the optimization on its node whose final geometry matches the single point's geometry (D100), or set by the user; with no match it has none until the user sets one. Its composite level is `SP level // geometry level`. The node's geometry level is that of the optimization its geometry came from; a lower-level pre-optimization continued by a higher-level one is not it (D100). | D31, D100 | 2 |
| FR-CALC-05 | The user can edit a calculation's level-of-theory fields and geometry level. Edits are recorded in history, and the original parsed values stay viewable. | D29, P13 | 2 |
| FR-CALC-06 | The user can inspect each saved custom basis set (elements, contraction, ECP, every exponent and coefficient, the calculations using it) and download it, or chosen elements, as a Gaussian basis file. | D94, A41 | after 5 |

**AC:**
- Two calculations differing only in solvent are different levels.
- A cc-pVQZ single point on a node optimized at cc-pVDZ shows the composite level `…cc-pVQZ // …cc-pVDZ`.
- The same cc-pVQZ single point on a node optimized at def2-SVP is a different composite level.

## FR-IMP · Import and parsing

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-IMP-01 | Import Gaussian output files. Extract: final geometry, charge, multiplicity, route (method, basis, dispersion, solvation), job steps, SCF energy, ZPE, thermal enthalpy and free energy with T and P, frequencies, imaginary count, normal or abnormal termination. | D8, 05 §3 | 2 |
| FR-IMP-02 | Multi-step Gaussian jobs (compound route, --Link1--): each step is recorded. The node geometry is the last geometry of the highest-level step, which is by default the last step. The chosen step is shown in the preview and can be overridden. | D25 | 2 |
| FR-IMP-03 | A failed or incomplete optimization imports its last geometry, and the node gets the tag `optimization-incomplete`. | D26 | 2 |
| FR-IMP-04 | Partial parsing: every field that could be read is imported. Missing fields are listed as warning W-PARSE on the calculation. The file is still copied. | 05 §3 | 2 |
| FR-IMP-05 | An import preview shows all parsed values and warnings before anything is written. Cancel writes nothing. | WF-04 | 2 |
| FR-IMP-06 | Import ORCA output files with the same kinds of results as FR-IMP-01, where ORCA provides them. | D8, A3 | 5 |
| FR-IMP-07 | Import xTB output files with the same kinds of results, where xTB provides them; a relaxed scan's `xtbscan.log` and a scan path's `path.xyz` import as a relaxed scan whose node takes the path's top (else its middle point). | D8, A3, D112, A58 | 5 (scans after 5) |
| FR-IMP-08 | Importing onto a node that has calculations: if the imported geometry does not match the node's (same atom order and elements, coordinates within tolerance), show W-GEOM and offer "create derived node" or cancel. | ID-7 | 2 |
| FR-IMP-09 | Duplicate check: if a new import matches an existing node's composition, charge and multiplicity, and its geometry is within the configurable tolerance after alignment, show "possible duplicate", with attach, new or cancel. Never merge automatically. | D47, Q32 | 2 |
| FR-IMP-10 | Import a CREST conformer ensemble. The preview lists conformers with energies. The lowest N are pre-selected (N from settings) and the user can untick any. The result is a group node with one member node per kept conformer and no representative. | D34, D46 | 5 |
| FR-IMP-11 | Members can be removed from a group node after import. Removal either keeps the member as a node (FR-GRP-10) or deletes it after confirmation. | D34, D88 | 5 |
| FR-IMP-12 | Importing onto a planned node with no calculations replaces its geometry, and the old geometry is kept in history. | D24, ID-6 | 2 |
| FR-IMP-13 | A frequency job can be imported onto an existing node at any time. If the file contains other steps, the preview lists every step with its geometry: steps matching the node's geometry are attached as calculations; from the first step that changes the geometry, the app offers a derived node. The preview flags a freq whose level differs from the node's geometry level. | D52, ID-7, EN-4 | 2 |
| FR-IMP-14 | Import a folder of output files at once: scan (optionally with subfolders), propose each file's node by geometry, by a planned node's guess, by file name or as a new node, review and retarget every file in one table, name custom basis sets once, and write all of them in one transaction with one history entry. | D97, A44 | later |
| FR-IMP-15 | In a batch import, files that name no basis set (ChkBasis from another job's checkpoint) are counted, and one basis set can be given for all of them at once, with a per-file override; the calculation records that the basis set was given at import. | D104, A51 | later |
| FR-IMP-16 | A Gaussian IRC output, or an optimization or frequency job run on one of its end points, is imported onto a transition-state node as part of its IRC (D131): the preview says the node does not change and warns about an IRC starting away from the TS, how each path ended, an abnormal end, an unconverged optimization, an end point with imaginary frequencies and another charge or multiplicity; a file with no IRC, or a node that is not a transition state, is refused with the reason. The parts never give the node a geometry, energy or level. Removing a file undoes its import. | D131, A76 | after 5 |
| FR-IMP-17 | An ORCA IRC output picked in its folder is imported onto a TS node's IRC with the forward and backward trajectories ORCA wrote beside it, which are copied with it; without them the import is refused and names them (D133) |
| FR-IMP-18 | An IRC file in the import dialog is offered to the transition states it starts from instead of the canvas; a batch import puts IRC files into the IRC of their TS; the IRC window imports a file from a server logged in to into a slot, with an ORCA IRC's trajectories (D134) |
| FR-IMP-19 | The energy of a Gaussian step with a correlated method (double hybrid, MP2, MP3, MP4, CCSD(T), QCISD(T)) is the method's own printed total, and the calculation names it (e.g. "E(B2PLYPD3)"); a correlated method whose total is not read has no energy and W-PARSE (D153). | D153, A97, 05 §3.1 | after 5 |
| FR-IMP-20 | A step a scan, frozen coordinates or frozen atoms held (Gaussian ModRedundant S/F and frozen atoms, ORCA `%geom Scan`/`Constraints`, xTB `$constrain`/`$scan`/`$fix`) imports with no suggested role and W-CONSTRAINED, and never gives a geometry level (D157) | Must |
| FR-IMP-21 | A Gaussian Counterpoise=N step's energy is its counterpoise-corrected total, with the BSSE kept and shown; an ONIOM step is at a level named by its layers and its energy is the extrapolated total; a counterpoise or ONIOM step cut off before its total has no energy and W-PARSE; older counterpoise imports are read again once (D161) | Must |
| FR-IMP-22 | ⟨S²⟩ is read from Gaussian (before and after annihilation) and ORCA (with its ideal value), kept with the calculation's results and shown with them; a calculation whose ⟨S²⟩ is more than 10 % from S(S+1) (a singlet: above 0.1) gets W-SPIN, which the values made from it carry into profiles, the energy table and the analyses; older imports are read again once (D162) | Must |

**AC:**
- An opt+freq output yields one node with 2 calculations (opt, freq), the geometry from the last step, and G, T and P shown.
- A truncated optimization yields a node tagged `optimization-incomplete` with W-TERM on the calculation.
- A file with the route line removed still imports its geometry, with W-PARSE listing method and basis.
- A CREST ensemble with 40 conformers and N = 10 gives a group with 10 members; unticking 2 gives 8.

## FR-FILE · Source files

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-FILE-01 | Every imported file is copied into the investigation's `files/` directory and linked to its calculation. The copy is never modified by the app. | D16 | 2 |
| FR-FILE-02 | Each copy records origin metadata: device or server name, original path, original file name, import time, and a content checksum. The name and path are user-editable. | D16 | 2 |
| FR-FILE-03 | The user can open or reveal a copied file from the calculation inspector. | D16 | 2 |
| FR-FILE-04 | The app does not depend on the original path being reachable. | D16 | 2 |

**AC:** after import, delete the original file. The calculation still opens its copy, and the origin path shows as recorded.

## FR-STEP / FR-BR / FR-EDGE / FR-GRP · Pathway structure

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-STEP-01 | The user can create, rename, reorder and delete reaction steps. Deleting a step unassigns its nodes. | D3, P2 | 3 |
| FR-STEP-02 | A node may be assigned to at most one step. | P2 | 3 |
| FR-STEP-03 | Each step has notes. | D45 | 3 |
| FR-BR-01 | The user can create a branch with a name, colour, parent branches and status. | D12, D19 | 3 |
| FR-BR-02 | "Split into branches" on a node creates N child branches whose parent is that node's branch, and records the split node. | D12 | 3 |
| FR-BR-03 | A node belongs to at most one branch. Membership changes only by explicit user action. | P5, INV-3 | 3 |
| FR-BR-04 | The inspector shows a branch's full lineage (chain of parents) and child branches. | INV-1 | 3 |
| FR-EDGE-01 | The user can create a directed transition between any two nodes or group nodes, with status and notes. | D3 | 3 |
| FR-EDGE-02 | Transitions between nodes of different branches are allowed and do not change membership. | D17, INV-4 | 3 |
| FR-EDGE-03 | A transition where neither endpoint is a TS-role node is a **direct connection**. It is drawn dotted with a "no TS" marker on the canvas, and is listed as such in the overview and the inspector. | D53 | 3 |
| FR-GRP-01 | "Create group" creates a group node from selected nodes and records the incoming branches. It starts no branch (D152); an outgoing branch whose parents are the incoming branches can be set by hand, or passed through the API. | D13, D18, D152 | 3 |
| FR-GRP-02 | The user marks at most one member as representative. The app never sets it automatically. | D18, EN-10 | 3 |
| FR-GRP-03 | Members can be sorted by energy at the current view level and type. | D18 | 4 |
| FR-GRP-04 | Transitions into a group node are optional. | D14 | 3 |
| FR-GRP-05 | Deleting a group offers **dissolve** (members kept as nodes; branch per P4) or **delete with contents** (group, members, their calculations and edges removed, after a confirmation listing them). | P4, D54 | 3 |
| FR-GRP-06 | Nodes can be added to an existing group: select the group and the nodes together and choose **Add to group** (A20). | D65, A20 | after 5 |
| FR-GRP-07 | A button in an expanded group cycles its members through a grid, a vertical line and a horizontal line; the choice is kept with the group (A21). | D65, A21 | after 5 |
| FR-GRP-08 | Group members keep their branch, so a group can hold members from several branches; a group left with one member by the filters is drawn as that node (D66). | D66, A22–A26 | after 5 |
| FR-GRP-09 | In the structure view mode, a collapsed group shows its representative's structure; with no representative it shows none (D68). | D68 | after 5 |
| FR-GRP-10 | A member can be taken out of its group and kept as a node beside the group, with its calculations and edges and its branch (or the branch it came from). If it was the representative, the group has none until the user picks one. The group's last member cannot be taken out; the group is dissolved instead (D88). | D88 | after 5 |
| FR-GRP-11 | A group's members are kept in an order the user sets. A button beside the layout button of an expanded group opens the list of members with arrows to move each up or down, like the reaction steps; the grid, the column and the row all follow the order. A member added later comes last (D89). | D89 | after 5 |

**AC (lineage):** build the §6 example of [02](02-domain-model.md). A1's inspector shows lineage A1 → A → T, and R shows parents A1, A2, B1, B2. Adding the A-S3↔B-S3 rotation transitions leaves every node's branch unchanged. There is no action in the UI that creates a path across steps without edges.

## FR-EN · Energies

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-EN-01 | The view has one level selector (composite levels available in the investigation) and one energy-type selector (E, H, G, G_qh). Only combinations with at least one value are listed. | D32 | 4 |
| FR-EN-02 | Each edge shows ΔX = X(target) − X(source) at the selected level and type, or "n/a". There is no fallback to other levels or types. | D27, EN-3 | 4 |
| FR-EN-03 | G at a composite level follows EN-4. If no frequency calculation exists at the geometry level, G is "n/a" with W-NOFREQ. | D35 | 4 |
| FR-EN-04 | G_qh follows EN-5 and EN-6: recomputed from the selected frequency step with Truhlar's raise on vibrational entropy only, at a configurable T and cutoff (defaults 298.15 K, 100 cm⁻¹) recorded with the value, reproducing Jonas's reference script. The job-printed G is shown alongside for comparison. Linear molecules use the linear rigid rotor (D164). G and G_qh can be put at the 1 M standard state in Settings (off by default), which the energy view names. | D41, D49, D56–D58, D95, D164 | 4 |
| FR-EN-05 | Energy profile: for a pathway chosen by the user along existing edges, plot X(n) − X(ref) against position, with a user-chosen reference node. Several branches can be overlaid in branch colours; a node they share at the same energy is drawn in the text colour. A branch's pathway includes the nodes on no branch it starts from or runs into along its edges. A pathway carries on across edges drawn to or from a group as well as node to node: a group's edges count for its members and its members' edges for the group, and an edge into a group offers the group and each member. | D39, D67, D108, D111, EN-8, INV-2 | 4 |
| FR-EN-06 | Energy table: for the viewed branch or pathway, one row per node with label, step, branch, composite level, E, G (and G_qh), the free species added or subtracted (D69), and ΔX from the reference. Exportable as CSV. | D44, D69 | 4 |
| FR-EN-07 | The display unit is kcal/mol by default and changeable in settings (at least kcal/mol, kJ/mol, hartree, eV). | D28, P14 | 4 |
| FR-EN-08 | Energy values are never user-typed. They come only from calculation results. | EN-1 | 4 |
| FR-EN-09 | In energy profiles, a direct connection is drawn as a dotted connector labelled "no TS" and never as a barrier. The profile style can hide the "no TS" tag; the connector stays dotted. A group whose representative is a TS counts as a TS. | D53, D108, D111, INV-8 | 4 |
| FR-EN-10 | The energy profile's style (size, font, colours, levels, connectors straight or curved, label placement, decimals, title, legend, grid, axis, background, PNG resolution) is set in one dialog with a live preview and presets "Screen" and "Publication". It applies to every profile drawn and to its PNG and SVG. The title and the legend never overlap: the legend moves below the title where they would. Node names go above or below each level, along the bottom of the profile, or nowhere. | D106, D108, D109 | 4 |
| FR-EN-11 | The profile in the app can be enlarged: the drawer's height is dragged, the pathways list can be hidden, and the profile zooms (Fit, 50 % to 400 %, Ctrl + wheel) and is dragged about when zoomed. | D106 | 4 |
| FR-EN-12 | Each energy value says when the frequency calculation at its geometry level does not show the node to be what its role says: a TS without exactly one imaginary frequency or without a frequency calculation, a minimum with one. Profiles mark the point (⚠ with the reason, on screen only), the energy table and CSV have a "Check" column, and a selectivity or turnover lists each such TS or intermediate in its notes. Nothing is left out or changed. | D154, A98 | after 5 |
| FR-EN-13 | Every frequency calculation a node's energies use (the latest at each composite level) is checked against its role, and a node card's warning names the level, so a good job at one level never hides a wrong one at another. A frequency calculation the program's own check finds not at a stationary point is W-FREQ-NONSTAT. W-FREQ-NONSTAT, W-OPT-INC and W-TERM of the calculations behind a value go with it, as FR-EN-12's checks do. A group card shows a ⚠ when any member has warnings, listing them. No status or value changes. | D158, A102 | after 5 |
| FR-EN-14 | The G_qh temperature and cutoff and the standard state of G and G_qh are the investigation's own: stored in it, synced with it, and each change recorded in its History. Every G and G_qh value, profile, table, selectivity, turnover, IRC end point, read-only copy and Claude's tools use the open investigation's values. Settings shows them as belonging to the open investigation; the app's values are the default for new investigations, and an investigation saved before this takes them once when opened. | D159, A103 | after 5 |
| FR-EN-15 | G read from a file is at its frequency job's temperature: its 1 M shift is taken there, and its label names that temperature ("G (1 atm, 353.15 K)"), while G_qh names its own ("G_qh (298.15 K, 100 cm⁻¹, 1 atm)"). Every label of G and G_qh, table header, CSV and profile title names the standard state, 1 atm included. W-TEMP marks H and G read at another temperature than G_qh's, and views where values from different job temperatures meet; W-STDSTATE marks free species joining or leaving at 1 atm in a solvation model. A free species may be given its own concentration (the investigation's standard state, 1 M, a neat solvent from a built-in table or with its molarity, or any molarity), recorded in the history and added as RT ln(c / 1 M) on top of the 1 M shift to its G and G_qh wherever it balances a pathway. | D160, A104 | after 5 |
| FR-EN-16 | "With BSSE", off by default and offered only at a level where a node has a counterpoise job, adds each such node's BSSE at that composite level to its E, H, G and G_qh on the canvas, the profile and the energy table (titled "… with BSSE", with a BSSE column in the table and CSV); without it a counterpoise job counts as the uncorrected complex, so no value changes unless asked. | D161, A105 | after 5 |

**AC:**
- Nodes X and Y have G at level L1, and Y also at L2. With L2 selected, the edge X→Y shows "n/a".
- With E selected, a node without a frequency calculation still shows values. With G selected, it shows "n/a".
- G_qh for a structure with no frequencies below 100 cm⁻¹ equals the job-printed G to within rounding.
- Changing the unit changes the displayed values, while the stored values stay the same.
- A profile over A1-S1 → A1-S3 (direct) shows a dotted "no TS" segment with no peak.

## FR-SPC · Free species and mass balance

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SPC-01 | The user can create a free species by hand or by import. It has the fields, calculations, source files, energies, 3D view and history of a node, is listed under "Free species" rather than drawn on the canvas, and deleting it takes it off every transition it is on (listed first). | D69 | after 5 |
| FR-SPC-02 | A free species has no step, branch, group or transitions of its own. | D69 | after 5 |
| FR-SPC-03 | A transition lists free species that join or leave on it, each with a count, shown as chips on the edge ("+ propene", "− C₂H₄"). Changes are recorded in the history. | D69 | after 5 |
| FR-SPC-04 | A node with no transitions and no group can be turned into a free species (it leaves its step and branch), and a species on no transition back into a node. | D69 | after 5 |
| FR-SPC-05 | The import dialog can create a free species instead of a node. A node derived from a species is a species. | D69 | after 5 |
| FR-SPC-06 | Profiles, the energy table, node cards in energy mode (along the route in D72) and ΔX on edges add the energy of leaving species and subtract that of joining species along the pathway from the reference (EN-3, EN-8). The table has a "Free species" column. A species without a value at the selected level makes the point, card or edge "n/a" with the reason. | D69, D72 | after 5 |
| FR-SPC-07 | W-BALANCE flags a transition whose atoms or total charge do not balance. | D69 | after 5 |

**AC:**
- A → B with "+ propene": B's profile value is X(B) − X(A) − X(propene).
- TS → C with "− ethylene": C's value is X(C) + X(ethylene) − X(A) − X(propene), and ΔX on the edge is X(C) + X(ethylene) − X(TS).
- Without the "− ethylene" chip, the edge TS → C shows W-BALANCE naming C2H4.

## FR-CAN · Canvas

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-CAN-01 | Infinite pan and zoom canvas. Node positions persist per investigation. | D2 | 3 |
| FR-CAN-02 | Nodes and edges are drawn in their branch colour, with status badges and warning icons. | D33 | 3 |
| FR-CAN-03 | Energy labels on edges can be shown or hidden. | D33 | 4 |
| FR-CAN-04 | View modes: compact, energy, structure. Structure mode shows ΔX below each structure once a reference is chosen, and draws each structure in the orientation saved from its 3D view. | D40, D43, D73, D74 | 3 (energy mode in 4) |
| FR-CAN-05 | Filters by branch, status and step. Filtering never alters data. | D33 | 3 |
| FR-CAN-06 | "Arrange branch" lays out one branch left to right in step order. Other positions are unchanged. | D33 | 3 |
| FR-CAN-07 | Export a canvas image (viewport or full canvas). | D39 | 3 |
| FR-CAN-08 | An investigation opens on what it showed when last used: energy level, energy type, reference, energies on edges, filters, expanded groups, and the energy drawer with its pathways. Saved in the investigation, not in the history. | D105 | 4 |
| FR-CAN-09 | A scan path is shown on the edge it was run on as a chip with the path's own top (xTB, from its first kept point), which opens the path's chart and movie in the edge panel; the path node can be shown on its edge only. The chip never feeds any energy of the edge. | D121 | 4 |
| FR-CAN-10 | An edge whose ends are on the same side of two boxes lined up on that side is drawn bowing out from that side, not as a straight line along the boxes. | D147 | 4 |

## FR-3D · 3D viewer

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-3D-01 | Show the selected node's geometry with rotate, zoom and pan; save the orientation for the structure card; hide hydrogens by a setting; pop the view out into a movable, resizable window and put it back. | D20, D74, D75, D82, A33 | 1 (pop-out after 5) |
| FR-3D-02 | Measure distances and angles between picked atoms. | D20 | 3 |
| FR-3D-03 | Animate any vibrational mode from a frequency calculation, with imaginary modes listed first. | D20 | 3 |
| FR-3D-04 | Overlay 2 to 12 selected geometries, or a group's members, on one reference: aligned on all atoms, on chosen atoms (a list per structure where the numbering differs), or not aligned; optionally allowing a mirror image. Each has its own colour, a visibility switch and its RMSD over the alignment atoms and over all atoms. Save the image and a multi-structure .xyz. | D20, D80, A31 | 3 (choices after 5) |
| FR-3D-05 | No geometry editing in the viewer; structures are changed in the Edit structure window (FR-3D-17). | X3, D139 | — |
| FR-3D-06 | Copy xyz to the clipboard and save a .xyz file from the node inspector. | D39 | 1 |
| FR-3D-07 | Named alignment sets keep each node's alignment atoms in the investigation, to reuse and extend. | D80, A31 | after 5 |
| FR-3D-08 | Play the structures of an optimization or scan as a movie in the node's 3D view, with each structure's energy; for a scan, optionally only the converged structure of each point. Take the structure shown as the geometry: in place on a scan path node, otherwise on a new derived node. Remove points or sections from a scan path and put them back, the file unchanged, the top chosen again over the kept points and each cut's jump shown. | D101, A48, D112, A58, D117, A63 | after 5 |
| FR-3D-09 | Match the atoms of two structures of the same atoms numbered differently, review the match side by side with the bonds that form or break, fix pairs by hand, and save the second renumbered in the first's order. | D113, A59 | after 5 |
| FR-3D-10 | The step movie shows, for the structure shown, the convergence criteria the optimizer printed for it (Gaussian's maximum and RMS force and displacement with the predicted energy change, ORCA's energy change, RMS and maximum gradient and step, xTB's energy change and gradient norm against its optimization level's thresholds) with value, threshold and whether each is met, and each criterion over all structures on a log scale with its threshold dashed; for calculations imported earlier too, nothing stored. | D123, A70 | after 5 |
| FR-3D-11 | An Inspect window reads an output or xyz file without importing it, from the import dialog's file browser, from "Import from here" and from `inspect FILE` in a server's terminal: the job steps (route, charge and multiplicity, how each ended, last SCF energy, imaginary frequencies), each step's structures in the 3D view with the step movie's player, energy chart and convergence criteria, and the normal modes (imaginary first). Refresh reads the file again (copied again from a server) and keeps the step and structure shown, also every 30 s when ticked; a file still being written is read up to its last whole block. "Import this file" goes on to the import preview with the same copy. Nothing is written to the investigation or the server, and a server file's copy is deleted when the window closes. xyz files (one or many structures, a CREST ensemble) and xTB's `xtbopt.log` (each cycle with its energy change and gradient norm) open too. A relaxed scan can be shown by its converged points only, one structure per scan point, as the step movie can; it opens that way (D125). Several files (chosen in "Import from here", matched by a name filter in the file browser, or named by one `inspect` command) open as one list, stepped through with Previous and Next file or picked from the list, each read when shown (D129). Copy XYZ puts the structure shown on the clipboard as xyz text, its comment line naming the file, step, structure, scan point and energy (D130). | D123, A70, D125, D129, A74, D130, A75 | after 5 |
| FR-3D-12 | A TS node's IRC window (IRC… in its panel, with one line on what it holds) shows each direction's path files and end-point jobs with how they ended and Import…, Remove… and Every structure; the path through the TS against the mass-weighted distance along it with the TS, the IRC ends and the optimized end points marked; the 3D view playing the path's points (or every structure of one file) with Copy XYZ; and a table of ΔE, ΔG and ΔG_qh relative to the TS for each IRC end and optimized end point. | D131, A76 | after 5 |
| FR-3D-13 | The IRC window says for each direction where it leads among the TS's neighbours on the canvas (reaches Int-2 ✓, reaches none of them with the nearest named, or no edges to check), on which structure it judged and over how many reacting atoms, with each neighbour's bonds and RMSDs on request; orders its columns, chart and energies by the edges; names a node that is the same structure as an optimized end point and otherwise offers Make it a node; a named node can be shown on the canvas (D132) |
| FR-3D-14 | A scan editor (Scan editor in the start page and header, the file browser's name filter, the Inspect window and an imported scan's step movie) reads scans from files without importing them, lists each with its scanned coordinate (named by the file, else found from the structures), range, points and level, draws them on one chart against the coordinate, and joins pieces of them into one path: added the right way round where they meet the path (files added together chained), reversed, trimmed, split, moved, removed, with undo; refuses pieces at different levels or with other atoms; marks each join (the shared structure kept once, its two energies, a jump, an energy step, turning back); plays the path with Copy XYZ and Set as zero; and saves it as an xyz file with its level, energies and sources, which it opens again as one scan (D135) | D135, A80 | after 5 |
| FR-3D-15 | The scan editor offers a finer scan of the path's coordinate in place of the coarser points it covers (or of a stretch chosen on the energy chart), lists branches whose energies cross and joins the lower on each side at the crossing, compares the scans' points at the frame shown fitted onto it, and renumbers a scan whose atoms are in another order (D136) | D136, A81 | after 5 |
| FR-3D-16 | A joined scan saved by the scan editor imports as a scan path node at the level of its pieces (a custom basis set named by its fingerprints), plays and trims as one, shows on an edge with its program on the chip, never gives energies to profiles, the energy table or analyses, and is imported from the editor onto the canvas or an edge (D137) | D137, A82 | after 5 |
| FR-3D-17 | Edit in 3D… opens the Edit structure window: atoms picked by clicking or typing; a distance, angle or dihedral set by moving one side (the smaller by default; only the end atom where the sides are joined); turning about a bond; adding and removing bonds for which atoms move together; adding an atom, replacing a terminal atom with a fragment, changing elements, deleting atoms; another molecule or node put beside; undo and redo; saved in place, as a derived node or as a new node. | D139, A84 | after 5 |
| FR-3D-18 | In the Edit structure window: freeze picked atoms and hold a picked distance, angle or dihedral at a typed value, listed and changeable; Minimize with a built-in UFF clean-up that keeps metals such as Ru in shape, moving the changed atoms and two bonds round them, or everything; Minimize again with nothing changed moves nothing, and atoms are kept off a metal; undoable, nothing stored before saving. | D140, A85, D145, A90 | after 5 |
| FR-3D-19 | In the Edit structure window: start from no atoms with a lone atom, add hydrogens to picked atoms up to their usual or a typed number of bonds, add a molecule from SMILES, and replace an atom with a fragment from SMILES with one `*` where it attaches. | D141, A86 | after 5 |
| FR-3D-20 | In the Edit structure window: draw a molecule, or a fragment with an attachment point, in 2D with Ketcher (also from ChemDraw CDXML, MOL or SMILES), and convert it to 3D as a molecule beside the structure or in place of the picked atom, keeping charges, drawn stereo and which ligands are drawn trans round a metal. | D142, A87 | after 5 |
| FR-3D-21 | In the Edit structure window: open the structure itself in the Ketcher drawer, change it in 2D, and put it back with unchanged atoms where they were in 3D and their numbers, new atoms built round them, removed atoms deleted, and the changed part minimized. | D143, A88 | after 5 |
| FR-3D-22 | In the Edit structure window: Minimize optimizes main-group heavy atoms UFF types (Al, Tl, Sb, Bi, Te, Hg), and offers to relax metals: bonds to UFF's lengths, angles to the nearest ideal shape, saying which shape each metal took or why it was held. | D146, A91 | after 5 |

## FR-STER · Buried volume and steric maps (D81)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-STER-01 | Named steric profiles keep the sphere radius, radii and their scale, whether hydrogens count, the mesh and the map's colour scale, and per node the centre (an atom or a centroid), the orientation (z-axis and xz-plane atoms) and the atoms left out. | D81, A32 | after 5 |
| FR-STER-02 | A node's atoms are typed or picked in the 3D view, with helpers that leave out one side of a bond or everything but one ligand. A node with the same elements in the same order can use another node's atoms. | D81 | after 5 |
| FR-STER-03 | The node inspector shows %V_bur and, with an orientation, the quadrants, octants and steric map, always with the profile's settings. | D81, A32 | after 5 |
| FR-STER-04 | Each result is stored with the inputs it came from and marked out of date, with the reason, when the coordinates, the profile's settings or the node's atoms change. | D81, A32 | after 5 |
| FR-STER-05 | Compare a branch, a pathway, a group or a selection: a table and a CSV with the settings and atoms on every row, maps side by side on one colour scale, a difference map of two nodes, and map images. | D81, A32 | after 5 |
| FR-STER-06 | Choose the steric maps' colours (blue, green–yellow–red, rainbow, viridis or grey) in the settings or beside the maps; difference maps stay blue–grey–red. | D84 | after 5 |

## FR-SEL · Selectivity (D83)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SEL-01 | Saved selectivities have two or more named outcomes; each outcome is one or more TS nodes or groups, and a group counts with all its members. | D83, A34 | after 5 |
| FR-SEL-02 | Every TS is valued at one composite level and energy type with no fallback; a missing value makes the result n/a with the reason. TSs with different atoms or charges, or a TS in two outcomes, are refused with the reason. | D83, EN-3 | after 5 |
| FR-SEL-03 | An outcome's weight is the Boltzmann sum over its TSs (Curtin–Hammett) or only its lowest TS's; both are computed, the chosen one leads. | D83, A34 | after 5 |
| FR-SEL-04 | The temperature is the G_qh setting or the selectivity's own; G_qh is recomputed at it from the stored frequencies, E, H and G are used as read and the result says so. | D83, A34 | after 5 |
| FR-SEL-05 | The result shows each outcome's ΔΔG‡ and predicted percentage, the ee or de for two outcomes, each TS's ΔG and share, and an optional experimental ratio with its ΔΔG‡. | D83, A34 | after 5 |
| FR-SEL-06 | An Analyses view lists the selectivities; one is made there or from a canvas selection. Creating, changing and deleting one is recorded in the history. | D83, A34 | after 5 |
| FR-SEL-07 | A selectivity can name a reference node or group; each TS is then balanced by the free species along its route from it (D72), their energies added at the same level and type with no fallback, and the atoms and charge compared include them. A TS not joined to the reference is refused; a species without a value makes the result n/a. | D103, A50 | after 5 |
| FR-SEL-08 | Rejected, failed and superseded TSs are left out of a selectivity, and a member that duplicates an earlier one of its outcome (same atoms, charge and multiplicity within the duplicate tolerance, and the same value within 0.1 kcal/mol) counts once; the notes name each. | D156, A100 | after 5 |

## FR-TOF · Turnover (D86)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-TOF-01 | Saved turnovers hold one closed pathway (A13); the cycle runs from the node the pathway returns to, and points before it are left out with a note. | D86, A36 | after 5 |
| FR-TOF-02 | Every point is valued at one composite level and energy type with no fallback, relative to the cycle's first node and balanced by its free species, so the closing point is ΔG_r; a missing value makes the result n/a with the reason. | D86, EN-3, D69 | after 5 |
| FR-TOF-03 | The TOF comes from the full energetic-span formula; each TS's and intermediate's degree of TOF control is given, the TDTS and TDI are the largest, and δE is reported. A TS is a node (or a group's representative) marked as one; a "no TS" connection makes the TOF an upper bound, with a note. | D86, A36 | after 5 |
| FR-TOF-04 | The temperature is the G_qh setting or the turnover's own, with G_qh recomputed at it. The result shows the TOF (s⁻¹, h⁻¹), δE, ΔG_r, the cycle's profile with the TDTS and TDI marked, and the degree-of-control table with a CSV export. | D86, A36 | after 5 |
| FR-TOF-05 | A turnover can be compared with another at the same level, energy type and temperature, as percentages and an effective ΔΔG‡. Turnovers are listed in the Analyses view and made there or from a closed pathway in the energy drawer; creating, changing and deleting one is recorded in the history. | D86, A36 | after 5 |

## FR-NOTE · Notes pinned to node cards (D85)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-NOTE-01 | A node can carry several notes, each pinned to one corner of its card that the user picks; a note moves with the node, can be collapsed to its title, and has a colour and a width. | D85, A35 | after 5 |
| FR-NOTE-02 | A note's text can be formatted (bold, italic, underline, strikethrough, sub- and superscript, lists, links); anything else pasted in keeps only its text. | D85 | after 5 |
| FR-NOTE-03 | Pictures (PNG, JPEG, GIF, WebP, SVG) are pasted, dropped or chosen into a note. SVG, also SVG markup pasted as text, is stored as SVG without scripts, event handlers, embedded documents or outside links; nothing in a note runs when shown. A paste with nothing a browser can read says how to bring a ChemDraw drawing in. | D85, A35 | after 5 |
| FR-NOTE-04 | Creating and deleting a note and changing its text, title, corner or colour are recorded in the node's history. | D85, A35 | after 5 |
| FR-NOTE-05 | Notes show in full in the node's side panel, and on the canvas, in the side panel and with their pictures in the read-only copy. | D85, D79 | after 5 |
| FR-NOTE-06 | A note can be detached to float apart from the card, joined to its corner by a line or not, moving with the card; it is dragged by its head and put back on its corner with its pin. An expanded note is resized from its outer corner. | D87, A37 | after 5 |

## FR-PANEL · Claude panel (D92)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-PANEL-01 | A "Claude" button opens a resizable panel docked beside the notebook with a terminal running the official `claude` CLI on this computer; hiding the panel keeps it running. | D92, A39 | after 5 |
| FR-PANEL-02 | Only `claude` is started, never a shell, in an app-managed working folder per investigation, with the `chembook3d` MCP server (D91) as its only MCP server, the notebook's read tools pre-approved, its other tools asking, and shell, file editing and web tools denied. "Continue last conversation" resumes the investigation's last one. | D92 | after 5 |
| FR-PANEL-03 | The terminal accepts a connection only while the app listens on 127.0.0.1, from this computer, for a loopback host name and page origin, with a one-time token only the app's page can read. | D92, NFR-SEC-01 | after 5 |
| FR-PANEL-04 | Without `claude` installed, the panel shows how to install it and sign in; without the notebook tools it says Claude can only answer from what it is told. | D92 | after 5 |

**AC:** with Claude Code installed and signed in, open the demo, "Claude", "New conversation": Claude Code starts in the panel; asking it to run a shell command is refused; hide and show the panel and the conversation is still there.

## FR-CLOUD · Calculations in Claude Code cloud sessions (D93)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-CLOUD-01 | Claude can write a calculation job into the investigation, `jobs/<date>-<name>/`: the chosen nodes' coordinates as XYZ inputs with their charge and multiplicity named, other input text files with checked names, `job.md` (what to run and return) and `job.json`. Nothing leaves the computer when a job is written. | D93 | after 5 |
| FR-CLOUD-02 | Starting a job commits only its folder and the cloud files (`.claude/settings.json` with the SessionStart hook, `.claude/chembook3d/setup-tools.sh`, `.claude/CLAUDE.md`), pushes them to the linked repository and runs `claude --permission-mode auto --cloud` in the investigation folder, in a pseudo-terminal (Claude Code creates a cloud session only from a terminal), with a task naming only the job. A job already pushed by a start whose launch failed is started again without a new commit. It is refused when the investigation is not linked, GitHub has a newer version, or Claude Code is not installed; a job with a session is not started twice. | D93, D71, A40 | after 5 |
| FR-CLOUD-03 | The job's state is draft, starting, waiting_for_answer (Claude Code asks something before it starts, such as whether the folder is trusted; the app never answers it, the user does in the Claude panel, which opens with the launch's screen), launch_failed (with what Claude Code said or showed and what to do; it can be started again), running (with the session's link), finished (its `result.json` is on a branch of the repository) or fetched. Claude can wait for a result for up to 300 s per call. | D93, A40 | after 5 |
| FR-CLOUD-04 | Fetching copies only the job's `outputs/` and `result.json` from the result's branch into the local job folder (names Windows cannot hold are skipped), answers with the local paths, and lists files the session changed outside its job folder; Claude imports the outputs like any other output file. | D93 | after 5 |
| FR-CLOUD-05 | The SessionStart hook does nothing outside a cloud session; in one it installs xtb 6.7.1 and crest 3.0.2 from their pinned releases after checking their SHA-256. The cloud instructions forbid touching the database, `files/`, `backups/`, `.claude/` and other jobs. | D93 | after 5 |
| FR-CLOUD-06 | Creating, starting, messaging and fetching a job are refused from a web page of another origin. | D93, NFR-SEC-01 | after 5 |
| FR-CLOUD-07 | The cloud instructions name the investigation's GitHub repository (its https address, without any user name or token) and tell the session to add it as `origin` when it has none, which is the case when Claude Code uploaded the folder because the Claude GitHub App is not set up for the repository. The app notices that upload on the launch screen and says why the results may not come back; Claude can send the job's session a message (`claude -p --cloud <session>`, the text on its input), for one to push again once the app is installed. | D93, A40 | after 5 |
| FR-CLOUD-08 | "Scan path…" on two nodes or groups joined by an edge checks the ends, matches their atoms, suggests the coordinates to hold at a TS end (from its imaginary mode, or ticked by the user for a guess), and sends a cloud job whose session designs and runs GFN2-xTB relaxed scans from one end to the other and returns the best path as `path.xyz`, with the helper `pathtools.py`. | D114, A60 | after 5 |
| FR-CLOUD-09 | "Cloud jobs" lists the investigation's cloud jobs with their status, session link and actions (start, check, fetch, import a path); while the investigation is open, a finished scan path is imported by itself as a new node between its ends, which can be undone. | D115, A61 | after 5 |
| FR-CLOUD-10 | A scan path job sends a TS end's imaginary mode when it has a frequency job, and its brief explains xTB's scan input, carries driven coordinates across stages, drives held TS coordinates from the TS's values to the other end's, tries the path downhill from the TS first and stops after a budget; `pathtools.py` measures dihedrals as xTB does, numbers atoms from 1 and reads scan logs and modes; a path that misses the quality check is imported with the reasons in its notes; the dialog warns when no bond forms or breaks. | D116, A62 | after 5 |
| FR-CLOUD-11 | A scan path's end is judged on the reacting atoms listed in `path.json` (`active`), with the all-atom RMSD reported and both named in the imported node's notes; the helper lists turning bonds with a concerted scan block, checks each stage against its scan input, converts xTB's path finder output to Eh, and the session may return `outputs/feedback.md`. | D118, A64 | after 5 |
| FR-CLOUD-12 | The Scan path dialog takes the user's own coordinates to drive (atoms, from, to; together or in order), which the job sends as `drive` and the brief runs first as given before the session's own strategies; `pathtools.py check` says whether a path followed them, the result reports `user_strategy` and the notes say whose design the path is; `pathtools.py diff` lists the turn of a ligand bound side-on (η²) to a metal. | D119, A65 | after 5 |
| FR-CLOUD-13 | A scan path runs along an edge where one free species joins or leaves: the species is matched across the ends with the other end beside it, and the app builds the separated end by pulling the species straight out of the bound end to a set closest contact, shown in the dialog beside the bound structure. | D120, A66 | after 5 |
| FR-CLOUD-14 | A scan path job with a species that joins or leaves sends the separated end as one of its ends, the species in `path.json` and the complex and species each alone; the brief pulls the species out from the bound end first; `pathtools.py check` judges the separated end on the complex and the species' closest contact and reports the leftover interaction; the import notes name the species. | D120, A67 | after 5 |

**AC:** with the demo linked to an empty private repository and Claude Code signed in, ask Claude in the panel to optimise a node with GFN2-xTB in the cloud: it shows the job, starts it when you agree and gives a claude.ai link; when the session has pushed its result, Claude fetches and imports the output, and the node has an xTB calculation.

## FR-SHARE · Read-only copy to share (D79)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SHARE-01 | "Share read-only copy…" saves the open investigation as one self-contained HTML file that opens offline in a browser, with no server or install. | D79 | after 5 |
| FR-SHARE-02 | The file shows the canvas with its view modes and filters, the details, notes and calculations of every record, the 3D view with measuring and the included vibrations, and the energies, profile and table at every level and energy type (A30). | D79, A30 | after 5 |
| FR-SHARE-03 | Nothing in the file can be edited, and it makes no network request. | D79 | after 5 |
| FR-SHARE-04 | The file holds no local paths (investigation folder, original paths and devices of imported files), no copied output files and no change history. | D79 | after 5 |
| FR-SHARE-05 | The file opens on the view the app showed when it was made: node view, level of theory, energy type, energies on edges, filters, expanded groups and the energy drawer, besides the reference and pathways (A30); what a reader then chooses is remembered in their browser for that copy. | D128, A73 | after 5 |

**AC:** export the demo investigation, open the file with the server stopped: the canvas, a node's calculations and 3D view, and a profile at another level show, nothing can be edited, and the file contains no folder path.

## FR-MCP · Claude in the notebook (D91)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-MCP-01 | `chembook3d mcp` is an MCP server (stdio) that works on the investigation open in the running app, only through the app's API on this computer; it never opens the database. When the app is not running or nothing is open, its tools say so. | D91, NFR-SEC-01 | after 5 |
| FR-MCP-02 | Its tools read everything the app shows and change what the app can change, through the same rules and history; opening, closing, creating and cloning investigations, sync, folder browsing, opening files with other programs, settings and the read-only copy are not offered. Imports take a path the user names and are previewed before they are committed. | D91 | after 5 |
| FR-MCP-03 | Every delete, dissolving a group and removing coordinates is a request: the app shows what would go and Claude's reason, and does it only on the user's Confirm; Refuse, closing or no answer within the time limit changes nothing (A38). The server cannot answer a request. | D91, A38 | after 5 |
| FR-MCP-04 | Open tabs reload what they show when the investigation is changed elsewhere, without reloading the page. | D91 | after 5 |
| FR-MCP-05 | The tab reports what is selected and the energy level, type and reference shown; Claude reads it with names. | D91, A38 | after 5 |
| FR-MCP-06 | Changes made through the server are marked "claude" in the history. The server's instructions state the domain rules (steps are not transitions, one composite level, X1 with D93's cloud jobs, X5). | D91 | after 5 |
| FR-MCP-07 | Claude builds and changes structures through the Edit structure window's tools as a list of steps (atoms from 1), checks them (formula, electrons, measurements, each metal's bonded atoms, clashes) and saves with `set_coordinates`; neither tool stores anything. | D144, A89 | after 5 |
| FR-MCP-08 | Claude saves and goes on from a built structure by its id, never copying coordinates; a molecule or SMILES binds at a metal's open site at a given distance; hydrogens can be added by exact count; each step says what it bonded or crowded without being asked, a refused move names the other side, and the node list is short unless coordinates are asked for. | D148, A93 | after 5 |
| FR-MCP-09 | A molecule or atom bound to a metal goes where it has room or is refused with what is in the way and where it fits; Minimize keeps a placed ligand at its length and angles; clashes drawn as bonds are reported; every tool shows its optional fields; the node list for Claude is brief. | D149, A94 | after 5 |
| FR-MCP-10 | Claude can turn a set of atoms rigidly about an axis given by atoms or their middles, or by the smallest turn that lines one vector up with another at a set angle; the answer states the sign convention, the bonds to the rest before and after, and new close contacts. | D150, A95 | after 5 |
| FR-MCP-11 | Claude can measure distances, angles, dihedrals and vector angles across nodes and groups in one call, with each row's geometry level and convergence, per-column statistics, numbering by a reference node or SMARTS, and filters by level, convergence and value. | D151, A96 | after 5 |

**AC:** with the app open, Claude Code renames a node: the canvas shows the new name at once and the history marks it "claude". Asked to delete it, Claude waits; Refuse in the app keeps the node, Confirm deletes it.

## FR-SSH · SSH servers such as Saga (D122)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SSH-01 | The side panel lists the saved SSH servers (Saga first) with host, port and user name, which can be changed and added to; nothing secret is saved. | D122, A69 | after 5 |
| FR-SSH-02 | "Log in" shows the server's prompts one after the other (hidden input where the server asks for it) and sends the answers once; a refused login says so and is not tried again by the app; an unknown host key is shown with its fingerprint to trust or cancel, and a changed one is refused. The password and code are never written to disk. | D122, A69, NFR-SEC-03 | after 5 |
| FR-SSH-03 | The connection and its shell are held by a background keeper on this computer, so closing and starting the app again shows the same shell with its recent output and current directory, without a new login. Log out ends it; a dropped connection is reported and needs a new login. | D122, A69 | after 5 |
| FR-SSH-04 | A logged-in server's tab is a terminal on its shell, with the user's login environment, and shows the shell's current directory. | D122 | after 5 |
| FR-SSH-05 | Logging in, logging out and the terminal are refused unless the app listens on 127.0.0.1 and the request comes from this computer, for a loopback host name, from the app's own page (Origin), and the terminal needs a one-time token; Claude's MCP tools cannot reach them. | D122, D92, NFR-SEC-01 | after 5 |
| FR-SSH-06 | "Import from here" lists the output files in the terminal's current directory; one chosen file is copied into the investigation and opens the import dialog, several open the batch import (D97), as from the file browser; nothing changes on the server; with the server's name as origin device and `<server>:/path` as origin path. A name filter (D78) hides the files whose names do not contain its text. | D122, D97, D16, D78 | after 5 |
| FR-SSH-07 | "Send to <server>" on a node with coordinates writes `<label>.xyz` into the terminal's current directory, asking before it replaces a file; the app never deletes on the server. | D122 | after 5 |
| FR-SSH-08 | A server can forward X11 windows to an X server on this computer (Xming on `localhost:0.0`): ticked per server, used from the next shell (New shell replaces the running one without a new login); a server refusing X11 still gives the shell; the tab says when no X server answers on the display. | D123, A70 | after 5 |
| FR-SSH-09 | A server's shell has an `inspect FILE` command that opens each file in the Inspect window (FR-3D-11), by its full path on the server, through an invisible OSC 1337 sequence as the current directory is reported; a file that is not there is said so in the terminal. The tab says the command is there. The files one command names, such as a wildcard's (`inspect *.log`), open as one list in the Inspect window (D129). | D123, A70, D129, A74 | after 5 |
| FR-SSH-10 | Claude's read-only tools list the logged-in servers with their terminals' current directories, and the files in that directory or a directory below it. | D124, D91 | after 5 |
| FR-SSH-11 | Claude reads a text file there by numbered pages, by its last lines or by the lines matching a text or regular expression with lines around them, within A71's limits; it reads an output as the Inspect window does (job steps, termination, energies, convergence, frequencies, each structure's energy and criteria) and one of its structures as XYZ. Nothing is imported, and nothing on the server is written, deleted or run. | D124, D123, A71 | after 5 |
| FR-SSH-12 | A path that resolves (symlinks followed) outside the terminal's current directory is refused with that directory named, and a listing does not follow a link out of it; with no directory reported, nothing is read. Claude cannot log in: a server not logged in, or logged in through a keeper from before D124, is reported with what the user does. | D124, D122 | after 5 |
| FR-SSH-13 | Claude writes new text files in the terminal's current directory or a folder below it (new folders made on the way) only while the user has turned on "Let Claude write new files here" in the server's tab, which only the app's page can do and which is off at every login; the tab lists what Claude wrote. | D126, A72 | after 5 |
| FR-SSH-14 | A file, directory or link already at the name is never replaced: the write is refused with the next free copy name; an edited copy names its original, which stays unchanged, and the answer shows the difference. Names starting with a dot, paths leading outside the directory, files over A72's size and text with NUL are refused; nothing on a server is ever deleted, renamed or run by Claude. | D126, A72 | after 5 |
| FR-SSH-15 | One header button opens the clusters: it reads "HPC" until a cluster is logged in, then the cluster's name; the side panel's HPC tab chooses a saved cluster or adds one (Sigma2's offered ready-filled) and logs in, after which the cluster has a tab of its own named after it. | D127, D122 | after 5 |
| FR-SSH-16 | `scans FILE...` in a server's terminal and "Scan editor (n)" in Import from here open the files in the scan editor; the editor adds files from the server, reads running scans again keeping the path, and saves the joined scan into the terminal's directory without replacing a file (D138) | D138, A83 | after 5 |

**AC:** log in to Saga with password and code, type `cd` into a calculation folder: the tab shows that folder; shut the app down and start it again: the same shell is there without a new login; Log out ends it.

## FR-HIST / FR-OV · History and overview

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-HIST-01 | Every change to a record (create, update with old and new values, delete) is appended to the history with a timestamp and a source (manual or import). | D29 | 1 |
| FR-HIST-02 | History is viewable per record and for the whole investigation. | D29, D45 | 1 |
| FR-HIST-03 | History entries are never edited or deleted by normal use. Reverting a change is done by making a new change (P15). | D29 | 1 |
| FR-HIST-04 | An import, of one file or of a batch, can be undone from its history entry: its calculations, the stored copy and the nodes it created go, and what it changed on its node is put back. A dialog lists what will change first; the undo is refused, naming what depends on the import, when later work builds on it. | D102, A49 | later |
| FR-OV-01 | The overview shows per-branch status and node counts by status, open items (planned, running, failed, warnings, direct connections), recent changes, and notes per step. Clicking an item navigates to it. | D45 | 5 |

## FR-SET · Settings

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SET-01 | Settings: energy unit, CREST default N, duplicate tolerance, default origin device name. G_qh temperature and cutoff (the open investigation's, D159). | D28, D34, Q32 | 1–5 |

## FR-RUN · Starting and stopping the app (D107)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-RUN-01 | `chembook3d shortcut` creates a Chembook3D shortcut (Windows desktop and Start menu, Linux app menu, a Windows shortcut for WSL) that starts the app without a terminal; when the app already runs, it only opens a tab. | D107, A54 | after 5 |
| FR-RUN-02 | Started from the shortcut, the checkout is fast-forwarded only when it is on `main` with no local changes, the interface is rebuilt only when `frontend/` changed since it was last built, Python packages follow `uv.lock`, the server runs with no console window, and a page shows each step until the app is up. Why it was not updated, or why the old interface is shown, is said in the app. | D107 | after 5 |
| FR-RUN-03 | Started from the shortcut, the server shuts down 10 s after the last tab closes, or after 5 minutes without a tab; started from a terminal it does not. | D107, A54 | after 5 |
| FR-RUN-04 | "Shut down" in the top bar and on the start screen asks first, closes and pushes the investigation as on Ctrl+C, stops the server, and every tab says it has shut down. Only the app's own page can ask for it; Claude cannot. | D107, D91 | after 5 |

**AC:** create the shortcut, double-click it: the app opens in the browser with no terminal; close the tab: the server stops within about 10 s; start it again and press Shut down: the tab says Chembook3D has shut down.


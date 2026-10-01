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
| FR-NODE-04 | While a node has no calculations, saving edited coordinates updates the node in place. | D23, ID-4 | 1 |
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
| FR-CALC-04 | A single-point calculation records its geometry level: the level of the optimization that produced the node's geometry, taken from that calculation or set by the user. Its composite level is `SP level // geometry level`. | D31 | 2 |
| FR-CALC-05 | The user can edit a calculation's level-of-theory fields and geometry level. Edits are recorded in history, and the original parsed values stay viewable. | D29, P13 | 2 |

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
| FR-IMP-07 | Import xTB output files with the same kinds of results, where xTB provides them. | D8, A3 | 5 |
| FR-IMP-08 | Importing onto a node that has calculations: if the imported geometry does not match the node's (same atom order and elements, coordinates within tolerance), show W-GEOM and offer "create derived node" or cancel. | ID-7 | 2 |
| FR-IMP-09 | Duplicate check: if a new import matches an existing node's composition, charge and multiplicity, and its geometry is within the configurable tolerance after alignment, show "possible duplicate", with attach, new or cancel. Never merge automatically. | D47, Q32 | 2 |
| FR-IMP-10 | Import a CREST conformer ensemble. The preview lists conformers with energies. The lowest N are pre-selected (N from settings) and the user can untick any. The result is a group node with one member node per kept conformer and no representative. | D34, D46 | 5 |
| FR-IMP-11 | Members can be removed from a group node after import. Removal deletes the member node after confirmation. | D34 | 5 |
| FR-IMP-12 | Importing onto a planned node with no calculations replaces its geometry, and the old geometry is kept in history. | D24, ID-6 | 2 |
| FR-IMP-13 | A frequency job can be imported onto an existing node at any time. If the file contains other steps, the preview lists every step with its geometry: steps matching the node's geometry are attached as calculations; from the first step that changes the geometry, the app offers a derived node. The preview flags a freq whose level differs from the node's geometry level. | D52, ID-7, EN-4 | 2 |

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
| FR-GRP-01 | "Reconnect as group" creates a group node from selected nodes, records the incoming branches, and optionally creates an outgoing branch whose parents are the incoming branches. | D13, D18 | 3 |
| FR-GRP-02 | The user marks at most one member as representative. The app never sets it automatically. | D18, EN-10 | 3 |
| FR-GRP-03 | Members can be sorted by energy at the current view level and type. | D18 | 4 |
| FR-GRP-04 | Transitions into a group node are optional. | D14 | 3 |
| FR-GRP-05 | Deleting a group offers **dissolve** (members kept as nodes; branch per P4) or **delete with contents** (group, members, their calculations and edges removed, after a confirmation listing them). | P4, D54 | 3 |
| FR-GRP-06 | Nodes can be added to an existing group: select the group and the nodes together and choose **Add to group** (A20). | D65, A20 | after 5 |
| FR-GRP-07 | A button in an expanded group cycles its members through a grid, a vertical line and a horizontal line; the choice is kept with the group (A21). | D65, A21 | after 5 |
| FR-GRP-08 | Group members keep their branch, so a group can hold members from several branches; a group left with one member by the filters is drawn as that node (D66). | D66, A22–A26 | after 5 |
| FR-GRP-09 | In the structure view mode, a collapsed group shows its representative's structure; with no representative it shows none (D68). | D68 | after 5 |

**AC (lineage):** build the §6 example of [02](02-domain-model.md). A1's inspector shows lineage A1 → A → T, and R shows parents A1, A2, B1, B2. Adding the A-S3↔B-S3 rotation transitions leaves every node's branch unchanged. There is no action in the UI that creates a path across steps without edges.

## FR-EN · Energies

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-EN-01 | The view has one level selector (composite levels available in the investigation) and one energy-type selector (E, H, G, G_qh). Only combinations with at least one value are listed. | D32 | 4 |
| FR-EN-02 | Each edge shows ΔX = X(target) − X(source) at the selected level and type, or "n/a". There is no fallback to other levels or types. | D27, EN-3 | 4 |
| FR-EN-03 | G at a composite level follows EN-4. If no frequency calculation exists at the geometry level, G is "n/a" with W-NOFREQ. | D35 | 4 |
| FR-EN-04 | G_qh follows EN-5 and EN-6: recomputed from the selected frequency step with Truhlar's raise on vibrational entropy only, at a configurable T and cutoff (defaults 298.15 K, 100 cm⁻¹) recorded with the value, reproducing Jonas's reference script. The job-printed G is shown alongside for comparison. Linear molecules give "n/a" with a warning. | D41, D49, D56–D58 | 4 |
| FR-EN-05 | Energy profile: for a pathway chosen by the user along existing edges, plot X(n) − X(ref) against position, with a user-chosen reference node. Several branches can be overlaid in branch colours. A branch's pathway includes the nodes on no branch it starts from or runs into along its edges. | D39, D67, EN-8, INV-2 | 4 |
| FR-EN-06 | Energy table: for the viewed branch or pathway, one row per node with label, step, branch, composite level, E, G (and G_qh), the free species added or subtracted (D69), and ΔX from the reference. Exportable as CSV. | D44, D69 | 4 |
| FR-EN-07 | The display unit is kcal/mol by default and changeable in settings (at least kcal/mol, kJ/mol, hartree, eV). | D28, P14 | 4 |
| FR-EN-08 | Energy values are never user-typed. They come only from calculation results. | EN-1 | 4 |
| FR-EN-09 | In energy profiles, a direct connection is drawn as a dotted connector labelled "no TS" and never as a barrier. | D53, INV-8 | 4 |

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

## FR-3D · 3D viewer

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-3D-01 | Show the selected node's geometry with rotate, zoom and pan; save the orientation for the structure card; hide hydrogens by a setting; pop the view out into a movable, resizable window and put it back. | D20, D74, D75, D82, A33 | 1 (pop-out after 5) |
| FR-3D-02 | Measure distances and angles between picked atoms. | D20 | 3 |
| FR-3D-03 | Animate any vibrational mode from a frequency calculation, with imaginary modes listed first. | D20 | 3 |
| FR-3D-04 | Overlay 2 to 12 selected geometries, or a group's members, on one reference: aligned on all atoms, on chosen atoms (a list per structure where the numbering differs), or not aligned; optionally allowing a mirror image. Each has its own colour, a visibility switch and its RMSD over the alignment atoms and over all atoms. Save the image and a multi-structure .xyz. | D20, D80, A31 | 3 (choices after 5) |
| FR-3D-05 | No geometry editing in the viewer. | X3 | — |
| FR-3D-06 | Copy xyz to the clipboard and save a .xyz file from the node inspector. | D39 | 1 |
| FR-3D-07 | Named alignment sets keep each node's alignment atoms in the investigation, to reuse and extend. | D80, A31 | after 5 |

## FR-STER · Buried volume and steric maps (D81)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-STER-01 | Named steric profiles keep the sphere radius, radii and their scale, whether hydrogens count, the mesh and the map's colour scale, and per node the centre (an atom or a centroid), the orientation (z-axis and xz-plane atoms) and the atoms left out. | D81, A32 | after 5 |
| FR-STER-02 | A node's atoms are typed or picked in the 3D view, with helpers that leave out one side of a bond or everything but one ligand. A node with the same elements in the same order can use another node's atoms. | D81 | after 5 |
| FR-STER-03 | The node inspector shows %V_bur and, with an orientation, the quadrants, octants and steric map, always with the profile's settings. | D81, A32 | after 5 |
| FR-STER-04 | Each result is stored with the inputs it came from and marked out of date, with the reason, when the coordinates, the profile's settings or the node's atoms change. | D81, A32 | after 5 |
| FR-STER-05 | Compare a branch, a pathway, a group or a selection: a table and a CSV with the settings and atoms on every row, maps side by side on one colour scale, a difference map of two nodes, and map images. | D81, A32 | after 5 |

## FR-SEL · Selectivity (D83)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SEL-01 | Saved selectivities have two or more named outcomes; each outcome is one or more TS nodes or groups, and a group counts with all its members. | D83, A34 | after 5 |
| FR-SEL-02 | Every TS is valued at one composite level and energy type with no fallback; a missing value makes the result n/a with the reason. TSs with different atoms or charges, or a TS in two outcomes, are refused with the reason. | D83, EN-3 | after 5 |
| FR-SEL-03 | An outcome's weight is the Boltzmann sum over its TSs (Curtin–Hammett) or only its lowest TS's; both are computed, the chosen one leads. | D83, A34 | after 5 |
| FR-SEL-04 | The temperature is the G_qh setting or the selectivity's own; G_qh is recomputed at it from the stored frequencies, E, H and G are used as read and the result says so. | D83, A34 | after 5 |
| FR-SEL-05 | The result shows each outcome's ΔΔG‡ and predicted percentage, the ee or de for two outcomes, each TS's ΔG and share, and an optional experimental ratio with its ΔΔG‡. | D83, A34 | after 5 |
| FR-SEL-06 | An Analyses view lists the selectivities; one is made there or from a canvas selection. Creating, changing and deleting one is recorded in the history. | D83, A34 | after 5 |

## FR-TOF · Turnover (D86)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-TOF-01 | Saved turnovers hold one closed pathway (A13); the cycle runs from the node the pathway returns to, and points before it are left out with a note. | D86, A36 | after 5 |
| FR-TOF-02 | Every point is valued at one composite level and energy type with no fallback, relative to the cycle's first node and balanced by its free species, so the closing point is ΔG_r; a missing value makes the result n/a with the reason. | D86, EN-3, D69 | after 5 |
| FR-TOF-03 | The TOF comes from the full energetic-span formula; each TS's and intermediate's degree of TOF control is given, the TDTS and TDI are the largest, and δE is reported. A TS is a node (or a group's representative) marked as one; a "no TS" connection makes the TOF an upper bound, with a note. | D86, A36 | after 5 |
| FR-TOF-04 | The temperature is the G_qh setting or the turnover's own, with G_qh recomputed at it. The result shows the TOF (s⁻¹, h⁻¹), δE, ΔG_r, the cycle's profile with the TDTS and TDI marked, and the degree-of-control table with a CSV export. | D86, A36 | after 5 |
| FR-TOF-05 | A turnover can be compared with another at the same level, energy type and temperature, as percentages and an effective ΔΔG‡. Turnovers are listed in the Analyses view and made there or from a closed pathway in the energy drawer; creating, changing and deleting one is recorded in the history. | D86, A36 | after 5 |

## FR-SHARE · Read-only copy to share (D79)

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SHARE-01 | "Share read-only copy…" saves the open investigation as one self-contained HTML file that opens offline in a browser, with no server or install. | D79 | after 5 |
| FR-SHARE-02 | The file shows the canvas with its view modes and filters, the details, notes and calculations of every record, the 3D view with measuring and the included vibrations, and the energies, profile and table at every level and energy type (A30). | D79, A30 | after 5 |
| FR-SHARE-03 | Nothing in the file can be edited, and it makes no network request. | D79 | after 5 |
| FR-SHARE-04 | The file holds no local paths (investigation folder, original paths and devices of imported files), no copied output files and no change history. | D79 | after 5 |

**AC:** export the demo investigation, open the file with the server stopped: the canvas, a node's calculations and 3D view, and a profile at another level show, nothing can be edited, and the file contains no folder path.

## FR-HIST / FR-OV · History and overview

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-HIST-01 | Every change to a record (create, update with old and new values, delete) is appended to the history with a timestamp and a source (manual or import). | D29 | 1 |
| FR-HIST-02 | History is viewable per record and for the whole investigation. | D29, D45 | 1 |
| FR-HIST-03 | History entries are never edited or deleted by normal use. Reverting a change is done by making a new change (P15). | D29 | 1 |
| FR-OV-01 | The overview shows per-branch status and node counts by status, open items (planned, running, failed, warnings, direct connections), recent changes, and notes per step. Clicking an item navigates to it. | D45 | 5 |

## FR-SET · Settings

| ID | Requirement | Trace | Phase |
|---|---|---|---|
| FR-SET-01 | Settings: energy unit, CREST default N, duplicate tolerance, default origin device name. G_qh temperature and cutoff. | D28, D34, Q32 | 1–5 |

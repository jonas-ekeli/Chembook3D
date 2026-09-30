# 08 · Decisions and open questions

_Chembook3D specification pack · v1.1 · 2026-09-29. This is the maintained source of truth for decisions. Other documents cite these IDs. When a decision changes, update this file first, then every document that cites the ID. The interview history is in [alignment-record.md](alignment-record.md)._

## 1. Confirmed decisions

Rationale: D1–D5 come from HANDOFF.MD. Every other decision was confirmed by Jonas in the batch shown; the rationale is Jonas's stated need unless noted.

| ID | Decision | Source |
|---|---|---|
| D1 | Product direction: a local-first, data-backed computational chemistry notebook for organizing and reviewing investigations of catalytic mechanisms. | HANDOFF |
| D2 | Primary workspace: an infinite 2D canvas with structure nodes joined by typed edges, plus interactive 3D molecular visualization. | HANDOFF; workflow detailed in D33, D40, D43 |
| D3 | A conceptual reaction step is represented separately from the concrete structure-to-structure transitions that realize it. | HANDOFF |
| D4 | Pathways are never assembled by picking the lowest-energy structure per step independently; energies may inform analysis but never rewrite or collapse branch lineage. | HANDOFF |
| D5 | Phase now is discovery and specification only: no application code. | HANDOFF |
| D6 | Primary user is Jonas, working alone. Others may use it later if it proves useful, so it should be shareable, but it is not built for collaboration now. | Batch 1 |
| D7 | Nodes can be created manually: the user types in metadata, coordinates and notes directly. | Batch 1 |
| D8 | Nodes can be imported from calculation output files. Programs: Gaussian (highest priority), ORCA, xTB, CREST. | Batch 1 |
| D9 | Calculations run on external machines. The app runs locally, never launches or monitors jobs, and only organizes, imports and parses. | Batch 1 |
| D10 | Target platforms: Windows and Linux. | Batch 1 |
| D11 | Reference scientific use case: olefin metathesis with asymmetric Ru-CAAC catalysts that carry bulky fragments with hindered rotation; some conformations and configurations carry over across parts of the catalytic cycle. | Batch 1 |
| D12 | Reference branch example accepted: rotamers A/B split at initiation and persist; olefin coordination splits them again into A1/A2/B1/B2. The four branches share the same reaction steps (coordination, [2+2] TS, metallacyclobutane, retro-[2+2] TS, product π-complex) but have separate concrete transitions. | Batch 2 |
| D13 | Branches may reconnect at a shared structure, and only the user decides that they do. After reconnection, several conformers can still be logged at that point (for example to find the lowest-energy candidate), but they are no longer separated by branch. | Batch 2; amended by D66 (members keep their branch) |
| D14 | Transition states leading into a reconnection are optional. The model must allow a reconnection with or without computed connecting TSs. | Batch 2 |
| D15 | Each distinct geometry is its own node. Calculations on the same geometry (for example single points at different levels of theory) stay on that node as separate calculation records, each with its own energy. | Batch 2 |
| D16 | Imported files are copied into the investigation. Each copy keeps origin metadata: the name of the external storage device or server, and the file's path on it. Live links to the original are not relied on. | Batch 2 |
| D17 | Interconversion between persistent branches (for example an A↔B rotation TS) is drawn as an ordinary transition edge. | Batch 3 |
| D18 | A reconnection point is shown as a group node on the canvas. It holds conformer nodes from the incoming branches; the user marks one as the representative the outgoing path continues from. The app may sort members by energy but never chooses the representative. | Batch 3 |
| D19 | Nodes, transitions and branches each have one status: planned, running externally, done, failed, rejected or superseded. Automatic validation warnings (for example a minimum with an imaginary frequency, or a TS without exactly one) are shown but never change a status. | Batch 3 |
| D20 | The 3D viewer is view-only in the first release: rotate, measure distances and angles, animate imaginary modes, overlay two geometries. Geometry is edited as text, by editing coordinate lines directly. | Batch 3 |
| D21 | Each investigation is one self-contained folder (a database plus the copied files) that can be zipped and shared. Backup is the user's responsibility, for example cloud-synced folders or git. | Batch 3 |
| D22 | Implementation language: Python (Jonas's strongest language, fits the marimo work). UI toolkit and database settled by D38; packaging is a proposal in the spec. | Batch 4 |
| D23 | A node's coordinates can be edited freely while it has no calculations. Once it has at least one, saving an edit creates a new node derived from the original, and the original keeps its calculations. | Batch 4 |
| D24 | Planned-node workflow: create a planned node with a guess geometry, then import the finished output onto it; the imported geometry and data replace the provisional ones (allowed by D23 because the node had no calculations yet). The change history keeps the guess. | Batch 4 |
| D25 | Multi-step Gaussian jobs (opt+freq, --Link1--): take the last geometry from the step with the highest level of theory, which is usually the job's last geometry. | Batch 4 |
| D26 | Failed or incomplete optimizations: import the last geometry and tag the node as "optimization incomplete". | Batch 4 |
| D27 | Relative energies are computed only between calculations at the same level of theory, never across different levels. A relative energy belongs to the edge between two nodes. | Batch 4; confirmed with the summary |
| D28 | Energy unit defaults to kcal/mol and is configurable in settings. | Batch 4 |
| D29 | A change history is kept for records. | Batch 4 |
| D30 | Same level of theory means program, method or functional, basis set, dispersion correction and solvation model all match. Custom basis sets occur and must be representable. | Batch 5 |
| D31 | A single-point level of theory includes the level the geometry was optimized at (for example cc-pVQZ//cc-pVDZ). Two single points at the same level on geometries from different optimization levels are not the same overall level. | Batch 5 |
| D32 | Free energy is the preferred energy type, with electronic energy as fallback. All edges in one viewed cycle use the same level of theory and energy type, chosen from a drop-down of the levels and energy types that are available. | Batch 5 |
| D33 | Canvas proposal accepted: a colour per branch, status badges on nodes, energies on edges (can be hidden), manual layout plus a helper that lays out one branch left to right by step, and filtering by branch, status or reaction step. | Batch 5 |
| D34 | CREST ensemble import keeps the lowest N conformers (N configurable), with an easy way to select and remove unwanted conformers afterwards. | Batch 5 |
| D35 | Free energy at a composite level = single-point energy + thermal correction (G − E) from the frequency job at the geometry level. Custom basis sets are identified by a user-given name. | Batch 6 |
| D36 | Jonas's usual thermal correction treats frequencies below 100 cm⁻¹ specially; supporting it is wanted. The correction printed by the frequency job is an acceptable fallback if the custom one is hard. | Batch 6; refined by D41, D49 |
| D37 | Manual nodes need no mandatory fields beyond what the app needs to store them, but must support free-text notes. | Batch 6 |
| D38 | Architecture direction: option B, a Python backend (FastAPI, SQLite) with a web interface (TypeScript, React Flow canvas, 3Dmol.js), run locally. Jonas notes it could also be deployed over the internet later. | Batch 6; hosting scope settled by D42 |
| D39 | First-version minimum: energy profiles, a rotatable 3D structure view, a canvas image export, and easy access to xyz coordinates. An energy table is wanted but its design is open. | Batch 6 |
| D40 | The canvas offers switchable view modes that show nodes in different ways. | Batch 6; modes in D43 |
| D41 | Low-frequency treatment: frequencies below 100 cm⁻¹ are raised to 100 cm⁻¹ as in GoodVibes. The app recomputes this correction from the parsed frequencies and offers it as its own energy type next to the job's printed correction. | Batch 7 |
| D42 | The first version runs locally only: one user, no login. Hosting stays possible for future versions but is not designed in detail now. | Batch 7 |
| D43 | Node view modes: compact (label and status), energy (label and ΔG), structure (small rendered image of the geometry). | Batch 7 |
| D44 | Energy table: covers the viewed branch or cycle, one row per node (label, step, branch, level, E, G, ΔG from the reference node), exportable to CSV. | Batch 7 |
| D45 | Resume overview shows branch status, open items (planned and failed nodes), recent changes and notes per reaction step. | Batch 7 |
| D46 | Imported CREST conformers land in a group node. | Batch 7 |
| D47 | A near-duplicate geometry on import triggers a "possible duplicate" warning; the user decides, and the app never merges automatically. | Batch 7 |
| D48 | Phasing accepted: foundation → Gaussian import → canvas and 3D → energies → ORCA/xTB/CREST import and resume overview → hosting and collaboration later. | Summary |
| D49 | Low-frequency treatment is Truhlar's method: vibrational frequencies below 100 cm⁻¹ are raised to 100 cm⁻¹. | Summary |
| D50 | Jonas confirmed the alignment summary. | 2026-09-29 |
| D51 | All proposals P1–P30 confirmed, with the amendments D52–D55. | Proposal review, 2026-09-29 |
| D52 | A frequency job can be imported onto an existing node later. The file may contain other steps too, so each step is checked against the node's geometry. | Amends P1 |
| D53 | Intermediates may be connected directly, with no TS in between. Such a direct connection is clearly marked "no TS" on the canvas, in the overview and in energy profiles. | Amends P2 |
| D54 | Deleting a group node offers either dissolving it (P4) or deleting it with all its contents. | Amends P4 |
| D55 | uv manages the Python environment. | Amends P23 |
| D56 | The Truhlar raise applies to vibrational entropy only. ZPE and thermal energy use the unmodified frequencies. Jonas's script `thermochem_corr_G16` is the reference implementation. | Jonas's script, 2026-09-29; resolves Q30b, confirms A4 |
| D57 | No concentration or standard-state correction; translational entropy is for an ideal gas at 1 atm, as in the script. | Jonas's script; resolves Q31 |
| D58 | G_qh temperature and cutoff are parameters (defaults 298.15 K, 100 cm⁻¹), stored with each value. Linear molecules give "n/a" with a warning in v1, as the script does not support them. | Jonas's script |
| D59 | Dispersion-parameter IOps (e.g. `IOp(3/174–178)` on `EmpiricalDispersion=GD3BJ`) are part of the dispersion in the level of theory. The first import with a new IOp set asks for a name (e.g. GD3MBJ), like custom basis sets (FR-CALC-03); later imports reuse it, and equality uses the name. Other IOps (e.g. `1/8`) do not change the level. | Jonas, 2026-09-29; resolves Q35 |
| D60 | Chembook3D opens in a browser tab on 127.0.0.1 (phase 0/1 behaviour). A native window (pywebview) may be added later; no UI component library, plain React with hand-written styles. | Jonas, 2026-09-29; resolves Q33 |
| D61 | Gaussian outputs are read by Chembook3D's own parser, one record per job step (route, GEN/GENECP basis blocks, IOps, per-step termination), not by cclib. Whether ORCA/xTB/CREST use cclib is decided in phase 5 on real files. | Jonas, 2026-09-29; resolves P16 for Gaussian |
| D62 | Geometry tolerances, both app settings: a step attaches to a node when its final geometry is within 0.001 Å RMSD after alignment (ID-7); within 0.05 Å it is flagged as a possible duplicate and the user chooses attach or new node (ID-8). | Jonas, 2026-09-29; resolves Q32 |
| D63 | The phase 3 defaults A4–A10 (split, branch deletion, reconnect, derived nodes, group endpoints, edge colours, collapsed groups) are confirmed as they are built. Jonas tests the branch model on his own data later. | Jonas, 2026-09-29 |
| D64 | ORCA and xTB outputs are read by Chembook3D's own parsers, like Gaussian (D61), not by cclib; CREST ensembles by a small xyz reader. Tested on public sample outputs from cclib's regression data (ORCA 5.0 and 6.0, xTB 6.6.1, BSD-3) and CENSO's test fixtures (CREST, LGPL-3.0), since Jonas has no samples of his own (R1). | Jonas, 2026-09-29; resolves P16 for ORCA and xTB |
| D65 | After testing the app, Jonas asked for two group features: a way to add nodes to an existing group (FR-GRP-06) and a small button in a group to switch its members between a vertical and a horizontal line (FR-GRP-07). How they work is A20 and A21. | Jonas, 2026-09-30 |
| D66 | Every group's members keep their branch, and a member's branch can be set or changed like any node's, so one group can hold members from several branches. Jonas's use: one group per species (IM1, TS1, IM2, TS2) with one conformer per branch inside, edges from conformer to conformer, so that collapsed groups read as one path. The existing filters apply to members: a group with exactly one member left after filtering is drawn as that node, and a group with none left is hidden. A reconnection still records the incoming branches and may start an outgoing branch (D13, D18). Details in A22–A26. | Jonas, 2026-09-30; amends D13, A10, A19, A20; resolves Q36 |
| D67 | A node or group on no branch belongs to every branch that reaches it along a transition. Jonas's case: IM1 is on no branch and splits into TS1-1 (branch 1) and TS1-2 (branch 2); branch 1's pathway, profile and table start at IM1, and likewise a shared node after the branches (a common product) ends them. Only transitions are followed, never energies (INV-2, EN-10): the trace goes back through, or on into, such nodes only while exactly one way leads in or on, a fork still stops it and offers the choices (A13), and a node on another branch is never entered. A branch that merges into a shared node and branches again passes through every such merge point, a shared node or a reconnection group alike (Jonas, 2026-09-30); when the way on after a merge is a new branch, its parent branch says which branch it continues, and a new branch with several parents stops at the merge point. The canvas branch filter is unchanged: "No branch" stays a filter of its own, shown by default. | Jonas, 2026-09-30; amends FR-EN-05, A13 |
| D68 | In the structure view mode, a collapsed group shows the structure of its representative. A group with no representative shows no structure, as before, and the app never picks one (FR-GRP-02, EN-10). A group that the filters leave with one member is already drawn as that node, with that node's structure (A23). | Jonas, 2026-09-30; amends FR-CAN-04 |
| D69 | **Free species** keep relative energies mass-balanced across association and dissociation. A free species (a substrate such as propene or styrene, a released fragment such as ethylene, a product) is a record of its own with everything a node has (label, charge, multiplicity, geometry, imported files, calculations, energies, 3D view, notes, status, history), but it has no step, branch, group or edges and is not drawn on the canvas; it is listed under "Free species". A transition lists the species that **join** ("+ propene") or **leave** ("− C₂H₄") on it, each with a count, shown as chips on the edge. Walking a pathway from the reference, a species that leaves is added and one that joins is subtracted (the signs swap when walking backwards), so every point has the reference's atoms: X_bal(n) = X(n) + Σ X(leaving) − Σ X(joining) − X(ref). The same applies to profiles, the energy table (a "Free species" column lists what was added or subtracted) and ΔX on an edge. A species needs a value at the same composite level and energy type, with no fallback (EN-3); otherwise the point or edge is "n/a" with the reason. W-BALANCE flags an edge whose atoms or total charge differ before (source + joining species) and after (target + leaving species), when all of them have coordinates; a group end uses its representative. D57 is unchanged: no 1 M concentration correction, although it would change each association or dissociation by about 1.9 kcal/mol at 298 K. A closed cycle (A13) ends at the reaction energy, and the energy table lists the closing node again with its own balance. A pathway that does not pass through the reference is balanced from the first point it shares with one that does, or else from its own first point. A node with no edges and no group can become a free species (it leaves its step and branch), and a species on no transition can become a node again. | Jonas, 2026-09-30 (decision card); amends EN-3, EN-8, FR-EN-02, FR-EN-05, FR-EN-06, A13 |
| D70 | A line between collapsed groups that stands for several edges (A24) shows the energy of the edge between the groups' representatives, as a collapsed group shows its representative's structure (D68). At each end that is a collapsed group, that edge must start or end at the group's representative (or at the group itself); the other end may be a plain node. With no representative, or no edge between the representatives, the line shows no energy, as before. The value is the edge's own ΔX, including the free-species balance (D69). | Jonas, 2026-09-30; amends A24 |
| D71 | **Sync through a private GitHub repo, and a newer-database warning.** (a) Opening an investigation whose schema revision this app does not know stops before anything is written (no backup, no migration, no lock) and says the investigation was saved by a newer version of Chembook3D and how to update the app. (b) An investigation can optionally be linked to an empty private Git repository (GitHub in practice), or cloned from one ("Open from GitHub"). The app then pulls when it opens the investigation, and commits and pushes when it closes it, when the user presses Sync, and (within a short time limit) when the app stops. The repository leaves out the lock file, the journal and `backups/`, and stores every file byte for byte (no line-ending conversion). The database is never merged: if this computer and GitHub both have changes, nothing is pushed or pulled, and the user keeps one copy. Keeping this computer's copy records GitHub's as an earlier version in the history and saves its database to `backups/`; keeping GitHub's saves this computer's database to `backups/` and its commits to a `set-aside/…` branch, locally and on GitHub. (c) Before upgrading the schema of a linked investigation, the app asks, because older versions on other computers will not open it afterwards. (d) The app never asks for, sees or stores a password or token: it runs the `git` installed on the computer, whose credential helper signs in (Git Credential Manager on Windows, `gh auth login` or Git Credential Manager on Linux and WSL), with terminal prompts turned off so a missing sign-in fails at once. (e) Importing a file over 100 MB into a linked investigation warns that GitHub refuses such files. Sync is between one person's own computers; collaboration stays out of scope. The server still listens on 127.0.0.1 only (NFR-SEC-01), and an unlinked investigation works as before. | Jonas, 2026-09-30 (decision card; proposal `spec/proposals/git-sync.md`); amends X2, C5, C6 |
| D72 | **A free species counts at every point after the edge it joins or leaves on, including node cards.** Every node and group joined to the reference by transitions has one free-species balance, accumulated along the route from the reference: the route with the fewest transitions walked backwards, then the fewest transitions, then the transitions created first. So in a catalytic cycle the points after the reference are balanced forward from it and only the points before it (a precatalyst) backwards, and the reference stays at zero. A group and its members share one balance. In energy mode each node card shows this balanced ΔX from the reference, and its tooltip lists the species added or subtracted; a species without a value at the selected level makes the card "n/a" with the reason (EN-3). A node not joined to the reference counts no species, and its tooltip says so. Profiles keep their own pathway's balance (a closed cycle still ends at the reaction energy, A13), and a pathway that shares no point with one through the reference starts from its first point's balance on the canvas instead of from nothing. Where two routes to a node carry different species, the card follows the route above and a profile follows its own pathway. | Jonas, 2026-09-30 (reported that node cards ignored joins and leaves; confirmed the route rule the same day); amends D69, EN-8, FR-SPC-06 |
| D73 | **Structure mode shows ΔX below each structure** once a reference node is chosen, the same value and tooltip as the node card in energy mode (the D72 balance at the selected level and energy type), centred under the drawing as in a figure. A collapsed group shows it below its representative's structure (D68). With no reference chosen, structure mode shows no energies. | Jonas, 2026-09-30; amends D43, FR-CAN-04 |
| D74 | **The 3D view can save its orientation for the node's structure card.** "Save orientation for card" stores the current rotation with the node; the structure-mode card, and a collapsed group whose representative it is (D68), are then drawn turned the same way, and the 3D view opens that way. "Reset to default" forgets it. | Jonas, 2026-09-30; amends D43, FR-CAN-04, FR-3D-01 |
| D75 | **A setting hides hydrogens for clarity** in the 3D views and on structure cards. Atom numbers for measuring stay those of the full structure. | Jonas, 2026-09-30; amends FR-3D-01, FR-CAN-04 |
| D76 | **An arrow can leave from and arrive at any of the four sides of a node or group box.** Dragging from a side's handle to a side of another box creates the transition with those sides; dragging an arrow's end onto another side of the same box moves it there, and the transition panel can change both sides too. | Jonas, 2026-09-30; amends FR-EDGE-01, FR-CAN-01 |
| D77 | "Reaction step" stays on nodes and groups, as a stage of the mechanism (P2). It is not moved onto edges and not renamed. Intermediates and TSs both get one; it lines up branches in energy profiles and drives Arrange branch, the stage filters and the per-step notes. | Jonas, 2026-09-30 |
| D78 | **The import dialog's file browser starts in the folder a file was last picked from and can filter by name.** The folder is one app-wide setting, shared by every place that imports and kept across restarts; if it no longer exists the browser starts in the home folder as before. A filter box below the path shows only the files and folders in the shown folder whose names contain the typed text, ignoring case (e.g. "SPQZ"); it is cleared on moving to another folder. | Jonas, 2026-09-30; amends FR-FILE-02, WF-04 |
| D80 | **Overlays align on atoms the user chooses and hold up to 12 structures.** The overlay dialog aligns on all atoms (only when every structure has the same elements in the same order, as before; otherwise the centres are matched), on chosen atoms, or not at all (the stored coordinates as they are, for structures that already share a frame). Chosen atoms are clicked in the 3D view or typed as numbers and ranges (`1-12, 15`), numbered as in the full structure, so hidden hydrogens do not shift them (D75). When all structures have the same elements in the same order, one list serves all of them; otherwise each structure has its own list, paired with the reference's in order, and each pair must be the same element. At least three atoms are needed. A named **alignment set** ("Ru–CAAC core") keeps each node's atom list in the investigation, so it can be reused; a node not yet in it is added by picking its atoms and updating the set. Any 2 to 12 selected nodes with coordinates, or a group's members ("Overlay members"), are overlaid on one reference, the first or a chosen one. Each structure has its own colour, a visibility switch and a legend row with its RMSD over the alignment atoms and, where its atoms correspond one to one with the reference's, over all atoms. "Allow mirror image" lets the alignment reflect as well as rotate, so pro-R and pro-S TSs can be compared; the legend says which structures were mirrored. The dialog saves the image and the placed structures as one multi-structure .xyz. | Jonas, 2026-09-30 (item 1 of `spec/proposals/selectivity-sterics-overlays.md`); amends FR-3D-04 |

## 2. Assumptions in use

| ID | Assumption | Consequence if wrong |
|---|---|---|
| A1 | The spec pack lives in the project's shared `spec/` folder | None for the product |
| A2 | Getting files from clusters to the local machine happens outside the app | A sync or fetch feature would need scoping |
| A3 | ORCA, xTB and CREST imports extract the same kinds of results as Gaussian, where printed | Parser scope for phase 5 changes |
| A4 | Phase 3 default (confirmed, D63): a node can be split into branches only when it is on a branch itself | Split would also need to start a branch from a node with none |
| A5 | Phase 3 default (confirmed, D63): a branch that has child branches cannot be deleted (delete the children first); deleting a branch keeps its nodes, with no branch | Delete would need to re-parent or cascade |
| A6 | Phase 3 default (confirmed, D63): reconnecting as a group needs at least two nodes that are not already in a group | Single-node or nested groups would need rules |
| A7 | Phase 3 default (confirmed, D63): a derived node (edited coordinates or a new import) keeps the step and branch of its source | The user would reassign them by hand |
| A8 | Phase 3 default (confirmed, D63): a group counts as a non-TS endpoint, so a transition into or out of a group with no TS at the other end is drawn "no TS" (D53) | Group edges would need their own style |
| A9 | Phase 3 default (confirmed, D63): a transition is drawn in its source node's branch colour, dashed when it joins two different branches | Only the look changes |
| A10 | Phase 3 default (confirmed, D63): members of a collapsed group are drawn as the group box; expanding it shows them inside the box  Amended by D66: only members left by the filters are drawn, and a group with one left is drawn as that node | Only the look changes |

Phase 4 defaults (A11, A12 and A14 confirmed by Jonas; A13 revised by him):

| ID | Assumption | Consequence if wrong |
|---|---|---|
| A11 | Confirmed by Jonas, 2026-09-29: a frequency calculation belongs to the composite level of the node's latest optimization; on a node with no optimization it is taken at its own level (geometry level = its own level) | Such a node would get no free energy until an optimization is attached |
| A12 | Confirmed by Jonas, 2026-09-29 (method and multiplicity rarely change within an investigation): the default R (singlet) or U (open shell) prefix is not part of the method, so RB3LYP and B3LYP are one level; RO and other explicit prefixes are kept | Levels would need merging by hand |
| A13 | Revised by Jonas, 2026-09-29 (catalysis needs cycles): a pathway visits each node once, except that its last node may return to a node it has already visited, closing a catalytic cycle. The cycle is closed once and extending stops there; further turnovers are not drawn, since they would repeat the same calculations. Where it can continue along more than one transition it stops and offers the choices, visited nodes included (EN-10). A branch's pathway is never traced back into the branch itself or from a later step to an earlier one, so the closing edge is not taken as the way in. The profile marks the closing node with ↻; the table keeps one row per node | Cycle handling in pathways and profiles changes |
| A14 | Confirmed by Jonas, 2026-09-29: G_qh that cannot be computed shows "n/a" with W-LINEAR (linear molecule, D58) or W-QH (temperature or cutoff out of range), listed with the other warnings in 02 §4 | Only the message changes |

Phase 5 defaults (confirmed by Jonas, 2026-09-29):

| ID | Assumption | Consequence if wrong |
|---|---|---|
| A15 | Confirmed by Jonas, 2026-09-29: xTB single points and frequency jobs print no coordinates, so such a file can only be imported onto a node; its calculations are taken on that node's geometry and the preview says so (NO-COORD). xTB prints no molecular mass or rotational constants either, so an xTB frequency job gives G from xTB's own printed G(RRHO) contribution, while G_qh shows "n/a" with W-PARSE | G_qh for xTB would need the mass and rotational constants computed from the node's geometry |
| A16 | Confirmed by Jonas, 2026-09-29: a CREST ensemble file carries only energies and coordinates, so the preview asks for charge and multiplicity and takes the energies as GFN2-xTB (editable), stored at the level "CREST GFN2-xTB". The lowest 10 are ticked (setting "Conformers kept on import", D34); members are named after the group with the conformer's number, get role minimum and status done, and the group has no representative (D18) | Members would need editing by hand |
| A17 | Confirmed by Jonas, 2026-09-29: for ORCA, G is E plus ORCA's printed G−E(el), as for Gaussian (EN-4). ORCA's own entropy uses Grimme's quasi-RRHO, so G and G_qh (Truhlar, D56) differ a little more than for Gaussian: 0.00006 Eh on the sample | Only which G is shown by default |
| A18 | Confirmed by Jonas, 2026-09-29: an ORCA output with several jobs ($new_job) is read up to its second job; W-PARSE names the jobs that were not imported. A scan, MD or other job that is not an optimization, single point or frequency job is imported as job type "other", with its last printed geometry | Multi-job files would need splitting into one calculation per job |
| A19 | Confirmed by Jonas, 2026-09-29: the resume overview is shown in the side panel whenever nothing is selected, and the Overview button in the header clears the selection to show it. Group members on a branch are counted with it (D66), and members with no branch with their group; nodes on no branch and in no group are counted as "No branch" | Only the layout changes |

Group additions (D65), defaults picked while building, awaiting Jonas's review:

| ID | Assumption | Consequence if wrong |
|---|---|---|
| A20 | A group and one or more nodes are selected together (Ctrl, Cmd or Shift-click, or Shift-drag a box), and **Add to group** in the side panel adds the nodes to the group. A node keeps its branch (D66), which is also noted as the branch it came from; that branch becomes one of the group's incoming branches and a parent of its outgoing branch, unless it is the outgoing branch or descends from it. A node already in another group moves to this one; if it was that group's representative, that group has none (FR-GRP-02), and a group left with fewer than two members stays until the user dissolves it. The history has one entry on the group for the addition (INV-3). Dragging a node onto a group does not add it, because a member leaves a group only by dissolving the group or deleting the node, so an accidental drop would be hard to undo | Adding would need another gesture, or members would need a way out of a group |
| A21 | A group's members are laid out in a grid (as before, and still the default for a new group), a vertical line or a horizontal line. A button in the header of an expanded group cycles through the three: grid, vertical line, horizontal line, and back to the grid (Jonas, 2026-09-30: the grid must stay reachable). The choice is stored with the group; like positions it is layout, not history | The grid would need to stay reachable, or the layout would be a view setting |

Groups spanning several branches (D66), defaults picked while building, awaiting Jonas's review:

| ID | Assumption | Consequence if wrong |
|---|---|---|
| A22 | Opening an investigation made before D66 puts each group member that has no branch back in the branch it came from (migration 0005). CREST members, which never had a branch, stay without one. The migration writes no history entries | Members would need their branches set by hand |
| A23 | A group left with exactly one member by the filters is drawn as that node, at the group's place, with the node's own energy and status. Edges to or from the group itself are drawn to that node. An expanded group is sized for the members left | The group box would stay, with one member inside |
| A24 | Edges joining the same two drawn boxes (for example two conformer-to-conformer edges between collapsed groups) are drawn as one line. It is dotted "no TS" or dashed only if all of them are, takes their branch colour when they share one and is grey otherwise, and shows ΔX only when it stands for one edge, or, for several, the ΔX of the edge between the groups' representatives (D70); for several it also says how many. Clicking it selects the first; the rest are listed in the energy table and the outline as before | Each edge would be drawn separately, on top of each other |
| A25 | A group whose members are all transition states is marked ‡ like a TS node. Edges to the group itself still treat it as a non-TS end (A8) | Group edges would need a TS rule |
| A26 | Dissolving a group (P4) keeps each member's branch, or clears all of them, by the user's choice. "Arrange branch" places a group where its members on that branch would go, and leaves the members inside it | Only the dissolve choice and the layout change |
| A27 | D74: the orientation is saved in the investigation (so it syncs and survives a reload) but is display only, not in the history. A node derived from it keeps it. Without a saved orientation, the card and the 3D view both use the default: the molecule's two longest directions in the picture plane | Only where the orientation is kept |
| A28 | D75: the setting is app-wide and remembered, with three choices: show all (the default), hide the hydrogens bonded only to carbon (so hydrides, O–H, N–H and agostic C–H···M stay), or hide all | Only the choices offered |
| A29 | D76: the sides are saved with the transition (so they sync and survive a reload) but are layout, not history, like positions. Existing and imported transitions, and ones made without choosing, leave on the right and arrive on the left as before. Dragging an arrow's end onto a different box does not move the transition to that box; that would change the chemistry, so it is refused, and a new transition is drawn instead. A line standing for several transitions (A24) uses the sides of the first; moving its end moves them all | Only where the sides are kept, or what dragging an end may do |
| A31 | D80 defaults, Claude's, awaiting Jonas's confirmation: alignment sets are saved in the investigation (so they sync and survive a reload) but, like positions, are not in the history (as A27), and deleting a node takes it out of every set. Mirror image is off by default. At most 12 structures, for legibility. One atom list serves all structures when their elements match in order, with "Different atoms per structure" to split it. The reference is drawn in element colours and the others each in their own colour. The all-atom RMSD is measured on the structures as placed by the chosen atoms, not refitted; with no alignment both RMSDs are those of the stored coordinates | Only where sets are kept, the limit, or the defaults |

## 3. Proposals (all confirmed, D51)

Jonas confirmed all proposals on 2026-09-29 (D51). They keep their P-IDs so citations stay stable. Amendments: P1 by D52, P2 by D53, P4 by D54, P23 by D55.

| ID | Proposal | Where | Rationale / tradeoff |
|---|---|---|---|
| P1 | Node role (minimum / TS / unspecified), set by the user, drives imaginary-frequency warnings and direct-connection marking. Freq jobs can be added later (D52) | 02 §1 | Needed for the D19 warnings; the alternative is inferring role from calculation type, which is less reliable |
| P2 | "Reaction step" = conceptual position in the mechanism (intermediate or TS stage); nodes are assigned to at most one step; transitions realize moves between steps; direct intermediate connections allowed and marked "no TS" (D53) | 02 §1 | Reading of the accepted example (D12). Alternative: steps as conceptual transformations between stages |
| P3 | Deleting a node deletes its edges after a confirmation that lists them; rejected nodes keep their edges | 02 INV-7 | Avoids silent rewiring |
| P4 | Removing a group node returns members to no branch or their recorded origin branch, by user choice; alternatively delete with contents (D54) | 02 §8 | Reversible reconnection |
| P5 | A node belongs to at most one branch | 02 §2 | Keeps lineage unambiguous; shared structures are modelled by trunks or group nodes |
| P6 | Screen layout: outline, canvas, inspector, bottom drawer | 03 §1 | — |
| P7 | Branch-wide multi-xyz export | 03 WF-11 | — |
| P8 | "Export as zip" command | 03 WF-11 | — |
| P9 | Edges between branches drawn dashed | 03 §3 | — |
| P10 | Search by label, tag or note text | 03 §3 | — |
| P11 | Markdown rendering in notes | 04 FR-NODE-02 | — |
| P12 | Formula derived from coordinates only | 04 FR-NODE-06 | Prevents drift between formula and geometry |
| P13 | Parsed level-of-theory values kept; user edits stored as overrides | 04 FR-CALC-05 | Provenance |
| P14 | Unit list: kcal/mol, kJ/mol, eV, hartree | 04 FR-EN-07 | — |
| P15 | History is append-only; revert by a new change | 04 FR-HIST-03 | — |
| P16 | cclib as base parser for Gaussian and ORCA, plus custom code | 05 §3 | Gaussian resolved by D61 (own parser). ORCA and xTB resolved by D64 (own parsers) |
| P17 | CODATA 2018 constants in one module | 05 §5 | — |
| P18 | PNG and SVG for image exports | 05 §7 | — |
| P19 | One command starts the backend and serves the built UI | 06 §2 | — |
| P20 | SQLite with foreign keys on, WAL off | 06 §3 | Single-file consistency when zipping or syncing |
| P21 | SQLAlchemy 2.x + Alembic migrations, backup before migrating | 06 §3 | — |
| P22 | Lock file while an investigation is open | 06 §3 | Protects against sync conflicts |
| P23 | Installable package with a pre-built UI, environment managed by uv (D55); browser tab first, pywebview optional | 06 §4 | — |
| P24 | Python 3.11+ | 06 §4 | — |
| P25 | No admin rights needed to install | 06 NFR-PORT-02 | — |
| P26 | Orphaned copied files cleaned up after an interrupted import | 06 NFR-DATA-04 | — |
| P27 | Permissive third-party licences preferred | 06 NFR-LIC-01 | Jonas may share the tool |
| P28 | pytest, FastAPI test client, Playwright, Windows and Linux CI | 07 §1 | — |
| P29 | 0.01 kcal/mol tolerance for thermochemistry checks | 07 T-EN-04 | — |
| P30 | Stress case of 500 nodes / 1,000 edges for performance observation | 07 §8 | — |

## 4. Open questions

| ID | Question | Blocks | Recommendation |
|---|---|---|---|
| Q11 | File variants beyond each program's main output (e.g. .fchk, .gjf, ORCA .hess, CREST rotamer files); is SDF needed? | Phases 2 and 5 parser scope | Start with main outputs only; add variants on request |
| ~~Q32~~ | ~~Default duplicate tolerance (RMSD after alignment)~~ | — | Resolved by D62 |
| ~~Q33~~ | ~~Browser tab or native window; UI component library~~ | — | Resolved by D60 |
| Q34 | Can calculations or energies be entered manually without a file? | Phase 4 | No in v1 (EN-1), revisit if needed |
| ~~Q36~~ | ~~Can a group hold members from several branches that keep their branch? (Jonas, 2026-09-30)~~ | — | Resolved by D66 (all groups work this way; the existing filters are used, and a group filtered to one member is drawn as that node) |
| R1 | Sample output files and the reference thermochemistry script with its outputs, from Jonas (see 07 §2) | Phase 2 | Gaussian part received 2026-09-29, see /mnt/project-files/samples/README.md. Those files stay private: before the repository went public (2026-09-30) the test fixtures were replaced by public GoodVibes examples (MIT) and synthetic custom-basis chains, see tests/fixtures/README.md. Still missing: a failed job and SCRF (fallbacks there). ORCA, xTB and CREST: public samples found online and used instead (D64), see /mnt/project-files/samples/README.md; real outputs from Jonas are still welcome |

## 5. Exclusions (first version)

| ID | Exclusion | Source |
|---|---|---|
| X1 | No launching, queueing or monitoring of calculations | D9 |
| X2 | No multi-user collaboration. Sync between one person's own computers through a private Git repository is in scope (D71). | D6, D71 |
| X3 | No geometry editing in the 3D viewer | D20 |
| X4 | No internet hosting, login or access control | D42 |
| X5 | The app never picks pathways, representatives or branches from energies | D4, D18 |
| X6 | Parsing and warnings do not establish scientific correctness | HANDOFF |

## 6. Risks

| Risk | Mitigation |
|---|---|
| The concept model (P2, P5) may not match how Jonas thinks once used on real data | Build phase 3 against the Ru-CAAC example early and review with Jonas |
| Gaussian output variety breaks parsers | Real fixtures (R1); partial-parse behaviour (FR-IMP-04) |
| G_qh mismatches the reference script | Oracle test T-EN-05 against Jonas's script outputs |
| SQLite in a synced folder gets corrupted | Lock file (P22); document "one machine at a time"; for Git sync, the database is never merged and a conflict keeps both copies (D71) |
| Two-language stack (Python and TypeScript) is harder for Jonas to maintain | Keep all domain rules in Python (06 §2); UI stays thin |

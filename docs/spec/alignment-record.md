# Chembook3D: alignment record

Living record of the discovery interview (per HANDOFF.MD). Updated after each batch of answers. No specification or code until Jonas confirms alignment.

_Last updated: 2026-09-29, after the alignment summary. From here on, 08-decisions-and-open-questions.md is the maintained source of truth. Jonas confirmed the summary on 2026-09-29; the spec pack is written from it._

## Confirmed decisions
- D1. Product direction: a local-first, data-backed computational chemistry notebook for organizing and reviewing investigations of catalytic mechanisms. (HANDOFF)
- D2. Primary workspace: an infinite 2D canvas with structure nodes joined by typed edges, plus interactive 3D molecular visualization. (HANDOFF; workflow detailed in D33, D40, D43)
- D3. A conceptual reaction step is represented separately from the concrete structure-to-structure transitions that realize it. (HANDOFF)
- D4. Pathways are never assembled by picking the lowest-energy structure per step independently; energies may inform analysis but never rewrite or collapse branch lineage. (HANDOFF)
- D5. Phase now is discovery and specification only: no application code. (HANDOFF)
- D6. Primary user is Jonas, working alone. Others may use it later if it proves useful, so it should be shareable, but it is not built for collaboration now. (Batch 1)
- D7. Nodes can be created manually: the user types in metadata, coordinates and notes directly. (Batch 1)
- D8. Nodes can be imported from calculation output files. Programs: Gaussian (highest priority), ORCA, xTB, CREST. (Batch 1)
- D9. Calculations run on external machines. The app runs locally, never launches or monitors jobs, and only organizes, imports and parses. (Batch 1)
- D10. Target platforms: Windows and Linux. (Batch 1)
- D11. Reference scientific use case: olefin metathesis with asymmetric Ru-CAAC catalysts that carry bulky fragments with hindered rotation; some conformations and configurations carry over across parts of the catalytic cycle. (Batch 1)
- D12. Reference branch example accepted: rotamers A/B split at initiation and persist; olefin coordination splits them again into A1/A2/B1/B2. The four branches share the same reaction steps (coordination, [2+2] TS, metallacyclobutane, retro-[2+2] TS, product π-complex) but have separate concrete transitions. (Batch 2)
- D13. Branches may reconnect at a shared structure, and only the user decides that they do. After reconnection, several conformers can still be logged at that point (for example to find the lowest-energy candidate), but they are no longer separated by branch. (Batch 2)
- D14. Transition states leading into a reconnection are optional. The model must allow a reconnection with or without computed connecting TSs. (Batch 2)
- D15. Each distinct geometry is its own node. Calculations on the same geometry (for example single points at different levels of theory) stay on that node as separate calculation records, each with its own energy. (Batch 2)
- D16. Imported files are copied into the investigation. Each copy keeps origin metadata: the name of the external storage device or server, and the file's path on it. Live links to the original are not relied on. (Batch 2)
- D17. Interconversion between persistent branches (for example an A↔B rotation TS) is drawn as an ordinary transition edge. (Batch 3)
- D18. A reconnection point is shown as a group node on the canvas. It holds conformer nodes from the incoming branches; the user marks one as the representative the outgoing path continues from. The app may sort members by energy but never chooses the representative. (Batch 3)
- D19. Nodes, transitions and branches each have one status: planned, running externally, done, failed, rejected or superseded. Automatic validation warnings (for example a minimum with an imaginary frequency, or a TS without exactly one) are shown but never change a status. (Batch 3)
- D20. The 3D viewer is view-only in the first release: rotate, measure distances and angles, animate imaginary modes, overlay two geometries. Geometry is edited as text, by editing coordinate lines directly. (Batch 3)
- D21. Each investigation is one self-contained folder (a database plus the copied files) that can be zipped and shared. Backup is the user's responsibility, for example cloud-synced folders or git. (Batch 3)
- D22. Implementation language: Python (Jonas's strongest language, fits the marimo work). UI toolkit and database settled by D38; packaging is a proposal in the spec. (Batch 4)
- D23. A node's coordinates can be edited freely while it has no calculations. Once it has at least one, saving an edit creates a new node derived from the original, and the original keeps its calculations. (Batch 4)
- D24. Planned-node workflow: create a planned node with a guess geometry, then import the finished output onto it; the imported geometry and data replace the provisional ones (allowed by D23 because the node had no calculations yet). The change history keeps the guess. (Batch 4)
- D25. Multi-step Gaussian jobs (opt+freq, --Link1--): take the last geometry from the step with the highest level of theory, which is usually the job's last geometry. (Batch 4)
- D26. Failed or incomplete optimizations: import the last geometry and tag the node as "optimization incomplete". (Batch 4)
- D27. Relative energies are computed only between calculations at the same level of theory, never across different levels. A relative energy belongs to the edge between two nodes. (Batch 4; confirmed with the summary)
- D28. Energy unit defaults to kcal/mol and is configurable in settings. (Batch 4)
- D29. A change history is kept for records. (Batch 4)
- D30. Same level of theory means program, method or functional, basis set, dispersion correction and solvation model all match. Custom basis sets occur and must be representable. (Batch 5)
- D31. A single-point level of theory includes the level the geometry was optimized at (for example cc-pVQZ//cc-pVDZ). Two single points at the same level on geometries from different optimization levels are not the same overall level. (Batch 5)
- D32. Free energy is the preferred energy type, with electronic energy as fallback. All edges in one viewed cycle use the same level of theory and energy type, chosen from a drop-down of the levels and energy types that are available. (Batch 5)
- D33. Canvas proposal accepted: a colour per branch, status badges on nodes, energies on edges (can be hidden), manual layout plus a helper that lays out one branch left to right by step, and filtering by branch, status or reaction step. (Batch 5)
- D34. CREST ensemble import keeps the lowest N conformers (N configurable), with an easy way to select and remove unwanted conformers afterwards. (Batch 5)
- D35. Free energy at a composite level = single-point energy + thermal correction (G − E) from the frequency job at the geometry level. Custom basis sets are identified by a user-given name. (Batch 6)
- D36. Jonas's usual thermal correction treats frequencies below 100 cm⁻¹ specially; supporting it is wanted. The correction printed by the frequency job is an acceptable fallback if the custom one is hard. (Batch 6; refined by D41, D49)
- D37. Manual nodes need no mandatory fields beyond what the app needs to store them, but must support free-text notes. (Batch 6)
- D38. Architecture direction: option B, a Python backend (FastAPI, SQLite) with a web interface (TypeScript, React Flow canvas, 3Dmol.js), run locally. Jonas notes it could also be deployed over the internet later. (Batch 6; hosting scope settled by D42)
- D39. First-version minimum: energy profiles, a rotatable 3D structure view, a canvas image export, and easy access to xyz coordinates. An energy table is wanted but its design is open. (Batch 6)
- D40. The canvas offers switchable view modes that show nodes in different ways. (Batch 6; modes in D43)
- D41. Low-frequency treatment: frequencies below 100 cm⁻¹ are raised to 100 cm⁻¹ as in GoodVibes. The app recomputes this correction from the parsed frequencies and offers it as its own energy type next to the job's printed correction. (Batch 7)
- D42. The first version runs locally only: one user, no login. Hosting stays possible for future versions but is not designed in detail now. (Batch 7)
- D43. Node view modes: compact (label and status), energy (label and ΔG), structure (small rendered image of the geometry). (Batch 7)
- D44. Energy table: covers the viewed branch or cycle, one row per node (label, step, branch, level, E, G, ΔG from the reference node), exportable to CSV. (Batch 7)
- D45. Resume overview shows branch status, open items (planned and failed nodes), recent changes and notes per reaction step. (Batch 7)
- D46. Imported CREST conformers land in a group node. (Batch 7)
- D47. A near-duplicate geometry on import triggers a "possible duplicate" warning; the user decides, and the app never merges automatically. (Batch 7)
- D48. Phasing accepted: foundation → Gaussian import → canvas and 3D → energies → ORCA/xTB/CREST import and resume overview → hosting and collaboration later. (Summary)
- D49. Low-frequency treatment is Truhlar's method: vibrational frequencies below 100 cm⁻¹ are raised to 100 cm⁻¹. (Summary)
- D50. Jonas confirmed the alignment summary. (2026-09-29)
- D51. All proposals P1–P30 confirmed, with the amendments D52–D55. (Proposal review, 2026-09-29)
- D52. A frequency job can be imported onto an existing node later. The file may contain other steps too, so each step is checked against the node's geometry. (Amends P1)
- D53. Intermediates may be connected directly, with no TS in between. Such a direct connection is clearly marked "no TS" on the canvas, in the overview and in energy profiles. (Amends P2)
- D54. Deleting a group node offers either dissolving it (P4) or deleting it with all its contents. (Amends P4)
- D55. uv manages the Python environment. (Amends P23)
- D56. The Truhlar raise applies to vibrational entropy only. ZPE and thermal energy use the unmodified frequencies. Jonas's script `thermochem_corr_G16` is the reference implementation. (Jonas's script, 2026-09-29; resolves Q30b, confirms A4)
- D57. No concentration or standard-state correction; translational entropy is for an ideal gas at 1 atm, as in the script. (Jonas's script; resolves Q31)
- D58. G_qh temperature and cutoff are parameters (defaults 298.15 K, 100 cm⁻¹), stored with each value. Linear molecules give "n/a" with a warning in v1, as the script does not support them. (Jonas's script)

## Temporary assumptions
- A1. Deliverables go in the project's shared files under `spec/`.
- A2. Getting files from the external machines into reach of the app (download or sync) is done by the user outside the app.
- A3. ORCA, xTB and CREST imports extract the same kinds of results as Gaussian where the program provides them.

## Open questions (impact)
- Q11. Which file variants beyond each program's main output must be imported (e.g. .fchk, .gjf, ORCA .hess, CREST rotamer files), and whether SDF is needed. (Does not block phase 1; confirm before phase 2 and 5)
- Q34. Whether calculations or energies can be entered manually without a file. v1 assumes no. (Does not block; affects phase 4)
- Q32. Numerical tolerance for "near-duplicate" geometry. (Does not block spec; configurable setting, default chosen during implementation)
- Q33. UI details of the web stack (component libraries, packaging as a desktop window vs browser tab). (Does not block spec; implementation decision)

## Exclusions and non-goals
- X1. No launching, queueing or monitoring of calculations. (Batch 1)
- X3. No geometry editing in the 3D viewer in the first release. (Batch 3)
- X4. No internet hosting, login or access control in the first version. (Batch 7)
- X2. No multi-user collaboration in the first release. (Batch 1; later sharing is not excluded)
- X5. The app never chooses a pathway, representative or branch from energies. (D4, D18)
- X6. Parsing and warnings do not establish scientific correctness. (HANDOFF)
- Cloud services are not assumed.

## Agreed terminology
- Node: one distinct 3D geometry (with its charge, spin and composition). (D15)
- Calculation: a job run on a node's geometry, with its own method, program, files and results. A node can have many. (D15)
- Reaction step: a conceptual step of the mechanism, shared across branches. (D3, D12)
- Concrete transition: a link between two specific nodes that realizes a reaction step in one branch. (D3, D12)
- Reconnection: a user-declared point where branches meet and stop being tracked separately. (D13)
- Group node: a canvas node holding several conformer nodes at a reconnection, with one user-marked representative. (D18)
- Full glossary, including branch, split, pathway and interconversion: see 02-domain-model.md §1.

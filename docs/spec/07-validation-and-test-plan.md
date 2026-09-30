# 07 · Validation and test plan

_Chembook3D specification pack · v1.1 · 2026-09-29. Test IDs (T-…) map to requirements in [04](04-functional-requirements.md) and rules in [02](02-domain-model.md)._

## 1. Test levels

| Level | Scope | Tooling (P28) |
|---|---|---|
| Unit | Domain rules, parsers, thermochemistry, unit conversion | pytest |
| API | Backend endpoints against a temporary investigation folder | pytest + FastAPI test client |
| UI and end-to-end | Canvas, inspector, import preview, profile, 3D | Playwright |
| Portability | Zip on one OS, open on the other | CI matrix on Windows and Linux |

## 2. Test fixtures (dependency on Jonas)

Real output files are needed. **Request to Jonas before phase 2 (R1):** a small anonymised set of:
- Gaussian: opt+freq minimum; opt+freq TS; a failed or truncated optimization; a `--Link1--` job with a single point; a `gen`/`genecp` custom-basis job; a job with a low-frequency mode under 100 cm⁻¹; one with SCRF solvation and empirical dispersion.
- ORCA: opt+freq, and a single point on a DFT geometry.
- xTB: optimization with frequencies.
- CREST: one conformer ensemble file.
- **The reference thermochemistry script** `thermochem_corr_G16` (saved from the thread at `reference/thermochem_corr_G16.py`; Jonas to confirm it matches his copy), plus its `.thch` output for each Gaussian frequency fixture (the oracle for T-EN-05).

Synthetic edge-case files (truncated, missing route, garbled numbers) are made from these.

## 3. Branch-lineage and pathway tests (the scientific invariants)

Build the Ru-CAAC example ([02 §6](02-domain-model.md)) as a fixture investigation.

| ID | Case | Expected |
|---|---|---|
| T-BR-01 | Lineage of A1-S3 | A1 → A → T |
| T-BR-02 | Split A at S1 into A1 and A2 | Two branches with parent A. The split node is recorded. |
| T-BR-03 | Add the A-S3 → rot-TS → B-S3 interconversion | Branch of every node unchanged (INV-4) |
| T-BR-04 | Reconnect A1…B2 S6 nodes into group G6 with outgoing R | R parents = {A1, A2, B1, B2}. Members keep their branch (D66). The history has one reconnect entry (INV-3). |
| T-BR-05 | Group with no representative | Its outgoing edges show "n/a" (EN-7) |
| T-BR-06 | Set a representative, then change the energy view | The representative is unchanged; energies never alter it (EN-10) |
| T-BR-07 | Sort group members by G | The order changes and the representative does not |
| T-BR-08 | Attempt to get a profile across nodes with no connecting edges | Not possible: the profile selection only follows edges (INV-2) |
| T-BR-09 | B1-S3 has a lower G than A1-S3 | A1's profile still uses A1-S3; nothing is re-routed |
| T-BR-10 | Reconnection with no TSs into the group | Allowed (D14) |
| T-BR-11 | Mark A2's S2 TS as superseded while a second TS exists | Both are kept; the filter hides superseded ones; the edges are intact |
| T-BR-13 | Connect A1-S1 directly to A1-S3 | Edge is dotted with "no TS" on the canvas, listed in the overview, and drawn as a "no TS" segment in the profile (D53) |
| T-BR-14 | Delete group G6: dissolve, then (on a copy) delete with contents | Dissolve keeps members as nodes; delete with contents removes group, members, their calculations and edges after a listing confirmation (D54) |
| T-BR-12 | Delete a node with 3 edges | The confirmation lists 3 edges; after confirming, they are gone and no other edges changed (INV-7) |
| T-BR-15 | Add A1-S5 and a member of another group to G6 | Both become members; A1-S5 has no branch and came from A1; the other group loses the member (and its representative, if it was one); one history entry on G6 (A20) |
| T-BR-16 | Switch an expanded group to a vertical line, a horizontal line and back to the grid, and reload | Members stand in one column, then one row, then in the grid again; the choice is still there after reloading (A21) |
| T-BR-17 | Groups IM1, TS1, IM2 with one conformer per branch 1 and 2, joined conformer to conformer; collapse them, then filter to branch 1 | Collapsed: one line between each pair of groups. Filtered: IM1-1, TS1-1 and IM2-1 drawn as plain nodes at the groups' places, joined by their own edges with their own ΔG; branch 1's profile is IM1-1 → TS1-1 → IM2-1 (D66, A23, A24) |
| T-BR-18 | Open an investigation made before D66 with a reconnection | Members are back in the branches they came from (A22) |
| T-BR-19 | Collapse a group, switch to the structure view mode, then pick a representative | No structure on the group until a representative is picked, then the representative's structure; none in compact mode (D68) |

## 4. Identity and geometry tests

| ID | Case | Expected |
|---|---|---|
| T-ID-01 | Edit xyz on a node without calculations | Updated in place; the history holds the old geometry |
| T-ID-02 | Edit xyz on a node with one calculation | A new derived node; the original is unchanged (ID-5) |
| T-ID-03 | Import onto a planned node | The geometry is replaced; the history holds the guess (ID-6) |
| T-ID-04 | Import an SP whose geometry differs from the node's | W-GEOM, with a "create derived node" offer (ID-7) |
| T-ID-05 | Import a geometry within tolerance of an existing node | The possible-duplicate prompt; nothing is merged without the user's choice (ID-8) |
| T-ID-06 | Same file imported twice | A checksum notice |
| T-ID-07 | Invalid xyz line | The error names the line; no save |

## 5. Import and parsing tests

| ID | Case | Expected |
|---|---|---|
| T-IMP-01 | Gaussian opt+freq | Geometry, level, E, ZPE, H, G, T, P, frequencies and termination all match values read by hand from the file |
| T-IMP-02 | Gaussian TS | Imaginary count 1; W-IMAG-TS absent when role = TS |
| T-IMP-03 | Truncated opt | Last geometry; `optimization-incomplete` tag; W-TERM |
| T-IMP-04 | `--Link1--` opt then SP at a higher level | Two calculations; the SP has composite level SP//opt; the geometry is from the default step (the last), and the override works |
| T-IMP-05 | `genecp` job | The preview asks for a custom basis name; equality uses the name |
| T-IMP-06 | Route line removed | Geometry imported; W-PARSE lists the missing fields |
| T-IMP-07 | Cancel in the preview | No records and no copied files |
| T-IMP-08 | Kill the process during import | No partial records on reopen (NFR-DATA-04) |
| T-IMP-09 | CREST 40 conformers, N = 10 | A group with 10 members; unticking 2 gives 8; removal after import works |
| T-IMP-11 | Import a separate opt+freq file onto a node that has no freq, where the opt does not move the geometry, then one where it does | First: steps attach to the node. Second: derived-node offer from the geometry-changing step (D52) |
| T-IMP-10 | ORCA and xTB fixtures | Same checks as T-IMP-01, where the program prints the values |

## 6. Energy tests

| ID | Case | Expected |
|---|---|---|
| T-EN-01 | Edge between nodes at different levels | "n/a" (EN-3) |
| T-EN-02 | Composite G | Equals E_SP + (G − E)_freq by hand calculation (EN-4) |
| T-EN-03 | G with no frequency calculation at the geometry level | "n/a" and W-NOFREQ |
| T-EN-04 | G_qh on a file with no modes below 100 cm⁻¹, at the job's T | Equals the job-printed G correction within 0.01 kcal/mol (P29) |
| T-EN-05 | G_qh on files with low modes, at default and at a non-default T and cutoff | Matches Jonas's reference script `thermochem_corr_G16` "Thermic correction" within 0.01 kcal/mol (D56, D58) |
| T-EN-06 | Unit switch | Display changes; stored values are unchanged |
| T-EN-07 | Level drop-down | Lists only combinations that have values |
| T-EN-10 | Linear molecule frequency job | G_qh "n/a" with a warning (D58) |
| T-EN-11 | A file with two frequency jobs | G_qh uses only the selected step's frequencies |
| T-EN-09 | Freq imported later at a different level than the geometry level | The preview flags the mismatch; G at the composite level uses only a freq at the geometry level |
| T-EN-08 | Energy table CSV | Columns as FR-EN-06; values equal the on-screen values |
| T-EN-12 | IM1 on no branch splits into branches 1 and 2, which join again at P on no branch | Each branch's pathway is IM1, TS1-n, IM2-n, P, with the profile relative to IM1; a node on another branch leading into IM1 is not entered, one branchless node leading in is followed, and two leading in stop the trace at IM1 (D67) |
| T-EN-13 | Branches 1 and 2 split at IM1 (no branch), merge at IM3 (no branch) and split again | Each branch's pathway runs IM1, …, IM3, …; a new branch after IM3 whose parent is branch 1 traces back through IM3 to IM1, and one with both branches as parents starts at IM3. The same when the merge is a reconnection group G with outgoing branch R: branches 1 and 2 run IM1, …, G, then R's nodes; R's own pathway starts at G (D67) |
| T-EN-14 | Collapse groups {A-S1, B-S1} and {A-S2, B-S2}, then pick A-S1 and A-S2 as representatives | The line between them shows "2 edges" and no energy until both representatives are picked, then ΔG of A-S1 → A-S2 (D70) |

### Free species (D69)

| ID | Case | Expected |
|---|---|---|
| T-SPC-01 | A → B (+ propene) → TS → C (− ethylene), reference A, then reference C | Every point equals X(n) plus leaving minus joining species minus X(ref); walking backwards swaps the signs |
| T-SPC-02 | ΔX on an edge with species | Includes the edge's own species |
| T-SPC-03 | A species lacks the selected level | The point and the edge are "n/a" with the species named |
| T-SPC-04 | Cycle closed back to A with "+ propene, − butene" | The closing point is the reaction energy; the table lists A twice |
| T-SPC-05 | Edge missing a leaving ethylene, then a charge change | W-BALANCE names C2H4, then the charges |
| T-SPC-06 | Energy mode with reference A, propene joining on A → B and ethylene leaving on TS → C; then the cycle closed; then a pathway TS → C shown apart from A → B | Every card after an edge keeps its species and matches the profile; A stays at zero in the closed cycle; the separate pathway starts from TS's balance (D72) |

## 7. Portability, data safety and UI

| ID | Case | Expected |
|---|---|---|
| T-OPS-01 | Create on Windows, zip, open on Linux (and the reverse) | Everything is present (FR-INV-03) |
| T-OPS-02 | Delete the original source file after import | The copy still opens (FR-FILE-04) |
| T-OPS-03 | Open an investigation already open elsewhere | A lock warning (P22) |
| T-OPS-04 | Open an investigation from an older schema | A backup is made, then the migration runs (NFR-DATA-03) |
| T-OPS-05 | Backend reachable from another machine? | No (NFR-SEC-01) |
| T-SYNC-01 | Open an investigation saved by a newer schema | The newer-version message; no backup, migration or lock (FR-SYNC-01) |
| T-SYNC-02 | Link to an empty bare repository, then to one that is not empty | Pushed without the lock file or `backups/`; the second is refused (FR-SYNC-02) |
| T-SYNC-03 | Clone into a second folder, change and sync there, open the first | The first shows the change (FR-SYNC-03, 04, 05) |
| T-SYNC-04 | Change both copies, sync, keep either copy | Nothing merged; the other copy is saved (FR-SYNC-06) |
| T-SYNC-05 | Clone with `core.autocrlf=true` | Copied output files are byte for byte identical (D71) |
| T-SYNC-06 | Remote unreachable | Opens anyway and reports it; the next sync pushes (FR-SYNC-04) |
| T-SYNC-07 | Open a linked investigation from an older schema | Asks before upgrading (FR-SYNC-07) |
| T-SHARE-01 | Export the demo, open the file from disk with no server | Canvas, node details, calculations and a profile show; changing level and type works; no request leaves the page (FR-SHARE-01–03) |
| T-SHARE-02 | Search the exported file for the investigation folder and the original paths of imported files | Not found (FR-SHARE-04) |
| T-UI-01 | Resume overview on the example | Branch statuses, open items, recent changes and step notes all shown; clicking navigates (FR-OV-01) |
| T-UI-02 | View modes and filters | Switching modes and filters never changes stored data |
| T-UI-03 | 3D | Rotate, measure, animate the imaginary mode, overlay two nodes, copy xyz |
| T-UI-04 | Canvas and profile image export | Files are produced and open in a standard viewer |

## 8. Performance observation (no targets)

Per NFR-PERF-01, record (not pass/fail): load time, canvas pan and zoom smoothness, and import time for (a) the reference example and (b) a synthetic investigation of 500 nodes and 1,000 edges (P30: size to be confirmed). Report the results to Jonas to set targets.

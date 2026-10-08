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
| T-BR-20 | Take B2-S6, the representative of G6 with an edge into G6, out of the group; then try to take out the last member of a two-member group after removing one | B2-S6 stays a node on B2 beside G6 with its edges; G6 has 3 members, no representative, and B2 is no longer an incoming branch (R keeps B2 as a parent); one history entry; the last member is refused (D88) |
| T-BR-21 | Reverse G6's members, add A1-S5, take out the second member; open an investigation made before D89 | The new order is kept and shown by every layout, with one history entry; A1-S5 comes last; the rest keep their order; old groups keep the order their members were made in (D89) |

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
| T-ID-08 | Save empty xyz on a node without calculations, then on one with a calculation | The first has no coordinates and the history holds the old ones; the second is refused and keeps its coordinates (D90) |
| T-ID-09 | "Use this structure" on a scan path node, then on a node with a Gaussian relaxed scan (D112) | The path node keeps its id: its coordinates and its scan's geometry and energy become those of the point shown, with history entries; the other node is unchanged and a derived node with that structure is made |
| T-OVL-01 | Overlay a structure and its rotated, translated copy with extra atoms, on chosen atoms | RMSD over the chosen atoms 0; paired atoms of different elements, fewer than three atoms or numbers out of range are refused with the reason (FR-3D-04) |
| T-OVL-02 | Overlay a structure and its mirror image, with and without "Allow mirror image" | Without: RMSD above 0, not mirrored; with: RMSD 0 and marked mirrored |
| T-OVL-03 | Save an alignment set, reopen the investigation, add a node, delete a node | The set keeps each node's atoms, gains the new one, loses the deleted one, and writes no history (FR-3D-07, A31) |
| T-MAT-01 | Match public metal complexes (morfeus's SambVca set) against copies shuffled, turned, moved and jiggled by up to 0.1 Å; symmetric ones turned only (D113) | Every atom is matched to itself; a symmetric complex may be matched through its symmetry but then fits and keeps every bond as well |
| T-MAT-02 | Match the first CREST conformer of glycerol against other conformers, shuffled | Every atom matched to itself, no bond changes, no centre inverted, although matching by distance alone would swap hydrogens |
| T-MAT-03 | Move one of a CH2's hydrogens onto the other arm's oxygen, shuffle, match; then every hydrogen moved onto another atom | One bond forms and one breaks, every atom matched to itself, the match confident; with every hydrogen moved, the match is doubtful with the number of bond changes |
| T-MAT-04 | `POST /api/atom-match` on two nodes; with a pair fixed by hand; with a node without coordinates; with different atoms or a bad pair | 1-based mapping and the end renumbered and fitted on the start; the fixed pair kept; each refusal says why |
| T-STER-01 | %V_bur of the 18 complexes in morfeus's test data, with SambVca's values (among them Ni(CO)3 with an NHC and an NHC iridium complex) | Within 0.3 of SambVca at its standard settings (FR-STER-01) |
| T-STER-02 | A probe atom inside one quadrant, and the same structure turned and shifted | Buried only in that quadrant, mostly in its −z octant; the same values and map both ways; their difference map is zero (FR-STER-03, FR-STER-05) |
| T-STER-03 | A centroid centre; hydrogens, CRC radii, the scale and the mesh changed; settings and atoms out of range; orientation atoms that fix no frame | Each setting changes the value the expected way; refusals give the reason (FR-STER-01) |
| T-STER-04 | Change the settings, the colour scale, a node's atoms and its coordinates; share atoms between nodes; reopen; delete a node | Out of date with the right reason, but not for the colour scale; sharing is refused when the elements differ; kept on reopen; no history; the deleted node leaves the profile (FR-STER-02, FR-STER-04, A32) |
| T-STER-05 | Save the comparison CSV, with one node not in the profile | Values, settings and atoms on every row; the missing node says why (FR-STER-05) |
| T-SEL-01 | Two TSs ΔΔG‡ = RT ln 10 apart at 298.15 K, one per outcome | 10 : 1 (90.9 %), ee 81.8 %, the same with only the lowest TS; no ee when "ratio only" (FR-SEL-05) |
| T-SEL-02 | Two equal TSs in one outcome against one equal TS in the other | 2 : 1 and ΔΔG‡ = RT ln 2 summed, 1 : 1 with only the lowest TS; each TS's share given (FR-SEL-03) |
| T-SEL-03 | A TS group as an outcome; a member added to the group | Every member counts, the added one too (FR-SEL-01) |
| T-SEL-04 | The selectivity's own temperature (233.15 K), then none, then G | G_qh as the reference formula gives at 233.15 K and the ratio at that T; the setting's temperature when none; for G, a note that it is at the job temperature (FR-SEL-04) |
| T-SEL-05 | A TS without a value at the level | n/a, naming the TS and why; no numbers (FR-SEL-02) |
| T-SEL-06 | Different atoms, a different charge, a TS in two outcomes (also through a group) | Refused, with the reason (FR-SEL-02) |
| T-SEL-07 | Experiment 95 : 5, then an amount for one outcome only | 95 %, ΔΔG‡ = RT ln 19, ee 90 %; then not compared, with a note (FR-SEL-05) |
| T-SEL-08 | Create, rename, change notes, save without change, delete; delete a TS | History entries for each change only; the TS leaves its outcome (FR-SEL-06) |
| T-SEL-09 | Two TSs on routes from a reference A, one before and one after ethylene leaves (different atoms); no reference, then A as reference, a species without a value, a TS not joined to A, a species as reference, A deleted | Refused for different atoms without a reference; with A, n/a naming ethylene until it has a value, then each TS balanced (− propene; − propene + ethylene) and ΔΔG‡ from the balanced values; the unjoined TS refused by name; a species refused as reference; the change in the history; n/a once A is deleted (FR-SEL-07) |
| T-TOF-01 | Cycles of one to four steps, exergonic and endergonic, solved exactly at steady state from Eyring rate constants for every forward and backward step | The energetic-span TOF equals the steady-state flux; the degrees of TOF control of the TSs add up to one, as do those of the intermediates; a cycle 900 kcal/mol downhill does not overflow (FR-TOF-03) |
| T-TOF-02 | 0, 20‡, 5, 15‡ with ΔG_r −10; and 0, 20‡, −20, −5‡ with ΔG_r −15 (kcal/mol) | TDTS 20 and TDI 0, δE 20; then TDTS before TDI, δE = 20 + 20 − 15 = 25 (FR-TOF-03) |
| T-TOF-03 | The same first cycle drawn with a substrate joining and a product leaving, through the API | ΔE_r −10.00 from the balance, the TOF by hand, TDTS and TDI, the table and its CSV with the same text, the cycle's profile (FR-TOF-01, 02, 04) |
| T-TOF-04 | The turnover's own temperature, 350 K | The TOF by hand at 350 K (FR-TOF-04) |
| T-TOF-05 | A "no TS" connection between two intermediates in the cycle | The TOF by hand with that intermediate, and a note that it is an upper bound (FR-TOF-03) |
| T-TOF-06 | A point without a value; an open pathway or one with a gap saved; a cycle without TSs; no pathway; no level | n/a naming the point; refused when saved; refused with the reason; incomplete (FR-TOF-01, 02) |
| T-TOF-07 | Two cycles 1 kcal/mol apart at the TDTS; then at different temperatures; compared with itself; the other deleted | Effective ΔΔG‡ 1.00 kcal/mol and the percentages; not compared, with the reason; refused; the comparison cleared (FR-TOF-05) |
| T-TOF-08 | Create, rename, change notes, save without change, delete; delete a node on the pathway | History entries for each change only; the result says the pathway lost a node (FR-TOF-05) |
| T-TOF-09 | A TS group in the cycle, without and then with a representative | n/a, then valued by the representative, which becomes the TDTS (FR-TOF-03, EN-7) |
| T-TOF-10 | A precatalyst before the node the pathway returns to | Left out of the cycle, with a note (FR-TOF-01) |
| T-NOTE-01 | Formatted HTML with scripts, styles, handlers, `javascript:` links, outside pictures and unknown tags | Only the allowed tags and attributes stay, with their text (FR-NOTE-02) |
| T-NOTE-02 | An SVG as ChemDraw saves one, with a script, handlers, `javascript:` and outside links, `@import`, a foreignObject and `<set>`; SVG without namespaces; entities | The drawing stays, nothing that runs or loads stays; namespaces added; entities refused (FR-NOTE-03) |
| T-NOTE-03 | Pictures stored twice; PDF and an over-large file; a note naming a picture not stored; a note on a free species; a corner or colour not offered | Stored once and served with a policy forbidding scripts; the rest refused with the reason (FR-NOTE-01, FR-NOTE-03) |
| T-NOTE-04 | Create two notes, change text and corner, collapse, set a width out of range, save without change, delete one, delete the node | History entries for text, corner, create and delete only; width clamped; the node's notes go with it (FR-NOTE-01, FR-NOTE-04) |
| T-NOTE-05 | Export a copy with a note holding an SVG, with an unused picture in the investigation | The note and its picture as a data URL are in the copy, the unused picture is not (FR-NOTE-05) |
| T-NOTE-06 | Set a note floating with a line, offsets and a size beyond the limits, then free with its height cleared; send an unknown placement and non-whole numbers | Placement, offsets (clamped to ±5000) and size (clamped) are kept, the height is cleared, the bad values are refused, and none of it is in the node's history (FR-NOTE-06, A37) |

## 5. Import and parsing tests

| ID | Case | Expected |
|---|---|---|
| T-IMP-01 | Gaussian opt+freq | Geometry, level, E, ZPE, H, G, T, P, frequencies and termination all match values read by hand from the file |
| T-IMP-02 | Gaussian TS | Imaginary count 1; W-IMAG-TS absent when role = TS |
| T-IMP-03 | Truncated opt | Last geometry; `optimization-incomplete` tag; W-TERM |
| T-IMP-04 | `--Link1--` opt then SP at a higher level | Two calculations; the SP has composite level SP//opt; the geometry is from the default step (the last), and the override works |
| T-IMP-05 | `genecp` job | The preview asks for a custom basis name; equality uses the name |
| T-IMP-05b | Saved `genecp` basis set (D94) | The dialog shows each element's contraction and ECP; the downloaded `.gbs` gives back every printed exponent, coefficient and ECP term, and only the ticked elements |
| T-IMP-06 | Route line removed | Geometry imported; W-PARSE lists the missing fields |
| T-IMP-07 | Cancel in the preview | No records and no copied files |
| T-IMP-08 | Kill the process during import | No partial records on reopen (NFR-DATA-04) |
| T-IMP-09 | CREST 40 conformers, N = 10 | A group with 10 members; unticking 2 gives 8; removal after import works |
| T-IMP-11 | Import a separate opt+freq file onto a node that has no freq, where the opt does not move the geometry, then one where it does | First: steps attach to the node. Second: derived-node offer from the geometry-changing step (D52) |
| T-IMP-10 | ORCA and xTB fixtures | Same checks as T-IMP-01, where the program prints the values |
| T-IMP-12 | Import a folder holding an opt+freq file, its single point, an output of another structure, an input file, a file imported before and a planned node's output | The single point attaches to the node the opt+freq file creates (in either order), the planned node is finished, the other output is a new node, the input is counted as no output, the old file is skipped; one transaction, one batch history entry |
| T-IMP-13 | Batch name matching with nodes TS1-2, TS1-2' and TS1-2'' | `TS1-2'_SPQZ.out` goes to TS1-2', `TS1-2''.out` to TS1-2''; `TS1-2__.out` only to TS1-2'' (marked close); a name fitting two nodes waits for a choice |
| T-IMP-14 | A batch in which one file fails while being written | Nothing is written and no copied file is left (NFR-DATA-01) |
| T-IMP-15 | Import a DFT optimization that starts from the geometry a node's lower-level optimization ended on, then a single point on its result (D100) | No derived node; the node's geometry is the DFT result, the earlier optimization stays as a pre-optimization (PRE-OPT in the preview); the single point reads `SP // DFT` and no `DFT // pre-optimization` level appears. A restart at the same level, or an optimization from another geometry, still makes a derived node |
| T-IMP-16 | Batch-import such a DFT optimization and its single point under names that match no node (D100) | The optimization goes to the pre-optimized node by its first geometry, the single point by its geometry; one node, `SP // DFT` |
| T-IMP-17 | Open an investigation imported before D100 with the DFT optimization on the pre-optimized node's geometry and single points at the pre-optimization's level (D100) | Once, after the backup: the node takes the DFT geometry; a single point on the DFT geometry reads `SP // DFT`, one on no optimization's geometry has no geometry level, one set by hand is kept; changes made later are not undone at the next open; a new investigation needs no repair; a failing repair does not stop the investigation opening |
| T-IMP-18 | Undo, from the history, imports that made a node, a derived node and a CREST group, one that finished a planned node, one that attached a single point, one with an incomplete optimization, and a DFT optimization that continued a pre-optimization (also one moved by the D100 repair) (D102) | Each node, calculation and stored copy the import made is gone, the node's fields, coordinates and tags are as before the import, a field changed by hand later keeps its value, the import's entries stay and the undo is recorded; an edge, a later import onto the node it made, a single point at its level or a later optimization continuing from it blocks the undo with a message; the preview and a refused undo write nothing; the copy is removed only after the commit |
| T-IMP-19 | Undo a batch of a planned node's file, an optimization and its single point (D102) | All three are undone last first and the batch's line can no longer be undone; a later import onto the node the batch made blocks it and nothing is undone; one file of the batch can be undone alone; a batch written before D102 is found by its files' names |
| T-IMP-20 | Batch-import two TS searches with frequency jobs that read the basis set from a checkpoint and an opt+freq file that names its own, giving one basis set for the batch and another for one file (D104) | The preview counts two files without a basis set and shows the given one in their levels; after import both steps of each TS file have their basis set (the file's own for the overridden one), the parsed level keeps the empty basis with the given one, no W-PARSE and no "edited"; the other file keeps its own basis set; left blank, a file imports with an empty basis and W-PARSE as before |
| T-IMP-21 | Import an xTB 6.7.1 `xtbscan.log` (a 13-point dihedral scan of 1,2-dichloroethane) from the folder holding its output, then uploaded alone, then the run's `xtbopt.log` (D112) | From the folder: one relaxed scan at GFN2-xTB with the output's charge and version; the node is point 5, the top, with its energy; 13 converged points play. Alone: the same at xTB's defaults with a W-PARSE warning. `xtbopt.log` is refused with a hint |
| T-IMP-22 | Import a two-stage `path.xyz` whose comment lines give the stage and the xtb call with `--alpb toluene --chrg 1 --uhf 1` and whose energies rise to the end (D112) | One relaxed scan at GFN2-xTB/ALPB(toluene), charge 1, multiplicity 2; each point names its stage; no top, so the node takes the middle point |

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
| T-EN-15 | Turn on the 1 M standard state; view G and G_qh of a node, an edge between two nodes, and an edge where a free species joins | G and G_qh rise by RT ln(V_m / 1 L mol⁻¹) = 1.894 kcal/mol at 298.15 K (and follow the temperature setting); E and H and the one-to-one edge are unchanged; the edge with a joining species falls by one correction; the drop-down, badge and table name 1 M (D95). With it off, every value equals the reference script's (T-EN-05) |
| T-EN-16 | Node X → group G {g1, g2} → TS → Y; groups A {a1, a2} and B {b1, b2} joined a1 → b1 and a2 → b2; a reconnection G of IM2-1 and IM2-2 with the edge G → TS3 | From X the pathway stops with G (group), g1 in G and g2 in G; X, g1 continues to TS and Y, as does X, G; from A it runs to B; from a2 to b2 only; from IM2-1 on through TS3; branch 1 still runs TS1-1 → G → TS3. Y → TS → g2 → X is a profile followed backwards, with only g2 → X "no TS"; g1 → G, g1 → g2 and a1 → b2 are refused (D108) |
| T-EN-17 | Node X → group G {ts1, ts2} → Y, ts1 and ts2 marked TS | With no representative G's point is not a TS and both edges are "no TS" in the profile and the edge list; with ts2 as representative G's point is a TS and neither edge is "no TS" (D111) |

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
| T-RUN-01 | Launcher on `main` behind its remote; on another branch; with local changes | Pulled; not pulled, with the reason shown (FR-RUN-02) |
| T-RUN-02 | Launcher with an interface built from the current `frontend/` tree, then from an older one | No rebuild; a rebuild (FR-RUN-02) |
| T-RUN-03 | Launcher when the app already answers on its port | Only a tab opens; nothing is pulled or started (FR-RUN-01) |
| T-RUN-04 | Started by the launcher, the last tab says goodbye; started from a terminal, the same | The first stops after the grace time; the second keeps running (FR-RUN-03) |
| T-RUN-05 | Shut down from a page on 127.0.0.1, and from a foreign origin | The first closes the investigation and stops the server; the second is refused (FR-RUN-04) |
| T-SYNC-01 | Open an investigation saved by a newer schema | The newer-version message; no backup, migration or lock (FR-SYNC-01) |
| T-SYNC-02 | Link to an empty bare repository, then to one that is not empty | Pushed without the lock file or `backups/`; the second is refused (FR-SYNC-02) |
| T-SYNC-03 | Clone into a second folder, change and sync there, open the first | The first shows the change (FR-SYNC-03, 04, 05) |
| T-SYNC-04 | Change both copies, sync, keep either copy | Nothing merged; the other copy is saved (FR-SYNC-06) |
| T-SYNC-05 | Clone with `core.autocrlf=true` | Copied output files are byte for byte identical (D71) |
| T-SYNC-06 | Remote unreachable | Opens anyway and reports it; the next sync pushes (FR-SYNC-04) |
| T-SYNC-07 | Open a linked investigation from an older schema | Asks before upgrading (FR-SYNC-07) |
| T-SHARE-01 | Export the demo, open the file from disk with no server | Canvas, node details, calculations and a profile show; changing level and type works; no request leaves the page (FR-SHARE-01–03) |
| T-SHARE-02 | Search the exported file for the investigation folder and the original paths of imported files | Not found (FR-SHARE-04) |
| T-MCP-01 | A change from one client, then reads, failed requests and computations that change nothing | Other clients are told of the change, the one that made it is not; the rest count no change (FR-MCP-04) |
| T-MCP-02 | A node created and changed by Claude, then by the user | History source "claude", then "manual" (FR-MCP-06) |
| T-MCP-03 | Report two nodes, an edge and an energy view; delete one node | Claude reads them by name with the level, type and reference; the deleted one is left out (FR-MCP-05) |
| T-MCP-04 | Claude asks to delete a node with an edge; answered without a page origin, then confirmed; answered again | Described as "Delete node “TS1-2” with 1 edge"; refused (403); deleted with the edge, history "claude"; already done (FR-MCP-03) |
| T-MCP-05 | Requests refused, expired, made while the investigation closes; unknown actions, odd ids, coordinates of a node without them or with calculations | Nothing changes; expired; refused with the reason (FR-MCP-03, D90) |
| T-MCP-06 | Every API route | Either a tool or deliberately not offered; every DELETE asks, except the three of A38; read tools change nothing (FR-MCP-02) |
| T-MCP-07 | Tools through the app: create, rename, xyz, an empty coordinates save, a refused value, the selection | Same answers as the app; the empty save is pointed to remove_coordinates (FR-MCP-02) |
| T-MCP-08 | Delete through the server, refused and then confirmed in the page; then with no answer | Nothing changes, then deleted; then no change after the time limit (FR-MCP-03) |
| T-MCP-09 | App not running; an address on another computer | Said plainly; refused (FR-MCP-01) |
| T-MCP-10 | `chembook3d mcp --url …` started over stdio as Claude Code starts it | Tools listed with read-only and destructive hints, instructions given, a node created in the running app, invalid input refused (FR-MCP-01) |
| T-MCP-11 | UI: Claude creates and renames a node, the user selects it, Claude asks to delete it twice | The canvas follows without a reload; the selection is reported; Refuse keeps it, Confirm deletes it; History shows "claude" (FR-MCP-03, 04, 05) |
| T-PANEL-01 | Ask for a terminal from another site's page, for a foreign host name, from another computer, without a token, with a spent or expired token | Refused before anything starts (FR-PANEL-03) |
| T-PANEL-02 | Start the panel with a stand-in for `claude` | Started in the investigation's working folder with only the fixed arguments and the terminal's size; typing reaches it; its exit is reported; `--continue` when continuing; closing the page stops it (FR-PANEL-01, FR-PANEL-02) |
| T-PANEL-03 | The written settings, with and without the notebook server | Shell, editing and web tools denied; the server and its read tools listed only when present (FR-PANEL-02, FR-PANEL-04) |
| T-PANEL-04 | UI: open the panel, start, type, hide and show, let it end, continue, stop | The output stays while hidden; each end says why; continuing passes `--continue` (FR-PANEL-01) |
| T-CLOUD-01 | Write a job with a node and a text file; again with the same name; bad file names; a node without coordinates | Inputs, charge and multiplicity in job.md; the second gets "-2"; names with paths, reserved names or spaces refused, as is the node (FR-CLOUD-01) |
| T-CLOUD-02 | Start a job not linked to GitHub, without Claude Code, with a newer version on GitHub | Refused with the reason; nothing pushed or started (FR-CLOUD-02) |
| T-CLOUD-03 | Start a linked job with a stand-in for `claude --cloud`; start it again | One commit on GitHub with only the job and the cloud files, the database left for the sync; `--cloud` run in a terminal in the investigation folder with only the job in its task; session id and link recorded; the second start refused (FR-CLOUD-02, FR-CLOUD-03) |
| T-CLOUD-04 | A second clone pushes outputs, result.json, a change to the database and a stray file on its own branch; check, fetch, import | Finished with the summary; only outputs and result.json copied, the database change reported and not copied; the output imports as xTB (FR-CLOUD-03, FR-CLOUD-04) |
| T-CLOUD-05 | `claude --cloud` fails (not signed in), then works; it keeps following the session; it names no session in time | launch_failed with its message without terminal codes and how to start it in a terminal, then running with no second commit; running with the session named; launch_failed saying it waited for an answer (FR-CLOUD-02, FR-CLOUD-03) |
| T-CLOUD-08 | `claude --cloud` asks whether the folder is trusted; the user answers in the Claude panel (backend and UI test) | waiting_for_answer with its screen; the panel opens with that screen, only for the app's page and with a one-time token; Enter there gives running with the session's link, and nothing is left to answer (FR-CLOUD-03) |
| T-CLOUD-06 | Create, start and fetch from another site's page | Refused (FR-CLOUD-06) |
| T-CLOUD-09 | Write the cloud files in a folder whose remote is a GitHub address with a token | The instructions name the https address without the token and how to add it as origin (FR-CLOUD-07) |
| T-CLOUD-10 | `claude --cloud` uploads the folder; message the session, with characters cmd.exe treats specially; message a draft job; a refused message | Running with a warning about the Claude GitHub App; the message reaches the session word for word; refused with no session; the CLI's error passed on (FR-CLOUD-07) |
| T-CLOUD-07 | The cloud files: settings with other keys, written twice; the setup script outside a cloud session | Other keys kept, the hook added once; checksums pinned; the script does nothing (FR-CLOUD-02, FR-CLOUD-05) |
| T-PATH-01 | Plan a scan path from the GoodVibes amination TS pushed down its imaginary mode and shuffled, to the TS (D114) | The match finds every atom; the held distances are ticked and the first is the distance the mode changes most, with its values at both ends; solvent THF from the TS's SMD level; charge and multiplicity from the TS |
| T-PATH-02 | Plan a scan path to a TS made by hand; without an edge; with other charges, no charge, other atom counts; send without ticking | The guess's partial bond is suggested unticked; each refusal says why; sending without a held coordinate is refused |
| T-PATH-03 | Plan a scan path to a group joined by an edge; choose another member; select a member alone | The group stands for its representative, then the member chosen; a member reaches the edge through its group |
| T-PATH-04 | Send a scan path with a pair fixed by hand, a held distance and "Dichloromethane"; with an unknown solvent; holding at an end that is not a TS | The job folder has the start, the end renumbered, mapping.json with the fixed pair, path.json with the held distance and ch2cl2, job.md with the xtb flags and held values, job.json kind scan_path; refusals say why |
| T-PATH-05 | Send a scan path in an investigation not linked to GitHub; from another site's page | The job is kept as a draft with the reason; refused (FR-CLOUD-06) |
| T-PATH-06 | `pathtools.py` against the app; join two stages (the second reversed) and import; check a path with a stray bond | Same RMSD and bonds as the app, written word for word with the cloud files; the joined path imports with its stages and ALPB solvent; reached end, no stray bonds, the top found; the stray H–H bond reported |
| T-UI-01 | Resume overview on the example | Branch statuses, open items, recent changes and step notes all shown; clicking navigates (FR-OV-01) |
| T-UI-02 | View modes and filters | Switching modes and filters never changes stored data |
| T-UI-03 | 3D | Rotate, measure, animate the imaginary mode, overlay two nodes, overlay three on typed atoms and save the set, copy xyz |
| T-UI-04 | Canvas and profile image export | Files are produced and open in a standard viewer |
| T-UI-05 | Sterics | Create a profile, type the atoms, compute (36.1 %V_bur for the NHC complex), see it go out of date after a setting change, share the atoms with a turned copy, switch the map colours to green–yellow–red, compare both with maps, a difference map and a CSV (FR-STER-01…06) |
| T-UI-06 | Selectivity | Select the demo's two TSs, "Selectivity…": 98.6 : 1.4, ee 97.1 %, ΔΔG‡ 2.50 kcal/mol; rename outcomes; 233.15 K gives 99.5 : 0.5; only the lowest TS; experiment 90 : 10 gives 1.30 kcal/mol; free species balanced from T-S0 (none needed, the same ratio); history; delete (FR-SEL-01…07) |
| T-UI-07 | Turnover | Close the demo's branch B back to T-S0, "Turnover" in the drawer; with G: δE 23.30 kcal/mol, TDTS B-S2, TDI B-S3 (the TS before the TDI), ΔG_r 0 so TOF 0 with a note; TDTS and TDI marked on the profile; the degree-of-control table; a second turnover from the branch, compared; a branch that does not close is refused; history; delete (FR-TOF-01…05) |
| T-UI-08 | Pinned notes | Add a note to A-S2 with bold text, SVG pasted as text and a PNG chosen; an empty paste shows the ChemDraw advice; HTML with a script keeps only its italics; the note sits at the top right corner with both pictures and nothing ran; collapse survives a reload; double-click, move it to the bottom left; history; the read-only copy shows it with its pictures; delete (FR-NOTE-01…05) |
| T-UI-09 | Floating notes | On T-S0, detach a note with its pin: a line joins it to the corner; drag its head up and right and resize it from its outer corner, and both are saved; in the dialog choose "Floating, no line" (the line goes) and then "On the corner" with the height fitting the text; the history shows only the note's creation (FR-NOTE-06) |
| T-UI-10 | Remembered view | On the demo choose G_qh, T-S0 as reference, hide edge energies, filter out a status, expand a group, open the drawer on the table with branch A's pathway; reload the page: all of it is back; close and open the investigation: the same; delete the reference node: the investigation opens with no reference. Saving adds nothing to the history (FR-CAN-08) |
| T-UI-11 | Profile style and zoom | Open the demo's profile; "Style…": choose "Publication", the preview loses its title and grid lines and its connectors become curved; set the width to 1200 and Save: the drawer's profile and its saved SVG are 1200 wide in Arial with no title, and the PNG is 4800 wide; reload: the style is kept; "Screen" restores the old look. Drag the drawer's top edge up: the drawer grows; hide the pathways list; zoom to 200 %: the profile is twice the figure's size and scrolls; Fit returns (FR-EN-10, FR-EN-11) |
| T-UI-12 | "no TS" tags and title beside the legend | With the profile 320 wide and pathways A and B, the title's two lines and the legend do not overlap; untick "“no TS” tags" and Save: the direct connections stay dotted but no "no TS" tag is drawn, and the setting is saved (D108) |
| T-UI-13 | Node names along the bottom | On the demo's profile of pathways A and B choose Node name "Along the bottom": B-S2 ‡ is written once, lower than it was below its level and above its step name; A-S1 and B-S1, in one column, are written on two lines (D109) |
| T-UI-14 | Close levels keep their label positions | On the demo's profile of pathways A and B (screen style: values above, names below), A-S1 (−3.20) and B-S1 (−2.10) share a column: −2.10 and −3.20 are centred above the levels, B-S1 and A-S1 below them, each list in the order of the levels; with names along the bottom the first name row is within 50 units of the figure's bottom edge (D110) |
| T-UI-15 | A node shared by overlaid pathways is neutral | On the demo's profile of pathways A and B, both starting at T-S0, T-S0 is named once, its value is drawn once in the text colour, and its level is in the text colour in both pathways (D111) |
| T-UI-16 | Scan path movie and "Use this structure" | Import an `xtbscan.log` as a new node, choose its relaxed scan in Steps: it plays; pause, step to structure 9, "Use this structure": the node's coordinates are structure 9's and no derived node appears; on a node with an optimization the same button makes a derived node (D112) |
| T-UI-17 | Match atoms | Select two nodes of glycerol, the second numbered backwards and turned, "Match atoms…": no bond forms or breaks, RMSD 0.00 Å, two 3D views; "Download renumbered end" saves the start's elements in order at the start's coordinates; "Swap ends" matches the other way round (D113) |
| T-UI-18 | Scan path to a TS guess | Select a TS guess and its start joined by an edge, "Scan path…": "From “start” to “guess”", 1 bond breaks, the guess must be ticked (Send disabled until a coordinate is held); an angle typed by hand is held; choose toluene, Send: the job is kept as a draft because the investigation is not linked, and it is a scan path job with toluene and the angle (D114) |

## 8. Performance observation (no targets)

Per NFR-PERF-01, record (not pass/fail): load time, canvas pan and zoom smoothness, and import time for (a) the reference example and (b) a synthetic investigation of 500 nodes and 1,000 edges (P30: size to be confirmed). Report the results to Jonas to set targets.

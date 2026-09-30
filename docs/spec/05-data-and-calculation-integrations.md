# 05 · Data and calculation integrations

_Chembook3D specification pack · v1.1 · 2026-09-29. This document covers records, files, import and parsing, calculation history, results, units and provenance. Requirements are in [04](04-functional-requirements.md) (FR-IMP, FR-FILE, FR-CALC, FR-EN)._

## 1. Records (logical)

| Record | Key fields |
|---|---|
| Node | id, label, role, charge, multiplicity, status, tags, notes, branch_id, step_id, group_id, derived_from_id, canvas position, geometry (elements, coordinates Å) |
| Calculation | id, node_id, type, program, program version, level_of_theory_id, geometry_level_id (for single points), job step index, termination (normal / abnormal / unknown), notes, parse warnings |
| Results | per calculation: E_scf (hartree), ZPE, H_corr, G_corr, H, G, T (K), P (atm), frequencies (cm⁻¹, with normal-mode displacements), imaginary count, final geometry, rotational constants, symmetry number (if printed), multiplicity, masses, and the job's printed values kept verbatim for reference |
| LevelOfTheory | id, program, method, basis (or custom basis name), dispersion, solvation model, solvent |
| CustomBasis | name, description, optional raw definition text |
| SourceFile | id, calculation_id, stored relative path, original file name, origin device, origin path, import time, checksum (SHA-256), size |
| Step, Branch, GroupNode, Transition | as in [02 §2](02-domain-model.md) |
| HistoryEntry | id, timestamp, record type, record id, action, field, old value, new value, source (manual / import) |

## 2. File handling

- **Copy, never link** (D16). Copies go to `files/<calculation-id>/<original-name>`. They are read-only by convention and never rewritten.
- **Origin metadata** is required in the preview but may be left blank. The device name defaults to the last used (FR-SET-01).
- **Checksum:** the same checksum imported twice triggers a notice ("this file was already imported as …"). The user may still proceed.
- **Size:** there is no limit on file size. Performance targets are not set (see [06](06-architecture-and-operations.md)).
- Getting files from the cluster to the local machine happens outside the app (A2).

## 3. Parsers

| Program | Phase | Files | Scope |
|---|---|---|---|
| Gaussian | 2 | output (.log/.out) | FR-IMP-01 to 03 |
| ORCA | 5 | main output (.out) | FR-IMP-06, same kinds of results where printed |
| xTB | 5 | main output | FR-IMP-07, same kinds of results where printed |
| CREST | 5 | conformer ensemble (multi-structure xyz with energies in comment lines) | FR-IMP-10 |

The exact input file variants beyond these (e.g. .fchk, .gjf, ORCA .hess, CREST rotamer files) are **not confirmed** (Q11). Parsers should be written so that more file kinds can be added.

**Parser library (P16):** use cclib for Gaussian and ORCA as a base where it covers the fields, plus small custom parsers for multi-step handling, route and keyword extraction, solvation and dispersion, and CREST and xTB. cclib is BSD-3 licensed. Tradeoff: faster and well tested, but it does not expose everything (e.g. solvation keywords, per-step metadata), so custom code is still required. Decide in phase 2. **Decided:** Gaussian (D61), ORCA and xTB (D64) use Chembook3D's own parsers; cclib is not used.

### 3.1 Gaussian specifics
- **Level of theory** comes from the route section: the method and basis token (e.g. `B3LYP/def2TZVP`), `EmpiricalDispersion=`, `SCRF=(model,solvent=…)`. `gen`/`genecp` bases are mapped to a custom basis name in the preview (FR-CALC-03).
- **Multi-step:** each `--Link1--` step and each compound job step becomes its own calculation on the same node when the geometry is unchanged, e.g. opt then freq at the same level. If a later step changes the geometry (another opt), the geometry of the last step at the highest level is used (D25), and earlier geometries are listed in the preview only.
- **Frequency job added later (D52, FR-IMP-13):** each step in the file is compared with the node's geometry; matching steps attach, the first geometry-changing step starts a derived-node offer.
- **"Highest level of theory"** is not something the app can rank reliably. Default: the **last** step. The preview shows the choice and lets the user override it (FR-IMP-02).
- **Termination:** "Normal termination" per step. A missing one means abnormal.

## 4. Thermochemistry

- Printed values (ZPE, H_corr, G_corr, T, P) are stored as parsed.
- **Composite G** (EN-4): `G = E_SP + (G − E)_freq`, where the frequency calculation is on the same node at the node's geometry level.
- **G_qh (Truhlar)** (EN-5, D49, D56). The reference implementation is Jonas's script `thermochem_corr_G16` (derived from GoodVibes). The app must reproduce it:
  - **Inputs** from the chosen frequency step only: real frequencies (imaginary ones excluded and reported), SCF energy of that step, molecular mass, multiplicity, rotational temperatures, rotational symmetry number.
  - **ZPE** = Σ ½hcν over the **unmodified** real frequencies.
  - **U_vib(T)** from the **unmodified** frequencies (harmonic, including ZPE); U_rot = 3/2 RT (non-linear); U_trans = 3/2 RT; PV = RT.
  - **S_vib**: RRHO entropy per mode, with every frequency below the cutoff **replaced by the cutoff** (Truhlar). S_trans: ideal gas at 101 325 Pa. S_rot from rotational temperatures and the symmetry number. S_elec = R ln(multiplicity).
  - H = E_SCF + U_rot + U_vib + U_trans + PV; G = H − T·S; **G_corr,qh = G − E_SCF**.
  - Constants and conversions as in the script (CODATA 2018 SI values, 1 hartree = 627.5094740631 kcal/mol), consistent with P17.
  - **Linear molecules:** the script does not support them; v1 shows G_qh as "n/a" with a warning for linear molecules (D58).
- **Parameters** (D58): T (default 298.15 K) and cutoff (default 100 cm⁻¹) are settings, and each stored G_qh value records the T and cutoff it was computed with. The energy-type drop-down labels it, e.g. "G_qh (298.15 K, 100 cm⁻¹)".
- **Multi-step files:** only the frequencies of the selected frequency step are used. (The reference script collects frequency lines from every step in a file, which double-counts modes if a file has two frequency jobs. The app deliberately differs here.)
- **Verification:** G_corr,qh must match the reference script's "Thermic correction" on the same single-frequency-job file within the test tolerance (P29). With no frequencies below the cutoff and T equal to the job's T, it must also match the job-printed G correction within tolerance.
- **Standard state** (D57): no concentration correction; the gas-phase 1 atm reference is used, as in the script.

## 5. Units

| Quantity | Stored | Displayed |
|---|---|---|
| Energy | hartree | kcal/mol default; kJ/mol, eV, hartree selectable (D28, P14) |
| Coordinates | Å | Å |
| Frequencies | cm⁻¹ | cm⁻¹ |
| T, P | K, atm | K, atm |

Conversion constants are defined once, in one module, from CODATA 2018 (P17).

## 6. Provenance and consistency

| Concern | Rule |
|---|---|
| Where a value came from | Every result links to its calculation, which links to its copied source file and origin metadata. |
| Parsed and edited values | Parsed values are kept. User edits to level fields are stored as overrides, and both are viewable (FR-CALC-05). |
| Map versus data | The canvas and tables only render derived values. Nothing numeric is typed on the canvas (EN-1, FR-EN-08). |
| Geometry versus results | Results can never end up on a geometry they were not computed on (ID-5, ID-7, INV-6). |
| History | All changes are appended to the history (FR-HIST-01). |
| What validation means | Warnings report file contents (termination, imaginary counts). They do not establish that a structure or pathway is chemically correct (X6). |

## 7. Import and export formats

| Direction | Format | Status |
|---|---|---|
| Import | Gaussian, ORCA and xTB outputs, CREST ensemble | Confirmed (D8) |
| Import | xyz pasted as text | Confirmed (D20) |
| Import | SDF | Not discussed; out of scope for v1 unless requested |
| Export | xyz per node | Confirmed (D39) |
| Export | Energy table CSV | Confirmed (D44) |
| Export | Canvas and profile images | Confirmed; PNG and SVG (P18) |
| Export | Whole investigation | The folder itself; optional "Export as zip" (P8) |

## 8. Open items affecting this document
- Q11: file variants per program beyond the main output.
- Q32: duplicate tolerance default.
- Q34: whether calculations and energies can be entered manually without a file (not discussed; v1 assumes no, per EN-1).

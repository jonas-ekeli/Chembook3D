# 05 · Data and calculation integrations

_Chembook3D specification pack · v1.1 · 2026-09-29. This document covers records, files, import and parsing, calculation history, results, units and provenance. Requirements are in [04](04-functional-requirements.md) (FR-IMP, FR-FILE, FR-CALC, FR-EN)._

## 1. Records (logical)

| Record | Key fields |
|---|---|
| Node | id, label, role, charge, multiplicity, status, tags, notes, branch_id, step_id, group_id, derived_from_id, canvas position, geometry (elements, coordinates Å) |
| Calculation | id, node_id, type, program, program version, level_of_theory_id, geometry_level_id (for single points), job step index, termination (normal / abnormal / unknown), notes, parse warnings |
| Results | per calculation: E_scf (hartree), ZPE, H_corr, G_corr, H, G, T (K), P (atm), frequencies (cm⁻¹, with normal-mode displacements, kept compressed in a table of their own, D247), imaginary count, final geometry, rotational constants, symmetry number (if printed), multiplicity, masses, and the job's printed values kept verbatim for reference |
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
| xTB | 5 | main output; a relaxed scan's `xtbscan.log` and a scan path's `path.xyz` (D112) | FR-IMP-07, same kinds of results where printed |
| CREST | 5 | conformer ensemble (multi-structure xyz with energies in comment lines) | FR-IMP-10 |

The exact input file variants beyond these (e.g. .fchk, .gjf, ORCA .hess, CREST rotamer files) are **not confirmed** (Q11). Parsers should be written so that more file kinds can be added.

**Parser library (P16):** use cclib for Gaussian and ORCA as a base where it covers the fields, plus small custom parsers for multi-step handling, route and keyword extraction, solvation and dispersion, and CREST and xTB. cclib is BSD-3 licensed. Tradeoff: faster and well tested, but it does not expose everything (e.g. solvation keywords, per-step metadata), so custom code is still required. Decide in phase 2. **Decided:** Gaussian (D61), ORCA and xTB (D64) use Chembook3D's own parsers; cclib is not used.

### 3.1 Gaussian specifics
- **Level of theory** comes from the route section: the method and basis token (e.g. `B3LYP/def2TZVP`), `EmpiricalDispersion=`, `SCRF=(model,solvent=…)`. `gen`/`genecp` bases are mapped to a custom basis name in the preview (FR-CALC-03).
- **Multi-step:** each `--Link1--` step and each compound job step becomes its own calculation on the same node when the geometry is unchanged, e.g. opt then freq at the same level. If a later step changes the geometry (another opt), the geometry of the last step at the highest level is used (D25), and earlier geometries are listed in the preview only.
- **Frequency job added later (D52, FR-IMP-13):** each step in the file is compared with the node's geometry; matching steps attach, the first geometry-changing step starts a derived-node offer.
- **"Highest level of theory"** is not something the app can rank reliably. Default: the **last** step. The preview shows the choice and lets the user override it (FR-IMP-02).
- **Termination:** "Normal termination" per step. A missing one means abnormal.
- **Correlated methods (D153):** a step's energy is the method's own printed total, never the SCF energy of its reference: `E(<name>)` after `E2(<name>)` for a double hybrid (B2PLYP, DSD-PBEP86, mPW2PLYP, PBE0-DH…), `EUMP2`, `EUMP3`, `UMP4(DQ|SDQ|SDTQ)`, `CCSD(T)=` and `QCISD(T)=`; in an optimization, each structure's. A correlated method whose total is not read (CCSD, QCISD, CISD, BD…, or a job cut off before it) gets no energy and W-PARSE "correlated energy (method)". ORCA's `FINAL SINGLE POINT ENERGY` already includes the correlation energy.
- **Counterpoise and ONIOM (D161):** a `Counterpoise=N` step's energy is the printed counterpoise-corrected total (`Counterpoise corrected energy =`, earlier `Counterpoise: corrected energy =`), each structure's in an optimization, with the BSSE (`BSSE energy =`) kept in the result's `printed`; the fragments' structures and energies (after `Counterpoise: doing …`) are skipped. An ONIOM step's method is its route word (`ONIOM(B3LYP/6-31G(D):UFF)`, no separate basis set) and its energy `ONIOM: extrapolated energy =`. A step whose last structure has no such total gets no energy and W-PARSE naming it.
- **Spin contamination (D162):** ⟨S²⟩ after each unrestricted SCF, before and after annihilation (`S**2 before annihilation x, after y`, else the `<S**2>=` line), the last of the step; ORCA's `Expectation value of <S**2>` and `Ideal value S*(S+1)` likewise. Kept as printed in the result's `printed`; W-SPIN beyond 10 % of S(S+1) (a singlet: above 0.1).
- **Thermochemistry model and frequency scale (D163):** a frequency step keeps `g_model` (Gaussian: RRHO) and `frequency_scale` in the result's `printed`: the factor a line "… frequencies scaled by x" names, else the route's `Scale=x` or `Freq=(Scale=x)` (an `opt freq`'s frequency step takes the first step's), else 1. Gaussian's printed frequencies stay unscaled; only its printed thermochemistry uses the factor. ORCA: `Scaling factor for frequencies = x (already applied!)` (the printed frequencies carry it), else `%freq ScalFreq x` in the input echo; `Quasi RRHO ... True` or "Vibrational entropy computed according to the QRRHO of S. Grimme" is qRRHO, `Quasi RRHO ... False` is RRHO. xTB: mRRHO, with the `scaling factor` of its thermo setup.

## 4. Thermochemistry

- Printed values (ZPE, H_corr, G_corr, T, P) are stored as parsed.
- **Composite G** (EN-4): `G = E_SP + (G − E)_freq`, where the frequency calculation is on the same node at the node's geometry level.
- **G_qh (Truhlar)** (EN-5, D49, D56). The reference implementation is Jonas's script `thermochem_corr_G16` (derived from GoodVibes). The app must reproduce it:
  - **Inputs** from the chosen frequency step only: real frequencies (imaginary ones excluded and reported), SCF energy of that step, molecular mass, multiplicity, rotational temperatures, rotational symmetry number.
  - **ZPE** = Σ ½hcν over the **unmodified** real frequencies.
  - **U_vib(T)** from the **unmodified** frequencies (harmonic, including ZPE); U_rot = 3/2 RT (non-linear; RT for a linear molecule, D164); U_trans = 3/2 RT; PV = RT.
  - **S_vib**: RRHO entropy per mode, with every frequency below the cutoff **replaced by the cutoff** (Truhlar). S_trans: ideal gas at 101 325 Pa. S_rot from rotational temperatures and the symmetry number. S_elec = R ln(multiplicity).
  - H = E_SCF + U_rot + U_vib + U_trans + PV; G = H − T·S; **G_corr,qh = G − E_SCF**.
  - Constants and conversions as in the script (CODATA 2018 SI values, 1 hartree = 627.5094740631 kcal/mol), consistent with P17.
  - **Linear molecules:** the script does not support them (D58); the app adds the linear rigid rotor (D164): one rotational temperature, U_rot = RT, S_rot = R(ln(T/(σΘ_rot)) + 1), the 3N − 5 vibrations the file lists, everything else as above. With cutoff 0 it gives Gaussian's printed G − E within 0.001 kcal/mol. Non-linear molecules are unchanged.
- **Parameters** (D58): T (default 298.15 K) and cutoff (default 100 cm⁻¹) are settings, and each stored G_qh value records the T and cutoff it was computed with. The energy-type drop-down labels it, e.g. "G_qh (298.15 K, 100 cm⁻¹)".
- **Multi-step files:** only the frequencies of the selected frequency step are used. (The reference script collects frequency lines from every step in a file, which double-counts modes if a file has two frequency jobs. The app deliberately differs here.)
- **Verification:** G_corr,qh must match the reference script's "Thermic correction" on the same single-frequency-job file within the test tolerance (P29). With no frequencies below the cutoff and T equal to the job's T, it must also match the job-printed G correction within tolerance.
- **Standard state** (D57, D95): by default no concentration correction; the gas-phase 1 atm reference is used, as in the script. As an option in Settings, G and G_qh are at 1 M: each molecule adds RT ln(V_m / 1 L mol⁻¹) with V_m = RT / 1 atm at the G_qh temperature (1.894 kcal/mol at 298.15 K). The reference script's values are those at 1 atm.

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

- Q100: failure markers in the wrapper log and supported edits to generated inputs (§9).
- Q11: file variants per program beyond the main output.
- Q32: duplicate tolerance default.
- Q34: whether calculations and energies can be entered manually without a file (not discussed; v1 assumes no, per EN-1).

## 9. Gaussian HPC wrapper workflow (D266)

Jonas's two-step example, 2026-11-25. Both invocations use the submission workspace as their working directory. The geometry and generated input share a filename stem.

| Operation | Example invocation | Result |
|---|---|---|
| Prepare | `submit_all_DFT.sh minimum.jd example.xyz nosub` | Creates `example.inp` for inspection or revision. No job is submitted. A bare job filename refers to a file in the workspace. The wrapper also accepts an absolute job-file path, so the helper can select the file from a trusted project directory. |
| Submit | `submit_job_g16-C.01 example.inp -n 1 -c 40 -t 24` | Submits the prepared input and creates `example.log` in the submission workspace. This workflow always requests one node. The example requests 40 CPU cores and 24 hours of wall time. |

The workspace `.log` is the submission wrapper's job record. It reports the separate run directory and is updated as the job completes, times out or fails. The developing Gaussian `.out`, checkpoint and other calculation files are in the run directory. On normal completion the wrapper copies files back to the submission workspace. When Gaussian exits with a fatal error, the wrapper also copies back the `.out`, which contains the Gaussian diagnostic. After a crash or wall-time exit the wrapper updates the workspace `.log` and the calculation files survive in the run directory. Copy-back may or may not have occurred. Inspect the output for this attempt in the workspace if present, or in its verified run directory otherwise.

For agent follow-up, consult the wrapper log for job state and the Gaussian output for scientific progress or termination. The cluster helper must verify a reported run directory against the approved read scope and its association with this job before retrieving files (D266). Report unavailable output explicitly if that check fails or the files are gone.

### Behaviour of the supplied preparation and submission scripts

`submit_all_DFT.sh` explicitly handles XYZ and SDF and invokes the configured Java generator's `PrepareInputGaussian` task, writing its output messages to `<stem>.accLog`. With one selected geometry, the `nosub` branch exits successfully without checking the generator's status or validating the new input. For agent preparation, supply one geometry and one trusted project job file in a fresh attempt directory. Verify that this invocation produced a usable input before offering it for review or submission.

`make_gaussian-linda_jobscript_for_SLURM.sh` uses the UiBKvant argument parser, calls `make_jobscript_for_SLURM` to generate the batch script, then calls `sbatch` outside debug mode. Its final `exit 0` does not preserve a failed scheduler exit status. Use the receipt to establish acceptance. A definite scheduler rejection and a lost reply require different follow-up. Jonas confirmed that `submit_job_g16-C.01` calls this script through a lightweight wrapper. The shared functions select files and generate the batch script.

In the supplied `functions.sh`, `parseCLIArgs` starts the input list with the positional file and adds explicitly listed files. Optional inclusion regexes can select further entries at the top level of the submission directory. The shared functions do not add all files with the input's basename by default. For agent submissions, resolve the approved input and checkpoint in the cluster helper and keep file-selection rules under site control.

The generated job constructs the run directory from the configured scratch root, login name, Slurm job ID and job name. For a fresh directory it copies the selected files, decompresses staged gzip files and runs the application preflight. An existing directory for the same ID and job name sets the wrapper's restart flag. It reuses that directory and skips staging and decompression. A new job ID starts a separate attempt even when its Gaussian input reads a previous checkpoint.

On reaching postprocessing, the job applies its output rules and normally removes the run directory after copying. A copy message is printed before the copy succeeds. In the default Gaussian regex path, the copy loop runs in a pipeline. With default Bash behaviour, the loop's `cleanup=false` on error does not reach the parent performing cleanup. Follow-up should verify each needed artifact at the destination and check the verified run directory if a copy is missing. Report an unavailable artifact if neither location holds it.

The preparation script discovers a tool installation and sources its site configuration. The submission script loads shared shell functions and embeds checkpoint names from the Gaussian input in its generated job. The restricted cluster helper must bind these dependencies to trusted installations, validate filenames and checkpoint references, and supply only the configured resource arguments. Agent write access must exclude control files and executable dependencies.

### Evidence from Saga startup and completion logs using Gaussian 16 C.01

On every job the wrapper searches the submission workspace for a checkpoint matching by name. On fresh staging, if it finds a match, it copies the checkpoint into the calculation workspace even when the submitted input never reads it. Determine whether a calculation depends on that checkpoint from the submitted input revision. A fresh calculation can create its own checkpoint. A restart or follow-on calculation that reads an earlier checkpoint requires the file under the expected name and in the required form before Gaussian starts. The Linda script also reads case-insensitive %CHK lines and rejects conflicting names in one input. For each distinct value it tries appending `.chk.gz`, `.chk`, `.fchk.gz`, then `.fchk`, taking the first existing candidate in that lookup. Explicit file lists or configured transfer rules may also select a checkpoint. Its preflight uses `unfchk` on formatted checkpoints after decompression when the wrapper's restart flag is off. The flag describes a wrapper path and should not be inferred solely from whether a chemistry input reads a checkpoint.

The submission wrapper prints job-name and checkpoint-search messages, then the Slurm receipt `Submitted batch job <id>`. Record that numeric ID with the cluster and attempt as soon as the receipt arrives. It confirms acceptance. The current queue state requires a separate status check. When the workspace log becomes available, use only records matching that job ID, since successive attempts can reuse an input name. If the connection drops before the receipt is captured, keep the submission outcome uncertain and reconcile before any retry.

| Evidence in the workspace log | Interpretation for follow-up |
|---|---|
| `Starting job <id>`, `JobName:` and `Work space:` | The start record identifies the running job and its calculation directory. Slurm's later `WorkDir=` identifies the submission directory. This example starts after the job leaves the queue. |
| `Running Gaussian` and a launch line naming the input and output, with no later entries yet | Report that the matching job started and the wrapper reached Gaussian launch. Look for the named `.out` in the verified run directory for progress. A missing or empty output at launch leaves progress unknown. The launch line is descriptive log text. |
| Gaussian module load and an input update before launch | The wrapper reports setting processor count and memory in the run copy of the input. Preserve the approved submission revision when comparing it with the input copied back. |
| `Checking normal termination` followed by the output filename and `normally terminated` | The wrapper found normal Gaussian termination. Inspect the corresponding output for optimization convergence and any requested frequency or mode checks. |
| Checkpoint formatting and individual copy-back messages | In this run the wrapper reported copying the input, output, compressed formatted checkpoint and `.fort.7`. It reported removing the binary checkpoint after formatting. Use the artifacts actually available when assessing a possible restart. |
| Slurm snapshot `JobState=RUNNING`, followed by accounting text `Job exited normally.` and `Job <id> completed at ...` | The snapshot was taken before the final accounting summary. Match the attempt ID and use the later terminal evidence. Earlier module, striping and missing optional-file messages also occur in this successful example. |
| `JOB <id> ... CANCELLED AT ...` followed by `Job was cancelled.` | Record the matching attempt as cancelled. In the observed manual cancellation, copy-back was skipped. The top-level task row shows exit status 0, the batch row shows signal 15, and the final accounting line still says `completed at`. That timestamp records the end of accounting for the job. The explicit cancellation supplies the outcome. Signal 15 alone does not establish who stopped the job or why. |

After a user cancellation, stop automatic retries and await a new instruction before another submission. If the user wants to inspect partial progress, look in the verified run directory for this attempt and report which files are available. The log alone does not establish how far the chemistry progressed.

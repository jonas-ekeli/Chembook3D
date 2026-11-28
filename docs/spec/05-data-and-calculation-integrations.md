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

### From a node request to a reaction-wide task (scoping)

Jonas wants to give the agent a scientific task in an investigation, ranging from work on one node to completing a set of calculations for a reaction. Choosing a default geometry from the viewer is still open. The investigation already exposes nodes, stored geometries, their levels of theory and calculation steps. A request can explicitly identify a displayed frame when that is the intended starting point.

Jonas's representative request is “Find a verified transition state for node X using this project's method.” The requested result includes a converged DFT TS optimization with one confirmed imaginary mode and a completed project single point on that optimized geometry. The node should retain the calculations and starting-geometry provenance so another request can reuse a valid TS or run just a missing single point.

| Stage | Agent action and evidence |
|---|---|
| Find a guess | Inspect X, its charge and multiplicity, project method, existing calculations and the adjacent intermediates. Reuse an appropriate guess or use the notebook's existing local ML or semiempirical guess tools. For example, `start_local_ts_guess` can use compatible endpoint structures. Record the source and atom mapping for the chosen guess. |
| Improve the guess | For a rough guess, pre-optimize with the relevant distances, angles or torsions held. Inspect the modes at that geometry. A mode moving the atoms along the intended coordinate is evidence to try an unconstrained project DFT TS optimization. A constrained structure remains a guess. |
| Recover a difficult TS | If the pre-optimization lacks a useful mode, or the TS search fails, use the preceding results to choose a revised constraint target or a scan along the relevant coordinate(s). Select a promising scan geometry for the next attempt and record why. Retry and resource limits remain open. |
| Verify and complete X | Check convergence and normal termination, then inspect the frequency result on the same unconstrained geometry at the project's TS level. Proposed interpretation of “verified”: exactly one imaginary mode whose atomic displacements support the intended elementary step. Record the frequency and evidence for that interpretation. `get_vibrational_modes` and `compare_structures` already provide relevant evidence for imported calculations. An ambiguous mode needs further evidence before the agent claims success. Run the project single point on the verified geometry in the correct electronic state and associate the successful result with X. |

For a set of alternating intermediates and transition states, the agent should inventory missing work and run independent jobs in parallel within the user's allocation. A difficult TS can depend on usable geometries from one or both adjacent intermediates. As soon as the needed geometries are ready it can proceed, while unrelated nodes continue. A TS single point depends on verification of its own TS. The set-level report should distinguish completed, pending and blocked nodes and give the outstanding calculation or scientific question for each.

#### Evidence from the three-stage Gaussian example (Q100)

The example uses three separate submissions. In the five-step preparation, the fourth step enables `ModRedundant` and freezes one interatomic distance at its starting value. The fifth step computes frequencies. The subsequent project TS job reads its geometry, basis and wavefunction guess from a checkpoint, releases the distance, optimizes and computes frequencies. The project single point reads the optimized geometry and guess from a checkpoint and supplies its own larger basis. In both handoffs the atom sequence and printed Cartesian coordinates agree after alignment. The wrapper logs identify the formatted checkpoint staged under each receiving job's name. They do not identify which earlier artifact supplied those bytes.

The constrained and unconstrained structures each have one imaginary vibrational frequency. The relative displacement of the selected atoms lies mainly along their separation in both modes. The constrained result supports the choice of starting geometry. For this node, Jonas accepted the converged, unconstrained TS because its sole imaginary mode opens and closes the selected bond. This evidence is sufficient to proceed to the project single point. For other nodes, use the intended elementary step to identify the motion to check. Where several bond changes matter, inspect their relative directions together, allowing for reversal of the whole mode.

If the expected motion is unclear from the node and its neighbors, the agent can consult project PDFs, search the literature, or look up a primary source online. When a source informs the assignment, record the citation and which motion it suggests, alongside the displacements observed in this calculation. If those displacements still leave the assignment ambiguous, keep the TS unverified.

In the local pilot, pass `freeze_distance` to `prepare_gaussian_job` as two distinct 1-based atom indices from the selected XYZ. The helper verifies the generated five-step minimum, adds `ModRedundant` to the optimization and inserts a single distance freeze at its starting value. The review shows the atom pair, complete revised input and its SHA-256 digest.

After the parent job finishes, `inspect_preoptimization_modes` takes its attempt ID. The helper checks the parent receipt, log and copied-back Gaussian output. For each imaginary frequency it reports the mode index, frequency and how strongly the two selected atoms move relative to one another along the bond. Reversing all displacements gives the same chemical interpretation. A mode is eligible only when its alignment with the bond is at least 0.5 and its bond share, the bond projection as a fraction of the mode's largest atom displacement, is at least 0.25 (D269). The agent uses this evidence and the intended elementary step to choose a guess. A component along the bond alone does not establish a TS.

`prepare_ts_from_preoptimization` takes the parent attempt, the inspected output's SHA-256 digest, selected mode index, cores and hours. A change to that output requires a new inspection. The local helper uses its fixed project TS job description to generate the unconstrained optimization and linked frequency calculation. Both steps read the receiving checkpoint. The mode index records the scientific reason for selecting the guess. The app binds the child to the investigation and presents the parent attempt, job ID, mode and checkpoint digest with the generated input. On approval the helper checks the source checkpoint again and stages those bytes in the fresh child submission directory under the receiving job's basename. The helper compares the formatted checkpoint's atom order, charge, multiplicity and Cartesian geometry with the parent input and frequency result. The local handoff accepts a copied-back `.fchk.gz` up to 64 MiB compressed and 256 MiB expanded.

Use synthetic fixtures in CI for the three-stage sequence. In the successful case the unconstrained optimization converges and its sole imaginary mode opens and closes the specified bond, which permits the single point. Change only the displacement vectors so the same imaginary frequency belongs to unrelated motion and verify that the single point stays pending. Check that the freeze applies only to the preparation, a constrained result cannot authorize a TS single point, and each child uses its selected parent's checkpoint. Cover a missing or changed checkpoint and a single point that starts from the pre-optimized geometry instead of the verified TS. Scans, ambiguous-mode follow-up, intermediate completion criteria, and authorization for concurrent work and retries remain open.

### Running the first workflow locally

From a checkout with development dependencies installed, run:

```text
uv run pytest -q tests/test_hpc_jobs.py tests/test_remote.py::test_local_preparation_submission_and_follow_up_through_mcp
uv run pytest -q tests/test_remote.py::test_preoptimization_checkpoint_to_ts_through_mcp
```

This exercises the app through MCP, the keeper and a loopback SSH subsystem. It uses synthetic water coordinates and a project job file. The generator and scheduler are local stand-ins. To try the workflow interactively, start the stand-in from the repository root:

```text
uv run python -m tests.fake_ssh_server --port 8767 --root .venv/hpc-demo/cluster --job-pilot-config .venv/hpc-demo/config
```

In a second PowerShell window at the repository root, start the built app with isolated settings:

```powershell
$env:CHEMBOOK3D_CONFIG_DIR = (Resolve-Path .venv/hpc-demo/config).Path
uv run chembook3d --port 8875
```

Open a local test investigation. In that app's server settings edit its Saga entry to host `127.0.0.1`, port `8767` and user `jonas`. Connect with the fixture password `correct horse` and code `123456`. Ask the agent to prepare `water.xyz` using `minimum.jd` on one node with 40 cores and 24 hours. The input, resources and attempt ID are available before submission. Request submission, expand **Review Gaussian input** in the app, then choose **Submit job** or **Refuse**.

To try a constrained pre-optimization in the same fixture, ask the agent to prepare `water.xyz` with `linked-minimum.jd` and freeze the distance between atoms 1 and 2. In the preview, step 4 should contain `Opt=(Tight,ModRedundant)` and `B 1 2 F`. Step 5 should still request frequencies. For the freeze, the helper supports bare `Opt` and the reviewed project options `Tight`, `RecalcFC=n` and `MaxCycles=n`. If it encounters another option, it identifies it before review.

For an interactive handoff test, approve that frozen water preparation on the loopback server. Its stand-in writes synthetic output and a compressed checkpoint. Ask the agent, “Inspect the imaginary modes of the pre-optimization I just submitted. If one opens and closes the distance between atoms 1 and 2, prepare the unconstrained project TS using that parent checkpoint, one node, two cores and one hour. Show me the parent job ID, selected mode and checkpoint digest before asking me to submit.” The fixture supplies one imaginary mode at −425 cm⁻¹. After review, the child stand-in supplies its own receipt and start log. The fixture demonstrates job handoff and makes no prediction about the potential energy surface of water.

The fixed read directory is `/cluster/work/gaussian-pilot`. The existing agent file-write toggle permits new files under its `geometries` subdirectory. The helper chooses a fresh attempt folder, verifies atom order and coordinates, and stages the approved revision in its own submission directory. The project job files and authorization records live outside the fake server's SFTP tree. Moving the user's terminal does not change either agent boundary.

Use `get_prepared_job` with the attempt ID after a lost reply. A recorded numeric receipt supplies `check_server_job` with its directory and job ID. A missing receipt after the wrapper has run leaves the attempt uncertain and blocks replay. The live Saga trial verified the enrolled minimum. The site helper also supports the archived TS acceptance procedure below. Agent-driven preparation of a new constrained parent on Saga, verification of the final TS, the single-point handoff, SDF, scans and IRC are later milestones.

### First Saga TS acceptance test using an archived pre-optimization

An operator can enroll one reviewed TS input and its starting checkpoint for the current Saga pilot enrollment. This lets the operator test the real wrapper and checkpoint staging without charging the project for another pre-optimization. The workstation checks the parent log, Gaussian calculations, selected imaginary mode, formatted checkpoint and project TS input. For operator-selected archives the downloaded files' local modification times describe the transfer. The operator supplies the original parent Slurm ID, which must match the log. The helper pins the reviewed manifest, input, checkpoint, existing pilot and wrapper. Only the operator can change the enrollment. The route, geometry and mode checks of the TS input run on the workstation when the bundle is made. On Saga the helper re-checks the digests, the two linked steps, the `%nproc`/`%mem` allowlist and the receiving checkpoint name, but not the route or other input content. The operator's enrollment is therefore the trust boundary for the TS input's content.

1. On the workstation with this checkout and the archive, run `uv run chembook3d saga-ts-bundle PARENT.inp CHILD.inp enrollment --parent-job-id ID --mode-index N` with the actual paths, original job ID and one-based Gaussian mode index. Keep the parent's matching `.log`, `.out` and `.fchk.gz` beside its input. Choose a new output directory. Inspect the command's report. Keep the bundle private with the project's calculation files.
2. Transfer the resulting `enrollment` directory and the matching `src/chembook3d/remote/saga_prepare.py` into `~/chembook-saga-ts/` on Saga using your usual authenticated transfer. In your Saga terminal run the following commands with the existing enrolled pilot:

   ```bash
   install -m 600 -- ~/chembook-saga-ts/saga_prepare.py ~/.local/lib/chembook3d/saga_prepare.py
   python3 ~/.local/lib/chembook3d/saga_prepare.py enroll-ts ~/chembook-saga-ts/enrollment
   ```

   Keep the TS attempt ID it prints. Re-enrolling the same bundle preserves the attempt. A different bundle for the same pilot enrollment is refused.
3. Start the matching workstation checkout. If the old keeper is still running, log out of the open Chembook HPC sessions, close the app, wait a minute, reopen it and log into Saga with 2FA. Open the test investigation. Ask the agent, “Call `prepare_saga_ts` on Saga and report the attempt ID, parent Slurm ID, imaginary frequency, atom pair, input and checkpoint digests, and destination. Stop after preparation.” Compare the result with the operator's report. This step uses zero scheduler jobs.
4. To exercise review, ask the agent to call `submit_prepared_job` once for that attempt. Choose **Refuse**. `get_prepared_job` should still return `prepared` with no job ID. For a live calculation, request review again and choose **Submit job** only if you intend to run this molecule with the displayed allocation. The pilot uses one node, 40 cores and 24 hours. After approval use `get_prepared_job` and pass its `follow_up` arguments to `check_server_job`. A lost reply is recovered through the same attempt.

On 2026-11-28 the operator confirmed step 3 on Saga. The agent returned the enrolled attempt ID. The parent Slurm ID, selected mode and frequency, frozen atom pair, and SHA-256 digests of the TS input and starting checkpoint matched the workstation record. The operator then chose **Refuse** in the submission review. Before and after review, `get_prepared_job` reported `prepared` with a null job ID.

The operator subsequently approved the same attempt. The agent reported one submission request. It retrieved the Slurm job ID from the submitted attempt and used its follow-up arguments to find the matching wrapper start line. At that check, the wrapper had recorded no termination and the Gaussian output in scratch was outside the approved results directory.

On 2026-11-28 the operator asked the agent in the logged-in session to follow the same attempt to completion and verify it without resubmitting. `get_prepared_job` returned the recorded Slurm ID, and `check_server_job` with its follow-up arguments reported a normal wrapper exit. It also attributed the copied-back Gaussian output in the attempt's submission directory to that job. The unconstrained TS optimization converged on all four criteria with no frozen coordinate. The optimization and frequency steps both terminated normally. The frequency step reported exactly one imaginary frequency, −119.5591 cm⁻¹. From that step's own input-orientation coordinates and mode 1 displacements, the signed projection of the 1–2 relative displacement on the 1→2 bond is −0.734 (±0.02 from the printed rounding), with alignment 0.97. In the printed phase the two atoms approach, so the mode opens and closes the intended bond. The mode passes the D269 floor. The agent reported the TS as verified and submitted nothing further. This completes the first live TS acceptance test. Which minima the TS connects was not checked, which would need an IRC or mode-follow. The single point and attachment of both calculations to the node are later milestones.

#### Small molecule for repeated live TS tests

Use neutral singlet HCN ⇌ HNC isomerization for the next short calculation. This three-atom system exercises hydrogen transfer and fits the pilot's single-distance constraint. Start with the following geometry in ångströms, derived from the three saddle-point distances reported by [Lee and Rendell](https://www.sciencedirect.com/science/article/abs/pii/000926149190073I).

```xyz
3
HCN-HNC TS guess, neutral singlet
C  0.000000  0.000000  0.000000
N  1.194000  0.000000  0.000000
H  0.380092  1.125555  0.000000
```

Generate the parent with the project minimum job description and `nosub`. Check the atom ordering, then freeze N–H (atoms 2 and 3 in the XYZ) in the fourth step and retain the fifth-step frequency calculation. The operator prepares this parent on Saga. After it finishes, inspect the imaginary mode and its motion along N–H before using the checkpoint for the unconstrained project TS. For the child, require convergence and one imaginary mode in which C–H shortens as N–H lengthens, or vice versa.

On Saga, the operator generated the three-atom input with the project minimum job description and `nosub`. The input preserved the C, N, H order, neutral singlet and expected optimization options. For the manually submitted parent, `check_server_job` attributed the copied output to its Slurm receipt. The wrapper and all five Gaussian steps terminated normally. The constrained optimization converged in step 4. Step 5 reported three frequencies, one imaginary at −1065.4677 cm⁻¹. In the printed phase of mode 1, the bond projections computed from the step 5 input orientation and displacements were +0.265 for C–H and −0.762 for N–H. This supports selecting the constrained parent for the H-transfer TS optimization. The submission directory contained the 39,415-byte formatted checkpoint archive.

The project TS job description generated five steps from the HCN XYZ. For this checkpoint restart, retain steps 4 and 5 in a separate input with the same basename. Keep the Link1 separator between those two steps. Both read the checkpoint geometry, guess and basis. The workstation bundle command accepted the extracted child and all four parent files. It validated the formatted checkpoint against the parent frequency geometry and identified mode 1 on N–H with a projection of −0.762. The HCN child is ready for enrollment.

A second live TS bundle needs a separate enrollment from the existing Ru attempt. The current helper refuses a second distinct bundle for the same minimum enrollment. An open scope question is whether the operator should be able to select the next validated bundle for preparation while earlier submitted attempts remain available by ID for read-only follow-up.

### Proposal: one node on Saga without hand enrollment (Q103–Q108)

_Proposed 2026-11-28, awaiting Jonas. Nothing here is built until he answers Q103–Q108._

The pilot replays only what the operator enrolled by hand: one manually verified minimum and one archived TS bundle. The next milestone is Jonas's representative request for one node: “Find a verified transition state for node X using this project's method.” The agent prepares and submits the calculations itself, but each submission still passes through the app's review. The local pilot already performs most of this sequence against a loopback stand-in (`prepare_gaussian_job` with `freeze_distance`, `inspect_preoptimization_modes`, `prepare_ts_from_preoptimization`). The proposal moves that sequence onto the Saga helper. The helper's independent checks stay in place, and the operator still owns everything that defines the method.

| Stage | What changes | Boundary that stays |
|---|---|---|
| Bind the request | The agent names node X in the open investigation. The request records X's charge and multiplicity (Q103), the geometry chosen as the starting guess and its source (an existing frame, `start_local_ts_guess` or another notebook tool, Q108), the atom mapping and the adjacent intermediates used. | The agent reads the notebook through its existing tools. It cannot change which method the project uses. |
| Write the guess | The agent writes the guess as a new flat XYZ file in the pilot's `geometries` directory with the existing write toggle. | New files only. Names are flat and the helper refuses anything else (FR-SSH-20). |
| Prepare a fresh parent | New helper operation: run the pinned generator with an operator-enrolled job description (Q106) on that XYZ with `nosub`, in a fresh attempt directory. The helper checks the generated input the way the local pilot does: atom order and coordinates match the XYZ, five steps, the project route and the receiving checkpoint name. It can then apply the single-distance freeze to step 4. The app reviews the exact input, destination and resources. | The generator, job descriptions and wrapper are pinned by digest. The agent can change only the freeze. The wrapper runs at most once per attempt and its Slurm receipt is recorded. |
| Choose a mode | The agent inspects the parent's copied-back output through scoped reads, as `inspect_preoptimization_modes` does locally. The D269 floor applies. | A mode below the floor cannot seed a TS. |
| Prepare the TS child | New helper operation: build the unconstrained TS input from the enrolled TS job description and the selected parent's checkpoint (Q104). It binds the parent attempt, the mode evidence and the checkpoint digest, as the archived replay does now. | Only the parent's own copied-back checkpoint enters the child's submission directory. |
| Follow up | `check_server_job` and `get_prepared_job` as now. The verdict uses the child's own convergence, normal termination and exactly one imaginary mode that matches the step. Automatic verification and the single point are step 2. | A missing receipt still blocks resubmission. |

The operator's limits (Q105) bound how many parents and children one request may start, and the resources of each. Results attach to X as ordinary imported calculations with their provenance (Q107). Scans, IRC, revised angle or torsion constraints, parallel nodes and the single point stay in later steps. Tests follow the existing pattern: the loopback stand-in and the Linux helper tests cover generation, freeze, refusal, a lost reply, a stale or foreign checkpoint, and limits that are reached.

### Testing access on Saga (Q101)

Sigma2 documents SSH connection reuse after interactive 2FA and user installation of software in a home or project area. OpenSSH registers a named subsystem in the server configuration. Installing a helper file in a user's home directory does not register it as a subsystem. The published guidance establishes neither an installed Chembook subsystem nor an isolated agent identity on Saga. [Sigma2 2FA](https://documentation.sigma2.no/getting_help/two_factor_authentication.html) · [User software](https://documentation.sigma2.no/software/userinstallsw.html) · [OpenSSH Subsystem](https://man.openbsd.org/sshd_config#Subsystem)

The alternative under investigation is a one-shot helper. The trusted desktop component would select one fixed executable and pass named requests on standard input. In the loopback test, a file-transfer channel stays open on the same authenticated connection while four requests reach the probe over SSH exec channels. The stand-in maps the fixed launcher to the local Python interpreter. Only the capabilities request succeeds. A separate test exercises the installed pilot and verifies that a missing subsystem produces an error without a shell fallback.

**First live check.** As the user, place the reviewed repository file `scripts/hpc_capability_probe.py` at `~/.local/lib/chembook3d/hpc_capability_probe.py` on Saga using your usual file transfer. Choose a fresh destination outside the agent's writable directories. The file needs read permission for your account and Python 3. From your own logged-in terminal run:

```sh
printf '%s\n' '{"operation":"capabilities"}' | python3 -I "$HOME/.local/lib/chembook3d/hpc_capability_probe.py"
printf '%s\n' '{"operation":"submit"}' | python3 -I "$HOME/.local/lib/chembook3d/hpc_capability_probe.py"
```

Expect `chembook3d-probe-v1` and a list containing only `capabilities` for the first request. The second should print `Unsupported request` and exit with status 1. The probe only reads stdin and writes the reply. These checks establish interpreter availability, file placement and request handling in an interactive login.

Jonas ran the capabilities request in an interactive SSH login on Saga and reported `{"ok":true,"protocol":"chembook3d-probe-v1","operations":["capabilities"]}`. A subsequent `submit` request returned `ok: false`. The process exit status was not reported.

**Second live check.** Test the same fixed launcher through an ordinary non-interactive SSH exec channel. For example, from an interactive PowerShell window, substitute your Saga login for `USERNAME` and use the actual uploaded path:

```powershell
'{"operation":"capabilities"}' | ssh -T USERNAME@saga.sigma2.no 'python3 -I .local/lib/chembook3d/hpc_capability_probe.py'
```

Jonas confirmed this separate SSH client prompted for 2FA and returned `ok: true` on Saga. This establishes that an ordinary SSH exec channel can launch the probe under his account.

**Third live check.** Put the reviewed probe at the fixed path `~/.local/lib/chembook3d/hpc_capability_probe.py` on Saga. If the uploaded copy is elsewhere, run the following from its directory in your Saga terminal:

```sh
mkdir -p ~/.local/lib/chembook3d
cp -n hpc_capability_probe.py ~/.local/lib/chembook3d/
```

On the workstation, update the testing branch with `git pull --ff-only`. Open Chembook from this checkout (`uv run chembook3d`) and log in to Saga in its HPC tab. With the tab connected, use a second local terminal in the same checkout and app configuration:

```text
uv run chembook3d saga-probe
```

The command asks the existing keeper to perform one capability check on its authenticated connection. It accepts an optional saved server ID (default `saga`). The fixed launcher checks the uploaded file against the reviewed digest and runs the verified bytes. The expected result is `chembook3d-probe-v1` without a further authentication prompt. This user diagnostic is available from the CLI. The separate preparation test below uses the notebook agent. Submission is available in the loopback pilot.

An older keeper reports an unknown operation. At a convenient time, log out all its saved HPC sessions, allow at least a minute for the empty keeper to exit, then log in from the updated checkout. Count any 2FA at that login separately from the probe. The diagnostic never opens, replaces or closes an SSH login.

**Saga result.** The first CLI attempt reached an older keeper and stopped locally. After following the reconnect procedure, Jonas reported `ok: true` from `chembook3d saga-probe`. The updated keeper ran the fixed helper over the existing authenticated SSH connection. The probe exercised no Slurm operations.

**Before live job submission**, verify the installed helper enforces the enrolled file scopes and that all agent paths to job inputs, results and imports use its named operations. The helper, scripts, job descriptions, dependencies and authorization records stay outside the agent's writable directory.

### Saga wrapper example

#### Agent file access through the Saga helper (D266, Q101)

Jonas confirmed that the desktop keeper is trusted. It reuses the operator's authenticated connection and controls the user-facing write permission. The notebook agent can request named operations. Saga independently checks each operation, its arguments and its file scope against the operator's enrollment. The operator's own terminal, import and XYZ export continue to use the interactive connection.

The keeper reads the enrollment marker, then sends file requests to the pinned helper. Directory listings use `list_files`. File inspection, summaries and import use `read_file`, which returns bounded portions of a file. New-file writes use `create_xyz` and require the write toggle in the desktop app. The helper limits creation to a fresh XYZ filename directly under `geometries/`. A missing, mismatched or refusing helper ends the agent operation.

**Updating an existing enrollment.** Update the workstation checkout and transfer its `src/chembook3d/remote/saga_prepare.py` to `~/saga_prepare.py` on Saga. Run `install -m 600 -- ~/saga_prepare.py ~/.local/lib/chembook3d/saga_prepare.py` in the Saga terminal to replace just the helper. This preserves the enrolled reference and the earlier preparation receipt. At a convenient time, log out of the older Chembook HPC sessions, close the app, wait at least a minute for the keeper to exit, then start the updated app and log in. For acceptance, repeat the directory and direct-read checks below and use a fresh XYZ name for the write check. The preparation and file-scope checks in the following subsection were performed with the earlier desktop file checks.

After the helper update, Jonas reported that the notebook agent listed the existing preparation directory and found all four expected files, including the 39,735-byte Gaussian input. From his Saga terminal, Jonas then asked the installed helper directly to list the sibling job-description directory. The helper returned `{"ok": false, "error": "This path is outside the enrolled pilot directory."}` and exited with status 1. This verifies that Saga enforces the directory boundary for listing independently of the desktop keeper.

With writes enabled on the same Saga login, Jonas reported that the updated helper created `geometries/helper-write-check.xyz` (36 bytes). The same text sent to `helper-write-check.xyz` at the pilot root was refused with HTTP 409 because creation is limited to the geometries directory. A request for `geometries/helper-write-check.txt` was refused with HTTP 409 because only flat, named XYZ files are allowed. Jonas then reported that `read_server_file` returned the expected third line, `He 0.0 0.0 0.0`, from that XYZ. Reading the master job description through the same agent tool returned HTTP 409 with the message `This path is outside the pilot's approved results directory.` That refusal came from the keeper's path check. The separate terminal test above exercised the cluster helper directly.

#### Reproducing a verified minimum through the agent

1. On the workstation, update the testing checkout with `git pull --ff-only`. Log out older Chembook HPC sessions at a convenient time, allow at least a minute for the keeper to exit and restart Chembook from the updated checkout with `uv run chembook3d`.
2. Transfer `src/chembook3d/remote/saga_prepare.py` from this checkout to `~/saga_prepare.py` using the operator's usual transfer method. In a **Saga terminal**, change to the successful `min-test-…` directory beneath the project pilot. Run `python3 ~/saga_prepare.py setup "$PWD/NAME.inp"`, replacing `NAME.inp` with the generated filename. Compare the printed SHA-256 with the digest of the manual run. Setup enrolls the sibling XYZ, the fixed minimum job description in the project's `jobdescriptions` directory, the generator in `~/tools/scripts` and the original input digest. It installs the helper and its configuration in `~/.local/lib/chembook3d`. The operator is responsible for the generator and its shell and Java dependencies and keeps them outside agent-writable locations.
3. In Chembook, open an investigation and log in to Saga in the HPC tab. Ask the notebook agent to use `prepare_saga_minimum` on Saga and report the input name, directory and SHA-256. The server ID is the only tool argument. The keeper supplies the fixed launcher and the helper supplies the generator arguments, including `nosub`. The acceptance criterion is an input in a new `prepare-…` directory whose full SHA-256 equals the digest printed during setup. Repeating the request should return the same directory and digest.

On 2026-11-26 Jonas reported that the first live agent request produced the expected input in a new `prepare-…` directory on Saga. Its full SHA-256 matched the enrolled manual input. The agent reported using `nosub` and submitting no job. A second tool call on the same enrollment returned the original preparation directory and the same SHA-256. The agent again reported no submission.

In a separate live check, `list_server_files` listed the four files in that preparation directory, including the 39,735-byte Gaussian input. Listing the sibling `jobdescriptions` directory returned HTTP 409 with the message “This path is outside the pilot's approved results directory.” Jonas then requested one line with `read_server_file` from each of two job descriptions. Reading the wrapper’s copy inside the preparation directory succeeded. Reading the master job description by its full path returned the same HTTP 409 refusal.

Once enrolled, the notebook agent can read files inside the pilot. With the app's write toggle enabled it can create new, safely named XYZ files directly in `geometries/`. The preparation uses only the exact XYZ selected during setup. Manual terminal, import and export actions continue to use the user's chosen location.

With the write toggle off, Jonas reported that all three live `write_server_file` calls returned HTTP 409 (“Writing is off on Saga”). The calls targeted a new XYZ in `geometries/`, an XYZ at the pilot root and a text file in `geometries/`. No files were created. After enabling writes, Jonas reported that the same three calls created only `geometries/write-scope-test.xyz`. A subsequent listing confirmed the file at 37 bytes. The XYZ at the pilot root returned HTTP 409 because creation is limited to the geometries directory. The text file inside that directory returned HTTP 409 because only flat, named XYZ files are allowed.

The helper records its attempt in the operator's home before it runs the wrapper. After a lost reply, repeat the same request. A completed attempt returns the checked input. If an attempt is incomplete or differs from the reference, inspect its directory and wrapper logs on Saga. Running setup again deliberately enrolls a fresh attempt. A missing or changed helper requires a matching copy from the workstation checkout.

#### Project job descriptions

Jonas identified three project job descriptions in one directory on Saga and created a sibling directory named `chembook3d-pilot` in his project area for calculation files. The intended write scope is that pilot directory and its children. The generator scripts and job descriptions stay outside it. Use a fresh child directory for each preparation so an existing input cannot be mistaken for new output.

| Job description | Purpose | Pilot coverage |
|---|---|---|
| `min-go_PBEPBE-GD3MBJ_DZ_singlet_CONVER9.jd` | Ordinary geometry optimization to a minimum | Jonas ran the generator with `nosub` in a fresh pilot directory on Saga. The generated input was 39,735 bytes with four Link1 separators. |
| `ts-go_PBEPBE-GD3MBJ_DZ_singlet_CONVER9.jd` | Transition-state geometry optimization | Identified for a later validation against a representative generated TS input. |
| `sp_PBEPBE-GD3MBJ_QZ_singlet.jd` | Single-point energy calculation | Identified for a later validation against a representative generated single-point input. |

Jonas confirmed that the SHA-256 of the live generated input matched the reference after removing only the memory and processor directives from each of its five steps. This verifies byte-for-byte agreement for the manual `nosub` run, including the geometry, routes, custom basis, effective core potential and dispersion settings.

An ordinary minimum from the supplied project job file is one scheduler job with five Gaussian steps. The first HF single point takes the XYZ coordinates and inline basis and effective core potential. A DFT single point and wavefunction stability step precede the geometry optimization. A frequency step follows it. The last four steps read the geometry, basis and guess from the checkpoint produced earlier in the same input. A successful stability step alone does not establish that the geometry has converged.

The local pilot accepts this sequence and the original single-step optimization. Each linked step must name the same attempt-local checkpoint. The later steps must retain the charge and multiplicity and take their geometry and guess from that checkpoint. The app reviews and submits the complete file, including its basis data, dispersion parameters and all five routes. The local stand-in also offers `linked-minimum.jd` with synthetic water data to exercise this case.

Jonas's two-step example, 2026-11-25. Both invocations use the submission workspace as their working directory. The geometry and generated input share a filename stem.

| Operation | Example invocation | Result |
|---|---|---|
| Prepare | `submit_all_DFT.sh minimum.jd example.xyz nosub` | Creates `example.inp` for inspection or revision. No job is submitted. A bare job filename refers to a file in the workspace. The wrapper also accepts an absolute job-file path, so the helper can select the file from a trusted project directory. |
| Submit | `submit_job_g16-C.01 example.inp -n 1 -c 40 -t 24` | Submits the prepared input and creates `example.log` in the submission workspace. This workflow always requests one node. The example requests 40 CPU cores and 24 hours of wall time. |

Jonas confirmed with `command -v` on a Saga login node that the installed submission entry point is `$HOME/tools/uibkvant-tools/runnable/submit_job_g16-C.01`. This lookup identified the wrapper without invoking it.

#### First submission of an enrolled minimum

Preparation alone ends at `nosub`. For the local acceptance test, the fixture operator runs the helper's `enable-submit` command to enroll the fixed wrapper for that preparation. The helper pins the resolved executable and its SHA-256. This command is outside the agent interface. The pilot fixes the resource request at one node, 40 cores and 24 hours. On 2026-11-27 Jonas selected this allocation for the first live submission of the enrolled minimum. The user confirms the attempt in the app. Resource selection for later calculations remains open.

1. Call `prepare_saga_minimum` in the investigation. With submission enabled, the response includes an attempt ID and the approved input revision. The app retains the complete input for review.
2. Request `submit_prepared_job` with the server and attempt ID. The app shows the input, SHA-256, account, host, submission directory and resources. Declining leaves the scheduler untouched. On confirmation, the keeper signs the fixed request for the enrolled attempt. The helper verifies the signature, checks the current input against the approved digest and claims the attempt before invoking the wrapper.
3. The wrapper runs from a fresh `prepare-<attempt>/submission/` directory containing only the verified input. This prevents the wrapper from automatically staging an unrelated matching checkpoint. A single unambiguous `Submitted batch job <id>` stdout line supplies the Slurm ID. An explicit Slurm submission failure is recorded as rejected. Missing or conflicting evidence leaves the attempt uncertain.
4. After a lost reply, use `get_prepared_job` for the same server and attempt. The cluster record survives a disconnect. An accepted submission returns the recorded ID and directory for `check_server_job`. A claimed attempt cannot invoke the wrapper again. If its outcome stays uncertain, the operator checks the scheduler and wrapper records before deciding how to proceed.

The local end-to-end test uses loopback SSH and a scheduler stand-in. Run `uv run pytest -q tests/test_remote.py -k enrolled_saga_minimum` to exercise app review, refusal, acceptance and recovery. The standalone helper and keeper checks are in `tests/test_saga_submission_helper.py` and `tests/test_saga_submission_transport.py`.

Jonas's read-only `namei -l` check on Saga found an operator-owned symlink from the configured command to `../source/job_submission/src/g16-C.01.sh`. The target has mode `0775`. Several directories below the operator's `2700` home have mode `2775`. Group members cannot traverse that home through this path. The helper accepts group write on the resolved installation below a canonical operator-owned home with no group or other permission bits. A group-writable executable must have a single hard link. The helper rechecks the resolved path, permissions and digest before submission. Private authorization records have separate permission checks.

To verify the live installation, copy the checkout's `src/chembook3d/remote/saga_prepare.py` to `~/saga_prepare.py` on Saga and run `python3 ~/saga_prepare.py check-wrapper` in the Saga terminal. This read-only operator command reports the resolved wrapper and SHA-256 using the existing preparation enrollment. Jonas reported `ok: true` on Saga. The command resolved to `$HOME/tools/uibkvant-tools/source/job_submission/src/g16-C.01.sh` with SHA-256 `70df8739fd08842d6c4115425fb00ac072569cc186553f05cb9c64beccb147f7`. This digest identifies the wrapper executable. The Gaussian input has its own digest.

For the live refusal test, the operator installs the checked helper using the existing-enrollment procedure above, then runs `python3 ~/.local/lib/chembook3d/saga_prepare.py enable-submit` in the Saga terminal. This persists the enrolled wrapper and resource request for the current minimum. On 2026-11-27 Jonas reported that Saga enabled submission for the same attempt ID returned by both earlier preparations. In the updated app, prepare the same minimum, request submission review, check the input digest and resources, and choose **Refuse**. Verify with `get_prepared_job` that the attempt is still `prepared` and has no job ID. A live acceptance test is a separate user decision.

The first live refusal test stopped at `prepare_saga_minimum` with HTTP 409 and the older keeper message “Install the matching Saga helper from this checkout.” The approval dialog never opened. That message covers both an unreadable installed helper and a different helper digest. The keeper loads its expected digest at startup. To diagnose the error, compare these read-only checks:

- On Saga, run `python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256((Path.home()/".local/lib/chembook3d/saga_prepare.py").read_bytes().replace(b"\r\n", b"\n")).hexdigest())'`.
- From the workstation's pilot checkout, run `uv run python -c "from chembook3d.remote.saga_access import _sha; print(_sha)"`. This starts a fresh interpreter. If the digests match, refresh the existing keeper using the logout and restart procedure above. If the remote copy is missing or different, install the helper from the matching checkout. Both recovery paths preserve the existing enrollment and attempt.

On the next live check, the agent reported that submission was enabled and the attempt ID and input SHA-256 matched the enrolled minimum. Jonas clicked **Refuse** in the app. The subsequent status lookup returned `state: prepared` and `job_id: null`. The live refusal test passed.

On 2026-11-27 Jonas approved the same minimum in the app. The agent reported one submission call. A separate `get_prepared_job` lookup returned `submitted` with a numeric Slurm ID. Using the returned follow-up arguments, `check_server_job` found a matching wrapper start record at 07:53:07 CET. Its read of the developing Gaussian output was refused because the scratch directory is outside the enrolled pilot. No terminal wrapper record or Gaussian step results were available at that check. The operator subsequently chose a smaller molecule for the end-to-end copy-back test.

The workspace `.log` is the submission wrapper's job record. It reports the separate run directory and is updated as the job completes, times out or fails. The developing Gaussian `.out`, checkpoint and other calculation files are in the run directory. On normal completion the wrapper copies files back to the submission workspace. When Gaussian exits with a fatal error, the wrapper also copies back the `.out`, which contains the Gaussian diagnostic. After a crash or wall-time exit the wrapper updates the workspace `.log` and the calculation files survive in the run directory. Copy-back may or may not have occurred. Inspect the output for this attempt in the workspace if present, or in its verified run directory otherwise.

For agent follow-up, consult the wrapper log for job state and the Gaussian output for scientific progress or termination. The cluster helper must verify a reported run directory against the approved read scope and its association with this job before retrieving files (D266). Report unavailable output explicitly if that check fails or the files are gone.

An earlier completed run can exercise result reading while the live trial runs. The operator can copy its matching wrapper `.log` and Gaussian `.out` into a fresh directory beneath the pilot, preserving their original filenames and modification times. Use the Slurm ID from the archived log and the copied log path with `check_server_job`. Record the wrapper outcome, optimization convergence and frequency results against the archived ID. If only the Gaussian output is available, `summarize_server_file` exercises the chemistry parser alone. An end-to-end trial also needs follow-up against its own receipt and output.

The archived-run check passed on 2026-11-27. Jonas reported that the agent identified the older Slurm job from its wrapper log and that `check_server_job` attributed the sibling output to that job. It reported normal wrapper exit and normal Gaussian termination for all five steps, convergence in the fourth (optimization) step and no imaginary frequencies in the fifth (frequency) step. This verifies result reading from an already copied-back output. The original live run was retired as an acceptance test before its terminal result was observed.

The replacement molecule is [water-pilot.xyz](../../tests/fixtures/gaussian/water-pilot.xyz), a synthetic bent water geometry. The existing singlet minimum job description has basis rules for hydrogen and oxygen. Generate the reference in a fresh `min-test-water-…` directory with the same generator, job description and `nosub`. Check the generated input for neutral singlet water and four Link1 separators, then record its digest before enrolling the new reference. Jonas reported that the manual Saga generator exited with `nosub`, produced the expected O/H/H coordinates with charge and multiplicity `0 1`, and wrote four Link1 separators. The generated water input SHA-256 was `a798aefe57e5b67fd5c16622d056424549832e1631d1e3dc2dc95818d1b7fc90`. Setup selects a new active attempt. The live water acceptance test passed on 2026-11-27. Jonas reported that `get_prepared_job` returned Slurm job ID `21309563`. Using the receipt's follow-up arguments, `check_server_job` attributed `submission/water-pilot.out` in the same attempt to that job and reported normal wrapper exit. All five Gaussian steps terminated normally. The optimization converged in step 4, and step 5 reported three vibrational frequencies with zero imaginary modes. Follow-up required no resubmission.

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

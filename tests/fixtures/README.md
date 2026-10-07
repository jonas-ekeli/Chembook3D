# Test fixtures

| Folder | Files | Source | Licence |
|---|---|---|---|
| `gaussian/` | `aziridinium-phos-full-c1.log` (opt freq, minimum), `aminationTS-full-unfrz-c1.log` (opt(ts) freq), `aminationTS-full-unfrz-c1_sp_tzpop.log` (single point on the TS); Gaussian 16 C.01, ωB97X-D, SMD(THF). The `.thch` files are the outputs of `docs/reference/thermochem_corr_G16.py` on the two frequency jobs. | GoodVibes examples, [patonlab/GoodVibes](https://github.com/patonlab/GoodVibes) commit `744cbfd`, `goodvibes/examples/pes/` | MIT, `gaussian/LICENSE-GoodVibes.txt` |
| `gaussian/` | `dvb_scan_relaxed.log` (`opt=modredundant` dihedral scan, 13 points), `dvb_scan_unrelaxed.log` (rigid `Scan`, 13 points); Gaussian 16, B3LYP/STO-3G | cclib regression data, [cclib/cclib](https://github.com/cclib/cclib) commit `7c17940`, `data/Gaussian/basicGaussian16/` | BSD-3-Clause, `gaussian/LICENSE-cclib.txt` |
| `orca/` | `dvb_gopt.out`, `dvb_ir.out`, `dvb_sp_un_dft.out`, `water_hf_solvent_cpcm.log`, `water_hf_solvent_smd.log` (ORCA 6.0.1); `orca5_dvb_ir.out` (ORCA 5.0, renamed from `dvb_ir.out`); `dvb_scan_unrelaxed.out` (ORCA 6.0 parameter scan, 12 points, cclib commit `7c17940`) | cclib regression data, [cclib/cclib](https://github.com/cclib/cclib) commit `f90be37`, `data/ORCA/` | BSD-3-Clause, `orca/LICENSE-cclib.txt` |
| `xtb/` | `dvb_sp.out`, `dvb_opt.out`, `dvb_ir.out` (xTB 6.6.1) | cclib regression data, same commit, `data/XTB/basicXTB6.6.1/` | BSD-3-Clause, `xtb/LICENSE-cclib.txt` |
| `crest/` | `crest_conformers.xyz` (74 conformers, 14 atoms) | CENSO's test fixtures, [grimme-lab/CENSO](https://github.com/grimme-lab/CENSO) commit `1a82c5a`, `test/unit/fixtures/` | LGPL-3.0, `crest/COPYING.LESSER-CENSO.txt` |

| `sterics/` | `1.xyz` … `18.xyz` (metal complexes, the metal is atom 1) and `reference_data.csv` (the atoms left out and SambVca's %V_bur for each, 3.5 Å, Bondi ×1.17, no hydrogens) | morfeus's buried volume test data, [digital-chemistry-laboratory/morfeus](https://github.com/digital-chemistry-laboratory/morfeus) commit `e5dacf9`, `tests/data/buried_volume/` (line endings changed to LF) | MIT, `sterics/LICENSE-morfeus.txt` |

The files are unchanged copies. Cases none of them cover (an ORCA relaxed scan, whose sample is 6 MB, ORCA `opt freq`, xTB `--ohess`, multi-job ORCA files) are made in the tests by joining two real files. A Gaussian `--Link1--` chain with a custom GenECP basis set and dispersion set by IOps (D59) is written by `tests/gaussian_text.py` (`custom_chain`, `custom_single_point`).

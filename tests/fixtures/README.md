# Test fixtures

| Folder | Files | Source | Licence |
|---|---|---|---|
| `gaussian/` | `aziridinium-phos-full-c1.log` (opt freq, minimum), `aminationTS-full-unfrz-c1.log` (opt(ts) freq), `aminationTS-full-unfrz-c1_sp_tzpop.log` (single point on the TS); Gaussian 16 C.01, ωB97X-D, SMD(THF). The `.thch` files are the outputs of `docs/reference/thermochem_corr_G16.py` on the two frequency jobs. | GoodVibes examples, [patonlab/GoodVibes](https://github.com/patonlab/GoodVibes) commit `744cbfd`, `goodvibes/examples/pes/` | MIT, `gaussian/LICENSE-GoodVibes.txt` |
| `orca/` | `dvb_gopt.out`, `dvb_ir.out`, `dvb_sp_un_dft.out`, `water_hf_solvent_cpcm.log`, `water_hf_solvent_smd.log` (ORCA 6.0.1); `orca5_dvb_ir.out` (ORCA 5.0, renamed from `dvb_ir.out`) | cclib regression data, [cclib/cclib](https://github.com/cclib/cclib) commit `f90be37`, `data/ORCA/` | BSD-3-Clause, `orca/LICENSE-cclib.txt` |
| `xtb/` | `dvb_sp.out`, `dvb_opt.out`, `dvb_ir.out` (xTB 6.6.1) | cclib regression data, same commit, `data/XTB/basicXTB6.6.1/` | BSD-3-Clause, `xtb/LICENSE-cclib.txt` |
| `crest/` | `crest_conformers.xyz` (74 conformers, 14 atoms) | CENSO's test fixtures, [grimme-lab/CENSO](https://github.com/grimme-lab/CENSO) commit `1a82c5a`, `test/unit/fixtures/` | LGPL-3.0, `crest/COPYING.LESSER-CENSO.txt` |

The files are unchanged copies. Cases none of them cover (ORCA `opt freq`, xTB `--ohess`, multi-job ORCA files) are made in the tests by joining two real files. A Gaussian `--Link1--` chain with a custom GenECP basis set and dispersion set by IOps (D59) is written by `tests/gaussian_text.py` (`custom_chain`, `custom_single_point`).

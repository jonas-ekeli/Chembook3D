# Third-party notices

Chembook3D's own code is under the GNU General Public License, version 3 or later
([LICENSE](LICENSE)). The parts below come from others and keep their own licences; all of them
can be combined with GPL-3.0.

## Test fixtures

The sample outputs in `tests/fixtures/` are unchanged copies from public projects. Each folder
holds its licence file, and [tests/fixtures/README.md](tests/fixtures/README.md) names the source
and commit.

| Folder | From | Licence |
|---|---|---|
| `gaussian/` | [GoodVibes](https://github.com/patonlab/GoodVibes); the two `dvb_scan_*` files from [cclib](https://github.com/cclib/cclib) | MIT; BSD-3-Clause |
| `orca/`, `xtb/` | [cclib](https://github.com/cclib/cclib) | BSD-3-Clause |
| `crest/` | [CENSO](https://github.com/grimme-lab/CENSO) | LGPL-3.0 |
| `sterics/` | [morfeus](https://github.com/digital-chemistry-laboratory/morfeus) | MIT |

## Reference thermochemistry script

`docs/reference/thermochem_corr_G16.py` follows GoodVibes (Luchini, G.; Alegre-Requena, J. V.;
Funes-Ardoiz, I.; Paton, R. S. *F1000Research* 2020, 9, 291), which is under the MIT License,
copyright 2017 Robert Paton, University of Oxford; its text is in `tests/fixtures/gaussian/LICENSE-GoodVibes.txt`.

## Web interface

The built interface bundles npm packages under the MIT, ISC and BSD-3-Clause licences, among them
3Dmol.js, React, React Flow and xterm.js. The build writes their licence texts to
`frontend/dist/third-party-licenses.md`, and the read-only HTML copy carries the same texts in a
comment at its top.

## Python packages

The backend's dependencies are installed by uv, not copied into this repository. They are under
permissive licences (MIT, BSD, Apache-2.0, PSF); certifi is under MPL-2.0. `uv.lock` lists the
exact versions.

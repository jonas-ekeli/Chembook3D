# Third-party notices

Chembook3D's own code is under the GNU General Public License, version 3 or later
([LICENSE](LICENSE)). The parts below come from others and keep their own licences; all of them
can be combined with GPL-3.0.

## Test fixtures

The sample outputs in `tests/fixtures/` are copies of public files, renamed or with LF line
endings in a few cases; [tests/fixtures/README.md](tests/fixtures/README.md) names each source,
commit and change, and where its licence text is. `xtb/dce_scan/` was made for this repository and
is under its licence.

| Folder | From | Licence |
|---|---|---|
| `gaussian/` | [GoodVibes](https://github.com/patonlab/GoodVibes); the two `dvb_scan_*` files from [cclib](https://github.com/cclib/cclib) | MIT; BSD-3-Clause |
| `orca/`, `xtb/` | [cclib](https://github.com/cclib/cclib) | BSD-3-Clause |
| `crest/` | [CENSO](https://github.com/grimme-lab/CENSO) | LGPL-3.0 |
| `orca/irc/` | Geoffrey Weal's [ORCA mechanism tutorial](https://github.com/geoffreyweal/ORCA_Mechanism_Procedure) | GPL-3.0 |
| `sterics/` | [morfeus](https://github.com/digital-chemistry-laboratory/morfeus) | MIT |
| `iochem/` | Occhipinti, Nascimento, Foscato, Fogg and Jensen, *Chem. Sci.* 2022, 13, 5107, deposited in [ioChem-BD](https://doi.org/10.19061/iochem-bd-6-113) (University of Bergen) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

## Reference thermochemistry script

`docs/reference/thermochem_corr_G16.py` follows GoodVibes (Luchini, G.; Alegre-Requena, J. V.;
Funes-Ardoiz, I.; Paton, R. S. *F1000Research* 2020, 9, 291), which is under the MIT License,
copyright 2017 Robert Paton, University of Oxford; its text is in `tests/fixtures/gaussian/LICENSE-GoodVibes.txt`.

## Web interface

The built interface bundles npm packages under the MIT, ISC, BSD-3-Clause, Apache-2.0 and other
permissive licences, among them 3Dmol.js, React, React Flow and xterm.js; the Ketcher structure
editor and its Indigo engine (EPAM Systems, Apache-2.0); and the Eclipse Layout Kernel (elkjs, under EPL-2.0 or
GPL-3.0-or-later; used here under GPL-3.0-or-later) for Tidy with springs. The build writes their licence texts to
`frontend/dist/third-party-licenses.md`, and the read-only HTML copy carries the same texts in a
comment at its top.

## Icons

The icon shapes in `frontend/src/icons.tsx` are copied from [Lucide](https://lucide.dev) (ISC
License; copyright Lucide Contributors 2022, and Cole Bemis 2013–2022 for the parts that come
from Feather, MIT License):

> Permission to use, copy, modify, and/or distribute this software for any purpose with or
> without fee is hereby granted, provided that the above copyright notice and this permission
> notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
> SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
> AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
> WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
> NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
> OF THIS SOFTWARE.

## Python packages

The backend's dependencies are installed by uv, not copied into this repository. They are under
permissive licences (MIT, BSD, Apache-2.0, PSF); certifi is under MPL-2.0, and asyncssh (the SSH connection of D122) under EPL-2.0 with GPL-2.0-or-later as its secondary licence. `uv.lock` lists the
exact versions.

## Optional local tools (D213)

Settings, Local calculations downloads these on the user's request into the app's own data
folder; none of them is copied into this repository or into an investigation, and the app runs
them as separate programs:

- [micromamba](https://github.com/mamba-org/mamba) (BSD-3-Clause), pinned by SHA-256 in
  `src/chembook3d/local_tools.py`, which installs the rest from [conda-forge](https://conda-forge.org);
- [Psi4](https://psicode.org) (LGPL-3.0, with BSD and MIT parts) for the DFT methods;
- [tblite](https://github.com/tblite/tblite) (LGPL-3.0-or-later) for GFN2-xTB;
- [geomeTRIC](https://github.com/leeping/geomeTRIC) (BSD-3-Clause), the optimizer;
- [dftd4](https://github.com/dftd4/dftd4), [s-dftd3](https://github.com/dftd3/simple-dftd3) and
  [gCP](https://github.com/grimme-lab/gcp) (LGPL-3.0-or-later), the corrections of the 3c methods;
- optionally [g-xTB](https://github.com/grimme-lab/g-xtb) (GPL-3.0), the Grimme group's build of
  xtb 6.7.1 with g-xTB, downloaded from that repository at a pinned commit and checked by SHA-256
  (D216);
- their dependencies, listed with exact versions in `src/chembook3d/assets/local-tools/`, among
  them OpenBLAS (BSD-3-Clause) on Linux and Intel MKL (Intel Simplified Software License) on
  Windows.

geomeTRIC is also a development dependency, for the tests of the local jobs.

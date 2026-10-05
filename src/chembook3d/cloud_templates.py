"""What a Claude Code cloud session needs in an investigation's repository to run a calculation
job (D93): instructions it reads first, and a SessionStart hook that installs xTB and CREST.

`cloud_jobs.write_cloud_files` writes these into the investigation folder and commits them with
the first job, so the private repository is ready without any environment settings on
claude.ai. They are rewritten whenever this version of the app differs from what is there.
"""

import json
from typing import Any

from chembook3d.investigation import BACKUP_DIR, DB_NAME, FILES_DIR

SETUP_SCRIPT = ".claude/chembook3d/setup-tools.sh"
INSTRUCTIONS = ".claude/CLAUDE.md"
SETTINGS = ".claude/settings.json"
HOOK_COMMAND = f'bash "$CLAUDE_PROJECT_DIR/{SETUP_SCRIPT}"'
HOOK_TIMEOUT = 300  # seconds; the downloads are about 36 MB

XTB_VERSION = "6.7.1"
CREST_VERSION = "3.0.2"
# Pinned release files and their SHA-256, checked before anything is unpacked.
XTB_URL = (
    "https://github.com/grimme-lab/xtb/releases/download/"
    f"v{XTB_VERSION}/xtb-{XTB_VERSION}-linux-x86_64.tar.xz"
)
XTB_SHA256 = "62a8d18778286e815292ee53d76ce447daf460a4dea3782c0f25cbac7019b5df"
CREST_URL = (
    "https://github.com/crest-lab/crest/releases/download/"
    f"v{CREST_VERSION}/crest-gnu-12-ubuntu-latest.tar.xz"
)
CREST_SHA256 = "8e5bd18b06f99741ebd7bb71b3a996295f391b2f076aaeab739740f709e9554d"

SETUP_TEXT = f"""\
#!/usr/bin/env bash
# Written by Chembook3D (D93); rewritten when the app changes it. Do not edit.
# SessionStart hook: installs xTB {XTB_VERSION} and CREST {CREST_VERSION} in a Claude Code cloud
# session, so it can run the calculation jobs in jobs/. Does nothing on other computers.
set -euo pipefail
if [ "${{CLAUDE_CODE_REMOTE:-}}" != "true" ]; then
  exit 0
fi
tools="$HOME/.local/chembook3d-tools"
fetch() {{  # url sha256 folder
  if [ -d "$tools/$3" ]; then
    return 0
  fi
  mkdir -p "$tools/download"
  archive="$tools/download/$3.tar.xz"
  curl -fsSL --retry 3 -o "$archive" "$1"
  echo "$2  $archive" | sha256sum -c --quiet -
  mkdir -p "$tools/$3.part"
  tar -xJf "$archive" -C "$tools/$3.part"
  mv "$tools/$3.part" "$tools/$3"
}}
fetch "{XTB_URL}" "{XTB_SHA256}" "xtb-{XTB_VERSION}"
fetch "{CREST_URL}" "{CREST_SHA256}" "crest-{CREST_VERSION}"
xtb_bin="$tools/xtb-{XTB_VERSION}/xtb-dist/bin"
crest_bin="$tools/crest-{CREST_VERSION}/crest"
if [ -n "${{CLAUDE_ENV_FILE:-}}" ]; then
  {{
    echo "export PATH=\\"$xtb_bin:$crest_bin:\\$PATH\\""
    echo "export XTBPATH=\\"$tools/xtb-{XTB_VERSION}/xtb-dist/share/xtb\\""
    echo "export OMP_NUM_THREADS=$(nproc)"
    echo "export OMP_STACKSIZE=1G"
  }} >> "$CLAUDE_ENV_FILE"
fi
echo "Chembook3D: xtb {XTB_VERSION} and crest {CREST_VERSION} are installed ($xtb_bin, $crest_bin)."
"""

INSTRUCTIONS_TEXT = f"""\
<!-- Written by Chembook3D (D93); rewritten when the app changes it. Do not edit. -->
# Chembook3D calculation jobs

This repository is a Chembook3D investigation: a notebook of a computational chemistry
mechanism study, kept in the database file `{DB_NAME}` and the copied output files in
`{FILES_DIR}/`. The Chembook3D app on the user's computer has it open; it hands calculations
to cloud sessions as job folders in `jobs/`. A session started for a job runs that job and
returns its outputs. The app imports them into the notebook; you never change the notebook.

## Rules

- Never open, change, move, delete or commit `{DB_NAME}`, `{FILES_DIR}/`, `{BACKUP_DIR}/`,
  `.claude/` or any other job's folder. Change only the folder of the job you were asked
  to run, and in it only `outputs/` and `result.json`.
- Do not edit the job's `job.md` or `inputs/`. If they are unclear or impossible, say so in
  `result.json` (status "failed") rather than guessing at chemistry.
- xTB {XTB_VERSION} (`xtb`) and CREST {CREST_VERSION} (`crest`) are installed when the session
  starts (`.claude/chembook3d/setup-tools.sh`). If they are missing, run that script with
  `CLAUDE_CODE_REMOTE=true`, then check `xtb --version` and `crest --version`.
- Run every calculation in a scratch folder outside the repository (for example
  `$HOME/scratch/<job>/<name>`), with `ulimit -s unlimited` first. Copy back only what
  the job asks for.

## Running a job

1. Read `jobs/<job>/job.md`. The structures are in `jobs/<job>/inputs/` (XYZ in Å). The charge
   and multiplicity it gives go to xTB as `--chrg <charge> --uhf <multiplicity - 1>`.
2. Run what it asks. Keep xTB's and CREST's full standard output: redirect it to a file
   (`xtb input.xyz --opt > opt.out 2>&1`). Chembook3D imports that `.out` file (it reads the
   command line, method, solvation, energies and, for an optimisation, the final structure).
3. Copy the results into `jobs/<job>/outputs/`, one subfolder per calculation when there
   are several: each `.out` file; for an optimisation also `xtbopt.xyz`; for CREST its
   output, `crest_conformers.xyz` and `crest_best.xyz`; and whatever else `job.md` asks for.
   No file over 50 MB.
4. Write `jobs/<job>/result.json`:

   ```json
   {{
     "job": "<job>",
     "status": "done",
     "summary": "Two or three sentences on what ran and what came out.",
     "outputs": [
       {{"file": "outputs/opt/opt.out", "program": "xtb", "input": "inputs/ts-guess.xyz",
        "note": "GFN2-xTB optimisation, ALPB(toluene), normal termination"}}
     ]
   }}
   ```

   `status` is "done" or "failed"; with "failed", `summary` says why.
5. Commit only `jobs/<job>/outputs/` and `jobs/<job>/result.json`, and push to this session's
   branch. Do not open a pull request, merge, or push to `main`. The app finds the job's
   `result.json` on whichever branch it is pushed to.
6. Answer with the summary.
"""


def settings_with_hook(existing: dict[str, Any] | None) -> dict[str, Any]:
    """`.claude/settings.json` with the SessionStart hook added, keeping what else is there."""
    settings: dict[str, Any] = dict(existing or {})
    hooks = dict(settings.get("hooks") or {})
    starts = list(hooks.get("SessionStart") or [])
    for entry in starts:
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                hook["timeout"] = HOOK_TIMEOUT
                hooks["SessionStart"] = starts
                settings["hooks"] = hooks
                return settings
    starts.append(
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": HOOK_TIMEOUT}]}
    )
    hooks["SessionStart"] = starts
    settings["hooks"] = hooks
    return settings


def settings_text(existing: str | None) -> str:
    try:
        parsed = json.loads(existing) if existing else None
    except ValueError:
        parsed = None  # not JSON: Claude Code could not read it either
    if not isinstance(parsed, dict):
        parsed = None
    return json.dumps(settings_with_hook(parsed), indent=2) + "\n"

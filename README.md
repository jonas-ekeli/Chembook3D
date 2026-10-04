# Chembook3D

A local-first notebook for computational chemistry mechanism investigations. It combines an infinite 2D reaction map, structured records for every structure and calculation, and an interactive 3D viewer.

**Status:** all five build phases are in: investigations with nodes made by hand or imported (Gaussian, ORCA, xTB, CREST), the reaction map canvas with branches and groups, a 3D view, energy profiles and tables with quasi-harmonic G, and a resume overview.

## Install the tools

You need [Git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/) (it installs Python for you) and [Node.js](https://nodejs.org/) 22 or newer. Node.js is only used to build the interface.

Pick one of the three setups below. On a Windows PC, native Windows is the simplest; WSL works too, but everything, Node.js included, must then be installed inside WSL.

### Windows (PowerShell)

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
```

Close PowerShell and open a new one so it finds the new commands.

### Linux

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
```

Open a new terminal, then `nvm install 22`. Git usually comes with the distribution (`sudo apt install git` on Ubuntu).

### WSL

Use the Linux commands above inside WSL. WSL also sees the Windows programs, so without a Node.js inside WSL the Windows `npm` runs instead, and the build fails with "UNC paths are not supported". After installing, check in a new terminal that both commands print paths that do not start with `/mnt/`:

```sh
which node npm
```

Keep the checkout, and your investigation folders, in your WSL home (for example `~/Chembook3D`), not under `/mnt/c`: files there are slow to reach from WSL, and the investigation database may not lock reliably.

## Run it

The same commands work in PowerShell, Linux and WSL. Get the code once:

```sh
git clone https://github.com/jonas-ekeli/Chembook3D.git
cd Chembook3D
```

Build the interface (again after each `git pull` that changes `frontend/`), then start the app:

```sh
uv run python scripts/build_frontend.py
uv run chembook3d
```

This starts the local server on http://127.0.0.1:8765 and opens it in your browser. If no tab opens (usually the case in WSL), open that address yourself in a Windows or Linux browser. Stop the server with Ctrl+C.

To try it with some data, create a demo investigation and open that folder from the start screen:

```sh
uv run python scripts/make_demo.py demo-investigation
```

Then import an output file with **Import file…** (or drop it on the node list). Sample outputs are in `tests/fixtures/`. In WSL, the folder and file pickers show the WSL file system; Windows drives are under `/mnt/c`, `/mnt/d` and so on.

## Sync investigations between computers

An investigation can be synced through a private GitHub repository, so you can work on it on
several computers. Git must be installed (on Windows, [Git for Windows](https://gitforwindows.org)).

1. Create an **empty private** repository on GitHub (no README).
2. Open the investigation, press **Sync with GitHub…** and paste the repository's address.
3. On another computer, choose **Open from GitHub…** on the start screen.

The app pulls when it opens a linked investigation, and pushes when you close it, press **Sync**,
or stop the app. If both computers changed it since the last sync, it cannot be merged: the app
asks which copy to keep and saves the other.

The app never asks for a password or token; Git signs in. On Windows, Git for Windows opens a
GitHub sign-in in the browser the first time. On Linux or WSL, sign in once with the GitHub CLI:

```sh
gh auth login
gh auth setup-git
```

Use the same Chembook3D version on every computer. When one computer has upgraded an
investigation, an older version refuses to open it and asks you to update. To update:

```sh
git pull
uv sync
uv run python scripts/build_frontend.py
```

## Share a read-only copy

**Share read-only copy…** saves the open investigation as one HTML file you can send to a
supervisor or co-author. It opens in a current browser by double-click, offline, with nothing to
install, and nothing in it can be changed. It shows the canvas, notes, calculations, 3D
structures, and energies at every level. The energy profile and table show the pathways you
added under **Profile and table**, or one pathway per branch if you added none. It leaves out
the folders and file paths on your computer, the output files themselves and the change history.

After updating Chembook3D, build the interface again (`uv run python scripts/build_frontend.py`),
since the viewer inside the file is built with it.

## Work with Claude

Claude Code or Claude Desktop can read and change the investigation open in the app, through
`chembook3d mcp`, an MCP server that talks to the running app on this computer. It uses your
Claude subscription; no API key is needed. Changes appear on the canvas at once and are marked
"claude" in the history. When Claude wants to delete something, dissolve a group or remove
coordinates, the app asks you first and does nothing unless you click **Confirm**. When you say
"this node", Claude asks the app what you have selected.

After `uv run chembook3d` has run once, the command is in the project's `.venv`. Add it to
Claude Code once, with the full path of your Chembook3D folder.

Windows (PowerShell):

```powershell
claude mcp add --scope user chembook3d C:\path\to\Chembook3D\.venv\Scripts\chembook3d.exe mcp
```

Linux and WSL:

```sh
claude mcp add --scope user chembook3d /path/to/Chembook3D/.venv/bin/chembook3d mcp
```

In WSL, add it in the WSL Claude Code, where the app runs. For Claude Desktop (Windows), add
this to its `claude_desktop_config.json` (Settings → Developer → Edit Config) and restart it:

```json
{
  "mcpServers": {
    "chembook3d": {
      "command": "C:\\path\\to\\Chembook3D\\.venv\\Scripts\\chembook3d.exe",
      "args": ["mcp"]
    }
  }
}
```

Start the app and open an investigation first; otherwise Claude's tools say the app is not
running. If the app runs on another port, add `--url http://127.0.0.1:<port>` after `mcp`
(in the JSON, as two more entries in `args`).

## Develop

```sh
uv sync
uv run ruff check .
uv run pytest
```

See [CLAUDE.md](CLAUDE.md) for the layout and the full list of commands.

## Specification

- Specification pack: [docs/spec/README.md](docs/spec/README.md)
- Decisions and open questions (source of truth): [docs/spec/08-decisions-and-open-questions.md](docs/spec/08-decisions-and-open-questions.md)
- Implementation roadmap: [docs/spec/09-implementation-roadmap.md](docs/spec/09-implementation-roadmap.md)
- Reference thermochemistry script (oracle for quasi-harmonic G): [docs/reference/thermochem_corr_G16.py](docs/reference/thermochem_corr_G16.py)

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

This starts the local server on http://127.0.0.1:8765 and opens it in your browser. If no tab opens (usually the case in WSL), open that address yourself in a Windows or Linux browser. Stop the server with Ctrl+C, or with **Shut down** in the app.

### Start it without a terminal

Create a Chembook3D shortcut once, from the folder of your checkout:

```sh
uv run chembook3d shortcut
```

On Windows it goes on the desktop and in the Start menu, on Linux in the app menu, and in WSL on the Windows desktop and Start menu (it starts the app inside WSL). A double-click then:

1. opens a browser tab that shows what it is doing;
2. updates the checkout with `git pull`, but only when it is on `main` with no local changes (otherwise it starts the version you have, and the app says why it was not updated);
3. rebuilds the interface only when `frontend/` changed since the last build, and installs new Python packages if the update asks for them;
4. starts the server in the background, with no terminal window, and the tab turns into the app.

If Chembook3D is already running, the shortcut only opens a tab. Started this way, the server stops by itself about 10 seconds after you close the last Chembook3D tab (a reload is fine), or after 5 minutes without any tab; **Shut down** in the top bar stops it at once. What the launcher did is in `launcher.log` in the app's config folder (`%LOCALAPPDATA%\chembook3d` on Windows, `~/.config/chembook3d` on Linux and WSL). If your browser puts background tabs to sleep for a long time, the server may stop while a tab is asleep; start it again with the shortcut.

Run `uv run chembook3d shortcut` again if the shortcut stops working, for example after moving the checkout or reinstalling uv, Python or Node.js.

To try it with some data, create a demo investigation and open that folder from the start screen:

```sh
uv run python scripts/make_demo.py demo-investigation
```

Then import an output file with **Import file…** (or drop it on the node list). To import a whole folder of results at once, browse to it in that dialog and press **Import this folder…**: every output gets a proposed node (by geometry, by a planned node's guess or by file name), which you can check and change before anything is written. Sample outputs are in `tests/fixtures/`. In WSL, the folder and file pickers show the WSL file system; Windows drives are under `/mnt/c`, `/mnt/d` and so on.

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

## Saga and other SSH servers

The **Saga** button opens a tab in the side panel (beside Claude) for logging in to Saga with
your password and the one-time code from your authenticator app, as `ssh` asks for them. Fill
in your user name once; the host is `saga.sigma2.no`. The first time, the app shows Saga's host
key fingerprint: compare it with the one Sigma2 publishes, then trust it. Once logged in, the tab
is a terminal on Saga with your usual login environment, and shows the directory you are in.

The connection stays open in a small background process on this computer, so closing
Chembook3D and starting it again brings back the same terminal without a new login. A restart
of the computer, sleep that drops the network, or Saga closing the connection needs a new
login; **Log out** ends it, and it logs itself out after 12 hours without use. Your password
and code are sent once and never saved, and a refused login is never tried again by itself
(Sigma2 blocks addresses that keep failing). The **+** tab adds another server, such as Betzy.

**Import from here** lists the output files (`.log`, `.out`, `.output`, `.xyz`) in the directory
the terminal is in. Pick one to import it as from the file browser, or several for a batch
import that matches each to a node. The files are copied into the investigation with
`saga:/cluster/…` as their origin; nothing on Saga changes.

**Send to Saga**, beside Save .xyz in a node's coordinates, writes the node's structure as
`<label>.xyz` into the same directory, ready for an input file. If a file of that name is
already there, the app asks before replacing it; it never deletes anything on Saga.

## Claude in the app

The **Claude** button opens a panel beside the notebook running
[Claude Code](https://code.claude.com/docs/en/setup), Anthropic's command-line assistant, with
your own Claude account (a Pro or Max plan, or another account Claude Code accepts). Chembook3D
stores no sign-in and sends nothing to Claude itself; what Claude reads in the conversation goes
to Anthropic under your account's settings.

Install Claude Code once on each computer, in the same system the app runs in (in WSL, inside
WSL), then run `claude` once in a terminal to sign in:

```sh
# Windows PowerShell
irm https://claude.ai/install.ps1 | iex
# Linux, WSL or macOS
curl -fsSL https://claude.ai/install.sh | bash
```

In the panel, **New conversation** starts Claude Code for the open investigation and **Continue
last conversation** picks up its last one. Claude works on the notebook through the app's own
notebook tools, asks before changing anything, and cannot run commands, edit files or browse
the web from the panel. **Hide** keeps it running; **Stop**, closing the investigation or
reloading the page ends it.

Changes Claude makes appear on the canvas at once and are marked "claude" in the history. When
Claude wants to delete something, dissolve a group or remove coordinates, the app asks you first
and does nothing unless you click **Confirm**. When you say "this node", Claude asks the app what
you have selected.

### Calculations in the cloud

Claude can hand an xTB or CREST calculation to a
[Claude Code cloud session](https://code.claude.com/docs/en/claude-code-on-the-web) on your
account, for example "optimise this TS guess with GFN2-xTB in toluene". It writes a job folder
`jobs/<date>-<name>/` in the investigation (the structures, what to run and return), shows it
to you, and starts it only when you agree: the app pushes that folder to the investigation's
GitHub repository and starts the session, and Claude gives you its claude.ai link, where you can
follow it from any device. The session installs xTB 6.7.1 and CREST 3.0.2 itself, runs the job
and pushes the outputs to a branch; Claude then fetches them and imports them into the notebook.
The cloud session never touches the investigation's database.

This needs:

- the investigation linked to a private GitHub repository (see Sync above);
- Claude Code signed in with a plan that includes Claude Code on the web (Pro, Max, Team or
  Enterprise), and GitHub connected to it with the
  [Claude GitHub App](https://github.com/apps/claude) installed on the investigation's
  repository, once, on github.com, so the session can push its results (without the app,
  Claude Code uploads the folder instead, and the session's push is refused; install the app
  and ask Claude to tell the session to push again, its results wait there);
- the cloud environment's network access at its default, Trusted, so the session can download
  xTB and CREST from GitHub.

Nothing is approved in a separate terminal. When Claude Code asks something before it starts
the session (the first time, whether it may trust the investigation folder), the Claude panel
opens with that question at its top and you answer it there. The session runs in Claude
Code's auto mode, so it runs the job to the end without waiting for you to approve its steps
on claude.ai.

The first job also adds `.claude/` to the investigation's repository (the setup script and the
instructions the cloud session follows). The branches the sessions push stay on GitHub until you
delete them there.

### Claude Code in your own terminal, or Claude Desktop

The panel's notebook tools are `chembook3d mcp`, an MCP server that talks to the running app on
this computer. Claude Code in your own terminal, or Claude Desktop, can use them too, to read and
change the investigation open in the app. It uses your Claude subscription; no API key is needed.

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

## License

Copyright (C) 2026 Jonas Ekeli

Chembook3D is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

The licence covers the program, not your investigations: the files you import, the notebooks you
keep and the data in the read-only copies you export stay yours. The test fixtures, the reference script's
GoodVibes basis and the bundled web libraries keep their own licences; see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

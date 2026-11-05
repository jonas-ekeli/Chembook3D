# Chembook3D user guide

The guides for the parts of Chembook3D beyond the basics. Installing and starting the app are in the [README](../README.md#install-the-tools).

- [Sync investigations between computers](#sync-investigations-between-computers)
- [Share a read-only copy](#share-a-read-only-copy)
- [Saga and other SSH servers](#saga-and-other-ssh-servers)
- [Claude in the app](#claude-in-the-app)
  - [Calculations in the cloud](#calculations-in-the-cloud)
  - [Claude Code in your own terminal, or Claude Desktop](#claude-code-in-your-own-terminal-or-claude-desktop)

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
structures, and energies at every level. The energy profile and table show the pathways shown in
the profile open under **Profile and table**, relative to that profile's own reference node, or
one pathway per branch if none is shown. It leaves out
the folders and file paths on your computer, the output files themselves and the change history.

After updating Chembook3D, build the interface again (`uv run python scripts/build_frontend.py`),
since the viewer inside the file is built with it.

## Saga and other SSH servers

The **HPC** button opens a tab in the side panel (beside Claude) where you choose the cluster
(Saga first) and log in to it with your password and the one-time code from your authenticator app, as `ssh` asks for them. Fill
in your user name once; the host is `saga.sigma2.no`. The first time, the app shows Saga's host
key fingerprint: compare it with the one Sigma2 publishes, then trust it. Once logged in, the tab
is a terminal on Saga with your usual login environment, and shows the directory you are in.
The button and the tab then carry the cluster's name (**Saga**); **+** beside the tab logs in to
another cluster at the same time.

The connection stays open in a small background process on this computer, so closing
Chembook3D and starting it again brings back the same terminal without a new login. A restart
of the computer, sleep that drops the network, or Saga closing the connection needs a new
login; **Log out** ends it, and it logs itself out after 12 hours without use. Your password
and code are sent once and never saved, and a refused login is never tried again by itself
(Sigma2 blocks addresses that keep failing). **Add a cluster…** in the tab's Cluster list adds
another server; Betzy and Olivia are offered ready-filled.

**Import from here** lists the output files (`.log`, `.out`, `.output`, `.xyz`) in the directory
the terminal is in. Pick one to import it as from the file browser, or several for a batch
import that matches each to a node. The files are copied into the investigation with
`saga:/cluster/…` as their origin; nothing on Saga changes.

**Send to Saga**, beside Save .xyz in a node's coordinates, writes the node's structure as
`<label>.xyz` into the same directory, ready for an input file. If a file of that name is
already there, the app asks before replacing it; it never deletes anything on Saga.

**X11 windows**: tick it in the Saga tab and start Xming, and programs such as Molden typed in
the terminal (`molden TS1-2.out &`) open their windows on this computer, as from a terminal with
Xming. The display is `localhost:0.0`, Xming's own. The change takes effect in a new shell; the
tab offers **New shell** and says when no X server answers.

**Inspect** a file without importing it: type `inspect TS1-2.out` in the terminal, or pick a file
in **Import from here** or in the import dialog's file browser and press **Inspect**. The window
shows the job steps, plays the optimization with its energy and convergence criteria (as Molden
shows them), and animates the normal modes. For a job still running, **Refresh** (or **Every
30 s**) reads it again; **Import this file** goes on to the import. It also opens xyz files and
xTB's `xtbopt.log`. Nothing is imported and nothing on Saga changes.

To look at several jobs, type `inspect *.log` (any wildcard or several names), tick several files
in **Import from here**, or type a filter such as `*.log` in the file browser and press **Inspect
the N matching files**. The window then steps through them with **Previous file** and **Next
file**, or a list to jump to one.

**Join scans** in the scan editor: type `scans MCB_1_scan.log MCB_1_scan2.log` (or `scans *scan*.log`)
in the terminal, or tick the files in **Import from here** and press **Scan editor**. In the
editor, **Add from Saga…** adds more files from the terminal's directory, **Refresh** reads a scan
still running again and the path follows it, and **Save to Saga** writes the joined path into the
terminal's directory, never over a file there (a name taken becomes `_v2`). After pulling this
change, log out of Saga and in again.

**Ask Claude about files on Saga**: while you are logged in, the Claude panel can read the files
in the directory the Saga terminal is in, and in the directories below it, without importing
them: "did TS1 converge?", "why did the job in opt2 stop?", "how many imaginary frequencies has
the last frequency job?". `cd` in the terminal to where the jobs are first; Claude cannot read
above that directory, cannot log in, and never deletes or runs anything on Saga.

**Let Claude write new files here**: tick it in the Saga tab and Claude may create new files
(inputs, job scripts, xyz files, notes) in the terminal's directory and in new or existing
folders below it, until you log out or press **Stop**; it is off again at every login. The tab
lists what Claude wrote. Claude never replaces, edits or deletes a file: for a change it writes
a copy such as `TS1_v2.gjf` and shows you the difference, and if you want files deleted it gives
you an `rm` command to copy and run yourself. Names starting with a dot are refused, and a file
is at most 1 MB.

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

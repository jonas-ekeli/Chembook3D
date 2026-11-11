# Chembook3D user guide

How to use Chembook3D once it runs. Installing and starting the app are in the
[README](../README.md#install-the-tools); what the app computes, and by which rules, is in the
README's [How energies are computed](../README.md#how-energies-are-computed).

- [Find your way around](#find-your-way-around)
- [Build the mechanism](#build-the-mechanism)
- [Energy profiles and the table](#energy-profiles-and-the-table)
- [Analyses](#analyses)
- [Structures in 3D](#structures-in-3d)
- [Calculations on this computer](#calculations-on-this-computer)
- [Sync investigations between computers](#sync-investigations-between-computers)
- [Share a read-only copy](#share-a-read-only-copy)
- [Saga and other SSH servers](#saga-and-other-ssh-servers)
- [Claude in the app](#claude-in-the-app)
  - [Calculations in the cloud](#calculations-in-the-cloud)
  - [Claude Code in your own terminal, or Claude Desktop](#claude-code-in-your-own-terminal-or-claude-desktop)

## Find your way around

The start screen creates or opens an investigation folder. Inside an investigation:

- the **outline** on the left lists reaction steps, branches, groups and nodes, with **Add node**
  and **Import…** at its top;
- the **canvas** in the middle holds the node cards and the transitions between them; the tabs
  above it switch to **Analyses** and **History**;
- the **inspector** on the right shows what is selected: a node's structure, calculations and
  energies, or a group, branch or transition;
- the **top bar** holds the investigation menu (click the investigation's name: sync, basis sets,
  read-only copy, **Shut down Chembook3D**), Settings, Local jobs, and the optional HPC and Claude
  panels.

Most actions are also in a right-click menu on a card, an arrow or the empty canvas, and every
command can be found by name in the command palette (Ctrl+K, ⌘K on a Mac). Press **?** for the
keyboard shortcuts. Settings marks which settings belong to this computer and which to the open
investigation.

## Build the mechanism

**Nodes.** **Add node** makes an empty node; **Import…** reads an output file into a new node, or
a CREST ensemble into a group. You can also drop files on the canvas, or on a selected node's
inspector to add calculations to it. The preview shows which node each job goes to and its level
of theory before anything is written; see the README for how a file is
[matched to a node](../README.md#from-output-file-to-energy). A whole folder is imported with
**Import this folder…** in the import dialog's file browser.

**Transitions, steps and branches.** Draw an arrow from one card to another to add a transition.
Reaction steps (the stages of the mechanism) are listed in the outline, where you can reorder
them; a node is placed on a step. Branches hold lines of descent, for example one per catalyst;
**Split into branches…** on a node starts new ones from it. Free species that join or leave on a
transition (ethylene, a product) are added with **Free species…** on the transition, or imported
with **Import free species…**.

**Groups.** Select conformers or rotamers and use **Create group…** (or **Add to group…** for an
existing one). A representative stands for the group on the canvas, and the cards can show each
member's Boltzmann population. **Order members…** sets their order; **Expand groups** /
**Collapse groups** in the canvas bar opens or closes them all.

**Keeping a large canvas readable.**

- **Tidy** in the canvas bar lays the canvas out **In step columns**, **With springs** (a new
  layout where a cycle comes out as a loop), or **With springs, in place**; **Undo tidy** puts it
  back. **Tidy selection** and **Tidy canvas** are in the right-click menus, and **Arrange branch**
  in a branch's inspector.
- **Move into new sub-investigation…** folds a part of the mechanism into one box; **Expand here**
  opens it in place, and **Remove sub-investigation…** dissolves it again.
- **Add pinned note** pins a note to a card; notes can hold text and images.

**History and undo.** Every change is listed in the **History** tab, marked as made by you, an
import or Claude. A single field can be set back with **Restore old value**; an import, or a whole
batch import, is undone with **Undo import** or **Undo batch** on its entry, after a preview of
what will be removed.

## Energy profiles and the table

**Profile and table** in the canvas bar opens the energy drawer under the canvas. The canvas bar
also chooses the level of theory, the energy type (E, H, G or G_qh), the reference node, and
whether counterpoise-corrected values are used (**with BSSE**).

1. Right-click the first node of a pathway and choose **Start energy pathway here**.
2. In the drawer, continue the pathway one step at a time (**Continue with**), or add a branch
   pathway beside it. A pathway that comes back to its first state closes the cycle.
3. The **Profile** tab draws it; the **Table** tab lists relative values, step barriers and the
   absolute energies behind them, with a Check column for the warnings.

A point shows **n/a** when its atoms or charge are not those of the profile's reference, and the
note **W-COMPARE** above the profile says what differs (for example "C2H4 more"). Usually an arrow
on the way lacks a free species that joins or leaves there, or the pathway belongs to another
system and needs a profile of its own.

✕ removes a pathway from the profile; **Undo** under the list puts the last one removed back.
A pathway that is already in the profile is not added twice. If a node on a pathway is deleted,
the pathway stays in the list as **Not drawn** until you remove it.

A drawer can keep several saved profiles, each with its own pathways and reference: **New**, and
the profile menu's **Rename**, **Duplicate** and **Delete…**. **Style…** changes the profile's
look, and **Export** writes **Profile as PNG**, **Profile as SVG** or **Table as CSV**.

Temperature, the quasi-harmonic cutoff and the standard state (1 atm or 1 M) are set per
investigation in Settings → **Thermochemistry**; every change is recorded in the history.

## Analyses

The **Analyses** tab holds analyses that are saved with the investigation and carry the warnings
of the values they use:

- **New selectivity**: ΔΔG‡ and ee/de between outcomes, each a set of transition states (also
  **Selectivity…** on a multi-selection).
- **New turnover**: the energetic span model (TOF, TDTS, TDI) of a closed cycle.
- **New conformer equilibrium**: populations and equilibration times between conformers (also
  **Conformer equilibrium…** on a multi-selection).

**Sterics.** **Compare sterics** on a selection, or in a branch's inspector, computes the buried
volume and steric maps; **Choose its atoms…** picks the centre and the atoms left out (the other
ligands). Settings → **Steric maps** sets the map colours.

## Structures in 3D

Selecting a node shows its structure in the inspector (Space opens it larger). The 3D view
measures distances, angles and dihedrals, plays an optimization, scan or IRC step by step,
animates normal modes, and can be popped out into its own window. **Overlay in 3D** on a selection of
2 to 12 nodes aligns them in one view; **Save as new set…** keeps the alignment. **Match atoms…**
on two selected nodes finds which atom in one is which atom in the other.

**Editing a structure.** Coordinates can be edited as text, copied (**Copy xyz**) or saved
(**Save .xyz**). **Edit structure in 3D…** opens the structure editor: change distances, angles
and dihedrals, swap elements, add fragments or a molecule from **SMILES…**, **Add hydrogens**,
**Flip** a fragment, and **Minimize** (a quick force-field clean-up) with chosen atoms frozen and
distances, angles or dihedrals held. **Draw…** opens a 2D drawing
editor (Ketcher), and **Draw from this structure…** starts it from the current structure.
Editing a node that already has calculations makes a new, derived node.

**IRC.** **IRC…** on a transition-state node opens the IRC window. It keeps the TS's IRC apart
from the node: **Import IRC…** adds the path in each direction, and **Import end-point job…** the
optimization and frequency job at each end. The chart joins both directions through the TS.

**Scan editor.** The **Scan editor** in the top bar joins several scan outputs into one path,
which can be reversed, trimmed and split, and saves it; files can come from this computer or from an SSH server (see
[Join scans](#join-scans)). **Scan path…** on two nodes joined by a transition asks a cloud session
for an xTB path between them (see [Calculations in the cloud](#calculations-in-the-cloud)).

**Inspect** reads an output file without importing it: the job steps, the optimization with its
energy and convergence criteria, and the normal modes. It is offered in the import dialog's file
browser, in the HPC tab and for local jobs.

## Calculations on this computer

Chembook3D can refine a structure on your own computer: an optimization, a transition-state
search, a relaxed scan, or frequencies, imported onto the node when it is done. It is optional,
and meant for quick structures, not production runs. Windows and Linux on x86-64 are supported.

**Install the tools once.** Settings → **Local calculations** → **Install** downloads Psi4,
tblite, geomeTRIC and the dispersion corrections from conda-forge, about 1.5 GB, with no account.
It takes 5 to 10 minutes and keeps going if you close the tab. The tools go into the app's own
data folder (`%LOCALAPPDATA%\chembook3d\tools` on Windows, `~/.local/share/chembook3d/tools` on
Linux), never into an investigation. **Install g-xTB** adds the Grimme group's g-xTB (about
40 MB). The same section sets how many cores and how much memory a job may use. Without the
interface, `uv run python -m chembook3d.local_tools install` (and `install-gxtb`) does the same.

**Refine a structure.** **Refine geometry…** on a node (right-click menu or inspector) opens the
job dialog:

| Method | What it is |
|---|---|
| GFN2-xTB | Tight binding; seconds to minutes. |
| g-xTB | Newer tight binding, closer to DFT, a few times GFN2-xTB's cost (after **Install g-xTB**). |
| B97-3c, r2SCAN-3c | Fast composite DFT through Psi4; hours for a 70-atom metal complex. |
| ONIOM(r2SCAN-3c:GFN2-xTB) | r2SCAN-3c on the metal and the atoms around it, GFN2-xTB on the rest. |

Set the charge and multiplicity, then choose what to run: **Search for a transition state** (with
the reacting atoms, if you know them), a **Relaxed scan** of one coordinate, several coordinates
together, or a **2D grid** shown as a surface map (GFN2-xTB and g-xTB only), **Frequencies at the
result** (GFN2-xTB and g-xTB), and held distances, angles or dihedrals. The dialog shows an
estimate of how long the job takes, which gets better after each job on this computer.

**Follow and import.** Jobs run one at a time, as programs of their own, so they keep running when
the app is closed. **Local jobs** in the top bar lists them with **Inspect** (live, every 5 s),
**Cancel** and **Show node**. Each job is a folder under `local-jobs/` in the investigation, which
sync leaves out; the result is imported onto its node when the job ends.

## Sync investigations between computers

An investigation can be synced through a private GitHub repository, so you can work on it on
several computers. Git must be installed (on Windows, [Git for Windows](https://gitforwindows.org)).

1. Create an **empty private** repository on GitHub (no README).
2. Open the investigation, press **Sync with GitHub…** and paste the repository's address.
3. On another computer, choose **Open from GitHub…** on the start screen.

The app pulls when it opens a linked investigation, and pushes when you close it, stop the app,
or sync by hand: the sync chip in the top bar shows the state ("Up to date", "Not pushed", …) and
syncs when clicked, as does **Sync with GitHub** in the investigation menu. If both computers changed it since the last sync, it cannot be merged: the app
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

### Import and send files

**Import from here** lists the output files (`.log`, `.out`, `.output`, `.xyz`, `.cml`; tick
**Show every file** for the rest) in the directory the terminal is in. Pick one to import it as from the file browser, or several for a batch
import that matches each to a node. The files are copied into the investigation with
`saga:/cluster/…` as their origin; nothing on Saga changes.

**Send to Saga**, beside Save .xyz in a node's coordinates, writes the node's structure as
`<label>.xyz` into the same directory, ready for an input file. If a file of that name is
already there, the app asks before replacing it; it never deletes anything on Saga.

### X11 windows

Tick **X11 windows** in the Saga tab and start Xming, and programs such as Molden typed in
the terminal (`molden TS1-2.out &`) open their windows on this computer, as from a terminal with
Xming. The display is `localhost:0.0`, Xming's own. The change takes effect in a new shell; the
tab offers **New shell** and says when no X server answers.

### Inspect a job

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

### Join scans

To join scans in the scan editor, type `scans MCB_1_scan.log MCB_1_scan2.log` (or `scans *scan*.log`)
in the terminal, or tick the files in **Import from here** and press **Scan editor**. In the
editor, **Add from Saga…** adds more files from the terminal's directory, **Refresh** reads a scan
still running again and the path follows it, and **Save to Saga** writes the joined path into the
terminal's directory, never over a file there (a name taken becomes `_v2`). After updating Chembook3D, log out of Saga and in again
so the background connection picks up the new version.

### Claude and files on Saga

While you are logged in, the Claude panel can read the files
in the directory the Saga terminal is in, and in the directories below it, without importing
them: "did TS1 converge?", "why did the job in opt2 stop?", "how many imaginary frequencies has
the last frequency job?". `cd` in the terminal to where the jobs are first; Claude cannot read
above that directory, cannot log in, and never deletes or runs anything on Saga.

Tick **Let Claude write new files here** in the Saga tab and Claude may create new files
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

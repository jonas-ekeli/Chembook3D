# Changelog

Chembook3D has no released version yet. Version tags (`v*`) start the release build
(`.github/workflows/release.yml`), which keeps a wheel and a source archive as workflow
artifacts and publishes nothing.

## Unreleased

- A minimum's imaginary mode from a local GFN2-xTB, g-xTB or MACE-OMOL-0 frequency job can be fixed on
  this computer (D261): Fix imaginary mode in the frequency calculation tries the structure pushed both
  ways along the mode, keeps the lower try without an imaginary mode, and offers to replace the
  geometry at that level. Claude starts one with `start_local_fix_mode`.
- An optimization at a level a node already has can replace the node's geometry at that level
  (D260), when picked in the import dialog or in Local jobs and confirmed; a derived node stays the
  default. What it replaces, and what sits on that, is removed (D259).
- One calculation can be removed from its node (D259): Remove… in its record, after a confirm that
  lists the frequency jobs and single points sitting on it, which go with it. Claude asks for it with
  `remove_calculation`.
- A local refine can start from another structure (D258): one of the node's other geometries, pasted XYZ
  text or a built structure, with the node's atoms in its order; the result is imported onto the node
  by the usual rules, so a free species takes a level it lacks beside its geometry.
- Energies are reused between edits (D257): after an edit the energy options and the energy view
  recompute only the nodes the edit touched, instead of every node.
- The canvas no longer reads every scan shown on an edge from its file on each load (D256): with
  more than 32 such scans every load took seconds; each scan is now read once.
- A single point or frequency job now remembers which calculation's geometry it is at (D255), so a
  later optimization or a level override no longer moves it to another geometry: a corrected
  optimization level carries its single points with it, and a geometry level set by hand against
  the coordinates gets a warning. Older investigations are linked once when opened, with no value
  changing.
- Duplicate TSs and conformers are now found at the analysis's level of theory (D254): two nodes
  whose MACE geometries agree count once at MACE//MACE even when their DFT geometries differ. A
  card no longer shows a relative energy across other atoms than the reference's, as profiles
  already did (D221).
- Which geometry a calculation is at is decided by one rule everywhere (D253): the import preview
  now agrees with what is stored and with energies (a node whose optimization ran elsewhere, a
  frequency job whose coordinates match none), and undoing an import is blocked while a later
  single point or frequency job takes its geometry level from it.
- The geometry tolerance (Same geometry) is now kept in each investigation (D252), so a folder
  matches calculations to geometries the same way on every computer; an existing one takes the
  app's current value once when it is opened, and each change is in its history.
- A level override that leaves no calculation at the old level now moves the saved selectivities,
  turnovers and conformer equilibria on it, and the energy view, to the new level, in the
  history (D251); an analysis left at an empty level says so instead of "No G at this level".
- The node panel's imaginary-frequency count is now from the frequency job the shown value
  was checked against, also for E, rather than the node's latest frequency job (D250).
- A free species' warnings (an imaginary frequency, spin contamination, MACE thermochemistry
  and the others) now show wherever its energy is added: profiles, the energy table, edges,
  cards, selectivities and equilibria (D249).
- The database is backed up before a one-off repair runs, also when no upgrade is needed
  (D248); a repair that keeps failing is backed up once.
- Normal modes take about an eighth of the room (D247): they move to a table of their own,
  compressed, with every number kept, and the upgrade shrinks the database file (a real
  investigation: 43 MB to about 15 MB). Sync now warns when a synced file, the database above
  all, reaches 80 MB, before GitHub's 100 MB limit refuses the push (FR-SYNC-10).
- Lower levels beside a node's geometry (D246): a MACE or xTB optimization from a DFT geometry
  is added beside it (the node keeps its geometry), a free species also takes a new level from
  a built structure, and a local single point can be run at any of the node's geometries, so one
  free species holds every level of its molecule. Refine geometry and Single point now open on a
  free species.
- TS guess correction (D239): optionally, the TS guess's forming bond is held at six lengths
  and the frame with the highest PBE-D3M(BJ)/def2-SVP single point becomes a second TS node.
- TS guess on this computer (D238): two structures joined by an edge give a starting structure
  for a TS, found with MACE-OMOL-0 (both ends minimized, an IDPP band, Sella's saddle search and
  a check that the mode is on the reacting atoms), imported as a new TS node between them.
- MACE-OMOL-0 on this computer (D237): an optional install of the machine-learned potential and
  its own environment, after accepting the model's licence; Refine geometry with it keeps the
  pi-complexes and cycloaddition barriers that xTB loses, for guess structures.
- Claude reads PDFs (D232): a paper or its supporting information on this computer, page by
  page, with the pages a compound's label is on and pages or parts of pages as pictures it can
  see; scanned pages are said to have no text.
- Claude checks a structure read from a paper (D233): it draws a SMILES, a build or a node in 2D
  as a picture, with its formula and each metal's bonded atoms.
- Crystal structures (D234): Claude searches the open Crystallography Open Database and reads an
  entry or a CIF as its whole molecules, ready to save as nodes; Inspect opens CIFs.
- Calculations on this computer (D213 to D220): a tools installer and a detached runner; refine
  with GFN2-xTB, optional g-xTB, B97-3c, r2SCAN-3c and an ONIOM core; transition state searches
  with frequencies; relaxed and 2D grid scans shown as an energy surface; a running job can be
  inspected point by point.
- Faster with large investigations: the canvas, node list and overview load in a fixed number of
  queries, the energy view lists each edge once, fewer fetches after each change, and a smoother
  canvas with many cards.
- Notebook ownership: a new investigation is created in one commit, and a late create or open no
  longer takes over an investigation that is open elsewhere.
- Fixes: stale results and pressure safety from an audit, the canvas drawn only once the saved
  view is restored, Shut down from an investigation in one click, and 3D viewers that release
  their page listeners.
- Arrows to sub-investigation stubs meet each end on the facing sides (D212).
- Energy profiles (D221): a branch that closes a cycle and holds groups starts its pathway at its
  first step; a point with other atoms or another charge than the reference shows n/a with the
  reason (W-COMPARE) instead of a value far off the axis; a removed pathway can be put back, a
  pathway is added once, and one that lost a node stays listed.
- Editing a pathway (D228): an arrow into a group takes the group and the pathway goes on; each
  step has a menu to swap it for another conformer or node, branch off a second pathway, start
  or end the pathway there, and move it along the axis; a pathway grows at its start too
  ("Start before"), and can follow an arrow backwards.
- Documentation: a restructured code map, an architecture map, a user guide covering the core of
  the app, and README screenshots in dark mode.
- CI: parallel pytest, time limits and pinned actions; then dependency audits, CodeQL, a
  Python 3.13 backend job, a bundle size budget and a release build.
- Tests: a 500-node stress case timed in pytest and in the browser, axe-core accessibility checks
  (and fixes for what they found on the start page and the app bar), tests for the last two HTTP
  routes no test called, and coverage of the Saga keeper and the local worker processes.

# Changelog

Chembook3D has no released version yet. Version tags (`v*`) start the release build
(`.github/workflows/release.yml`), which keeps a wheel and a source archive as workflow
artifacts and publishes nothing.

## Unreleased

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

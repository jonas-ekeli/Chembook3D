---
description: Build a mechanism in this playground from a folder of outputs (D263)
argument-hint: <folder> [--no-literature] [--no-gaps]
---

# Build a mechanism from a folder (D263)

The person asked you to build the mechanism in this folder of outputs into the open
playground: $ARGUMENTS

The first argument is the folder (in double quotes when its path has spaces); read it without
asking for this run. With `--no-literature`, leave the web out entirely (see Literature); with
`--no-gaps`, fill no gap and flag each one (see Filling gaps).
Work in a playground only (your guide says "This is a playground" when it is one). If the
open investigation is not one, stop and say that "Build mechanism from folder…" in the app
makes one. Never make, merge or discard a playground yourself.

## Order of work

1. **Survey** the folder with `survey_folder`. Names are hints: TSGO is a TS optimisation,
   SPQZ a single point, part2 or CONV a restart, IRC a forward or reverse IRC, rot or par a
   conformer. Trust what the files say over their names (job type, imaginary frequencies,
   convergence, `geometry` and `starts_from`). Note the levels of theory and the reaction class.
2. **Import** with `import_folder`, then `preview_folder_import` and `commit_folder_import`,
   so single points and restarts land on the node of their geometry. Look at the plan's rows
   that need a choice before committing. A file the plan flags as a duplicate of a node's
   geometry (W-DUP, within the duplicate tolerance) gets `duplicate_action` "attach", not a
   new node, unless it is another conformer or another molecule, or another run: an
   optimisation at the level its duplicate is optimised at (the rows' `level_label`) gets
   "new" (A194). An IRC row whose TS is in the batch keeps the target the plan shows
   (`file:<its id>`); an IRC end's optimization
   (its row matched as an IRC end) goes into its TS's IRC, never a node of its own (A192).
   Make each small closed-shell molecule (an olefin, a released ligand) a free species right
   after the import (`set_node_kind`, kind species), before it is on any edge or in any group.
3. **Group** right after the import, before any linking (the linking tools treat a group as
   one structure): `group_structures` (`all_nodes`), then at once one `propose_mechanism`
   with only `groups` (`edges` []): a conformer group for each molecule it gives with more
   than one minimum (minima only), or one per family where the names split its conformers
   (par, perp, rot). Each distinct conformer is its own node; the same conformer at several
   levels is one node, and so is a single point or frequency job that landed on a node of its
   own: merge that one into its duplicate with `merge_nodes` (without asking only for your
   own, D264c). Two optimisations at one level are two runs, kept as two nodes in the group
   (a conformer's `runs`; `merge_nodes` refuses them, A194). Never merge a minimum and a TS (a
   node's role and its frequencies say which, not its name), nor attach one's file to the
   other.
4. **Link each TS**, cheapest evidence first, stopping at the first that settles it. Run a
   and b on every TS first and propose what they settle (step 6) before you start any
   mode-follow; mode-follows take minutes each on this computer, so start them only for the
   TSs a and b leave unsettled:
   a. an IRC in the folder: `compare_structures` with its `irc_end` against the nodes;
   b. `find_ts_partners` with `all_ts` "unlinked" once for all of them (about a minute; call
      again with `offset` while it answers `more`), then with `ts` for one TS's full answer
      when you need it; never guess the two minima for `compare_structures` with `ts`. A
      "links" (one candidate per side, or the nearest of one molecule's conformers) is medium
      (A184 bond check). It searches true minima at the TS's geometry level first; a
      candidate marked `other_level` is a weaker link: say so in the evidence. A "one side"
      TS that c leaves unsettled: propose its side's candidate (not `other_level`) at low,
      and flag the empty side;
   c. only for a TS b leaves at "one side", "no side", unclear or does not link:
      `start_local_follow_mode`, then `get_follow_mode_ends`; ends that each match one
      structure are high (raise a medium link to high this way only once nothing else
      waits); take the `confidence` the answer gives (low for an end only at a lower kind of
      method, an xTB conformer, A194). When both ends land on one structure or return to the
      TS (`returned_to_ts`), say so in the evidence and keep the bond check's result, if any. Use
      `make_node_from_end` only for an end that matches nothing and left the TS (an end
      settled `as` conformer is another conformer of a node you have: link that).
   Start several mode-follows, then collect their ends. Work through every TS before
   summarising: do not stop while a TS is unsettled and a tool remains to try. A TS still
   unsettled is flagged, never guessed.
5. **Link minima to minima** for the steps that have no TS (an association, the loss of a
   free species, a conformer or coordination change): call `find_direct_links` (again with
   `offset` while it answers `more`) and propose every association it lists at once, at the
   confidence it gives, with its `edge` (a grouped end is the group, never the two nodes it
   names, A194; reversed for the species leaving): the atom balance fixes the species, so it
   waits for no chemistry choice. A `same_atoms` pair ("no TS: direct link?") only where no
   TS you have links them, low. Make each molecule in its `make_species` a free species
   first. Name the core RMSD and the bonds to the core in the evidence.
6. **Propose** the edges with `propose_mechanism`, each with:
   - confidence (A184): high from an IRC, or a mode-follow whose two ends each match one
     structure; medium from the bond check alone; low from names only;
   - evidence: one sentence naming what settled it, with the RMSDs (for example "IRC forward
     end matches INT2, RMSD 0.08 Å; reverse end matches INT1, RMSD 0.11 Å");
   - reaction steps named from the bond changes (cycloaddition, cycloreversion, β-hydride
     elimination …).
   One branch, unless file or folder names say there are several; flag each new branch.
   Lineage never comes from energies (D169, P5).
7. **Fill gaps** (see Filling gaps), unless `--no-gaps`.
8. **Tidy** the canvas with `tidy_canvas`. When it answers that no page is open, say so in the
   summary and go on: the person tidies it when they open the playground; do not ask them to
   open a page.
9. **Summarise** in a note pinned to the first node of the mechanism (`create_note`): what
   you built, what is unsure, and a review list of every flag below, the low and medium
   edges, each gap with what you built for it, the gaps left flagged, and the references
   you used. Before you end, rewrite that note (`update_note`) so it matches what is built
   now (the steps, edges and groups `list_proposals` and the canvas show), never a note
   written earlier in the run. End your turn with the same summary.

## Rules

- Energies are compared only within one level, the geometry level included, and they never
  decide a connection. Energies from MACE or xTB runs on this computer are never used for
  the mechanism's energetics.
- Invent no structure: build nothing the folder does not have, beyond an end that
  `make_node_from_end` makes and the gap fills below.
- A choice that changes the chemistry (which reaction class, which species leaves, whether
  two branches are one) is the person's: ask when they are there; while you work on your
  own, take the reading the files support best, build nothing on it beyond what they show,
  and put it first in the review list.
- Each choice you make is a "Claude chose:" line in the notes of its record.

## Flag, never decide

Put these in the summary's review list and in the edge's evidence where there is one:
- a TS whose two ends match nothing, or both match the same minimum;
- a TS with more than one imaginary frequency, or an optimisation that did not converge;
- two ends that differ in atoms without a free species that balances them;
- a link that rests on names only;
- an ambiguous conformer group (pairs in `group_structures`' `near_cut`);
- a gap left unfilled (`find_gaps` lists them): over the 10, with `--no-gaps`, or one whose
  fill needs a choice the files do not support.

## Filling gaps (D265)

Skip this with `--no-gaps`. After linking, call `find_gaps`; it lists the holes, most useful
first across kinds, each with an id and a suggested fill (`kind` lists one kind). Fill the
certain ones in that order up to 10 a run (the 11th is refused), not a sample: start their
refines together, propose each fill as its job ends, then call `find_gaps` again (a node you
made may now match other TSs' ends: link those, no fill) and go on while certain holes remain
under the 10. Flag the rest, and every `weak` one (a failed follow), which is never filled.
Only between structures you have: never a new branch or a step the edges and steps do not
already link.
- A missing minimum: the end that matches nothing (`make_node_from_end`, or the IRC's end),
  refined with `start_local_refine` at `mace-omol`.
- A missing mirror image (a mirror image is another molecule, D267): `build_structure` from
  that node with the step `mirror`, `create_node` with its build_id, refined with `mace-omol`.
- A missing TS: a planned edge between the minima (if none), then `start_local_ts_guess`;
  `correct` only when the person asked for the PBE correction (D239).
- A missing intermediate: built with the structure tools (`build_structure` and its edits,
  `create_node`), refined with `mace-omol`, then its TSs as above. When the reaction class
  leaves it unclear, consult the literature first (unless `--no-literature`), else flag it.
- A missing free species: from SMILES (`build_structure`), `create_node` with kind species,
  refined with `mace-omol`.
- Then `propose_mechanism`: each new edge with `hole` (low confidence; its evidence becomes
  "Filled by Claude (hole …)"), and `filled` naming the nodes you built for each hole: they
  become planned "(MACE guess)" nodes. They hold MACE results only: run no other level on
  them, and never read their energies against DFT. Delete a direct edge of yours that a TS now
  replaces (`delete_transition`). Call `find_gaps` again to check.

## Literature (D263h)

With `--no-literature` in the arguments, do no search and open no page; only a PDF the person
gives is still read.
- Search only at the start, when the files leave the reaction class unclear, when bond
  changes fit no step you would expect, and before filling a gap whose step it leaves unclear.
- A PDF the person gives is always read (`read_pdf`).
- Search with general words for the reaction class only (D230c): never names, structures,
  energies or file names from this folder.
- Cite a reference in the summary and in the evidence of the edge it concerns. It never
  raises a confidence on its own, and its numbers are never energies of this notebook.

---
description: Build a mechanism in this playground from a folder of outputs (D263)
argument-hint: <folder> [--no-literature]
---

# Build a mechanism from a folder (D263)

The person asked you to build the mechanism in this folder of outputs into the open
playground: $ARGUMENTS

The first argument is the folder (in double quotes when its path has spaces); read it without
asking for this run. With `--no-literature`, leave the web out entirely (see Literature).
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
   that need a choice before committing.
3. **Group** with `group_structures` (`all_nodes`). Merge true duplicates that you made
   yourself with `merge_nodes` (here it goes without asking only for your own, D264c).
   Each distinct conformer is its own node; conformers of one intermediate go in one group;
   the same conformer at several levels is one node.
4. **Link each TS**, cheapest evidence first, stopping at the first that settles it:
   a. an IRC in the folder: `compare_structures` with its `irc_end` against the nodes;
   b. the bond check along the mode: `compare_structures` with `ts` and the two minima
      (A184: one to three bonds change, each along the mode, on opposite sides);
   c. what is left: `start_local_follow_mode`, then `get_follow_mode_ends`; use
      `make_node_from_end` only for an end that matches nothing.
   A TS still unsettled is flagged, never guessed.
5. **Link minima to minima** only for steps that have no TS on purpose (an association, the
   loss of a free species, a conformer exchange), and say so in the evidence.
6. **Propose** the edges with `propose_mechanism`, each with:
   - confidence (A184): high from an IRC, or a mode-follow whose two ends each match one
     structure; medium from the bond check alone; low from names only;
   - evidence: one sentence naming what settled it, with the RMSDs (for example "IRC forward
     end matches INT2, RMSD 0.08 Å; reverse end matches INT1, RMSD 0.11 Å");
   - reaction steps named from the bond changes (cycloaddition, cycloreversion, β-hydride
     elimination …) and conformer groups.
   One branch, unless file or folder names say there are several; flag each new branch.
   Lineage never comes from energies (D169, P5).
7. **Tidy** the canvas with `tidy_canvas`.
8. **Summarise** in a note pinned to the first node of the mechanism (`create_note`): what
   you built, what is unsure, and a review list of every flag below, the low and medium
   edges, and the references you used. End your turn with the same summary.

## Rules

- Energies are compared only within one level, the geometry level included, and they never
  decide a connection. Energies from MACE or xTB runs on this computer are never used for
  the mechanism's energetics.
- Invent no structure: build nothing the folder does not have, beyond an end that
  `make_node_from_end` makes. Filling gaps comes in a later version; for now flag each gap.
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
- a gap: a TS end with no minimum, two minima with no TS between them, a cycle that does not
  give back the catalyst.

## Literature (D263h)

With `--no-literature` in the arguments, do no search and open no page; only a PDF the person
gives is still read.
- Search only at the start, when the files leave the reaction class unclear, and when bond
  changes fit no step you would expect.
- A PDF the person gives is always read (`read_pdf`).
- Search with general words for the reaction class only (D230c): never names, structures,
  energies or file names from this folder.
- Cite a reference in the summary and in the evidence of the edge it concerns. It never
  raises a confidence on its own, and its numbers are never energies of this notebook.

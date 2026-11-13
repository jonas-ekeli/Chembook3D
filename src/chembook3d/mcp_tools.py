"""The tools `chembook3d mcp` offers Claude (D91, FR-MCP), each one request to the running
app's API. Their inputs come from the API's own schemas (the app's OpenAPI description, built
in-process), so a tool always takes what its route takes.

Tools come in three kinds: "read" (changes nothing), "write" (changes the investigation, like a
click in the app) and "confirm" (deletes something: the app asks the user first, D91). A write
tool whose go-ahead the user gives in the app itself (`allowed_in_app`, D126), or that changes
only what the page shows (D226), needs no second question in the Claude panel.
Not offered: opening, closing, creating or cloning investigations, sync, opening files with
other programs and app settings. Calculation jobs for cloud sessions (D93) commit and push only
their own job folder. Files on a logged-in SSH server are read in the terminal's current
directory and below (D124), and new files written there once the user allows it in the app
(D126); logging in, deleting and running are not offered.
Structures are built and changed with the Edit structure window's tools as steps (D144), and saved
with set_coordinates.
"""

from dataclasses import dataclass, field
from functools import cache
from typing import Any

from chembook3d.api.live import ACTIONS


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # read, write or confirm
    method: str
    path: str  # the route, as in the app's OpenAPI description
    description: str
    exclude: tuple[str, ...] = ()  # body fields the tool does not offer
    help: dict[str, str] = field(default_factory=dict)  # descriptions of its inputs
    # D126: the user's go-ahead is a switch in the app's page; D226: or only the view changes
    allowed_in_app: bool = False
    defaults: dict[str, Any] = field(default_factory=dict)  # inputs the tool fills in when left out
    asked: bool = False  # D225: a read tool the Claude panel still asks about (list_folder)


ID_HELP = "Ids come from list or get tools (or get_selection); they are opaque strings."

# Descriptions of inputs that recur across tools; a tool's own `help` comes first.
FIELD_HELP = {
    "node_id": "Id of a node or free species.",
    "build_id": (
        "A build_structure answer's build_id: its coordinates are used, so no xyz is needed (D148)."
    ),
    "group_id": "Id of a group node.",
    "sub_investigation_id": "Id of a sub-investigation (a box with a canvas of its own, D193).",
    "step_id": "Id of a reaction step.",
    "branch_id": "Id of a branch.",
    "transition_id": "Id of a transition (edge).",
    "species_id": "Id of a free species (a node with kind 'species').",
    "calculation_id": "Id of a calculation.",
    "note_id": "Id of a note pinned to a node.",
    "selectivity_id": "Id of a saved selectivity.",
    "turnover_id": "Id of a saved turnover.",
    "equilibrium_id": "Id of a saved conformer equilibrium.",
    "profile_id": "Id of a steric profile.",
    "energy_profile_id": "Id of a saved energy profile (list_energy_profiles).",
    "set_id": "Id of an alignment set.",
    "source_id": "Id of the edge's start: a node or a group.",
    "target_id": "Id of the edge's end: a node or a group.",
    "label": "Display name.",
    "name": "Display name.",
    "notes": "Plain-text notes.",
    "role": "minimum, transition_state or unspecified.",
    "status": "planned, running_externally, done, failed, rejected or superseded.",
    "kind": "'node' (on the canvas) or 'species' (a free species kept off the canvas).",
    "charge": "Total charge.",
    "multiplicity": "Spin multiplicity (2S+1).",
    "tags": "Free-form tags; the whole list is replaced.",
    "pos_x": "Canvas x position in px.",
    "pos_y": "Canvas y position in px.",
    "colour": "A CSS colour, e.g. '#1f77b4'.",
    "parent_ids": "Ids of the parent branches (lineage); set only as the user says.",
    "level": "Composite level key 'levelId~geometryLevelId' from get_energy_options.",
    "type": "Energy type: E, H, G or G_qh.",
    "energy_type": "Energy type: E, H, G or G_qh.",
    "reference": "Id of the node or group the values are relative to (optional).",
    "reference_id": "Id of the node or group the values are relative to (optional).",
    "source_side": "Side of the start box the arrow leaves from: top, right, bottom or left.",
    "target_side": "Side of the end box the arrow arrives at: top, right, bottom or left.",
    "direction": "'joins' or 'leaves'.",
    "count": "How many of the species join or leave.",
    "temperature": "Kelvin; null uses the investigation's G_qh temperature setting.",
    "token": "The import token returned by import_file.",
    "job_id": "Id of a calculation job (its folder name in jobs/), from create_cloud_job or "
    "list_cloud_jobs.",
    "xyz": "XYZ text: atom count line, comment line, then 'Element x y z' lines in Å.",
    "ids": "All ids in the new order.",
    "node_ids": "Node ids.",
    "member_ids": "Ids of the nodes that become the group's members.",
}


# The import options preview_import and commit_import share (D222: the IRC and scan path ones).
IMPORT_HELP = {
    "duplicate_action": "'attach' to the possible duplicate, or 'new'.",
    "step": "Index of the job step to import when the file holds several.",
    "target_node_id": "The node the file is imported onto; left out, a new node.",
    "irc_part": "Import an IRC output onto its transition state node (target_node_id), as the "
    "IRC window's Import IRC… does (D131): 'path' keeps the file's points as the node's IRC "
    "path; 'end' imports an optimization or frequency job on an end point, with "
    "irc_direction. Left out, the file is imported as an ordinary calculation.",
    "irc_direction": "With irc_part 'end': 'forward' or 'reverse', the side of the path the "
    "end point belongs to.",
    "path_edge_id": "A scan imported as a new node: the transition (edge) the scan was run "
    "for, so its chip shows on that edge (D121, D137).",
    "on_edge_only": "With path_edge_id: true shows the scan only as the chip on its edge, with "
    "no card on the canvas.",
    "canvas_id": "A new node's canvas: a sub-investigation's id, or left out for the main one.",
    "conformer_count": "CREST: how many of the lowest conformers to import.",
    "conformers": "CREST: the conformers to import, by their number in the file from 1.",
}


FIELD_HELP_LEVEL = "Composite level key 'levelId~geometryLevelId' from get_energy_options."

T = ToolSpec

TOOLS: list[ToolSpec] = [
    # ---------- reading ----------
    T(
        "get_selection",
        "read",
        "GET",
        "/api/selection",
        "What the user has selected in the app (nodes, groups, edges, a branch) with names, and "
        "the energy level, type and reference the app shows. Use it whenever the user says "
        "'this', 'these' or 'the selected'.",
    ),
    T(
        "get_investigation",
        "read",
        "GET",
        "/api/investigation",
        "The investigation open in the app (name and folder), or null when none is open.",
    ),
    T(
        "get_overview",
        "read",
        "GET",
        "/api/overview",
        "The resume overview: branches with counts by status, open items (planned, failed or "
        "unfinished work, warnings), recent changes and the reaction steps' notes. A good first "
        "read.",
        help={"since": "Only recent changes from this day on (YYYY-MM-DD)."},
    ),
    T(
        "get_canvas",
        "read",
        "GET",
        "/api/canvas",
        "Everything on the canvas in one answer: nodes (with xyz), free species, steps, branches, "
        "transitions with their free species, groups, pinned notes and sub-investigations. Each "
        "node and group has the canvas_id of the sub-investigation it is drawn in (none: the "
        "main canvas). Can be large; prefer the list and get tools for one kind of record.",
    ),
    T(
        "list_nodes",
        "read",
        "GET",
        "/api/nodes",
        "All nodes and free species: id, label, kind, role, status, charge, multiplicity, "
        "formula, atom count, calculation count, tags, warning codes, step, branch, group. "
        "Coordinates only with include_xyz (large); get_node gives one node's in full.",
        help={
            "include_xyz": "Also each node's XYZ text.",
            "brief": "Leave out notes, canvas places, times and warning messages (D149).",
        },
        defaults={"include_xyz": False, "brief": True},
    ),
    T(
        "get_node",
        "read",
        "GET",
        "/api/nodes/{node_id}",
        "One node: label, kind, role, status, charge, multiplicity, step, branch, group, "
        "formula, xyz, warnings, notes, the node it was derived from.",
    ),
    T(
        "get_node_xyz",
        "read",
        "GET",
        "/api/nodes/{node_id}/xyz",
        "The node's coordinates as an XYZ file text.",
    ),
    T(
        "list_calculations",
        "read",
        "GET",
        "/api/nodes/{node_id}/calculations",
        "The calculations imported on a node: type, program, level of theory, termination, "
        "energies (hartree), thermochemistry, frequencies, the recomputed G_qh correction, "
        "warnings and source file.",
    ),
    T(
        "get_calculation",
        "read",
        "GET",
        "/api/calculations/{calculation_id}",
        "One calculation in full.",
    ),
    T(
        "get_vibrational_modes",
        "read",
        "GET",
        "/api/calculations/{calculation_id}/modes",
        "A frequency calculation's normal modes: frequencies (imaginary first in `order`) and "
        "displacements per atom. Large for big molecules.",
    ),
    T(
        "get_calculation_steps",
        "read",
        "GET",
        "/api/calculations/{calculation_id}/steps",
        "Every structure of an optimization or scan (D101), read again from its output file, "
        "as the step movie shows them: per frame its energy (hartree), scan point, whether "
        "the optimizer converged on it and the optimizer's criteria. Use it to see how an "
        "optimization went or where a scan's top is. A scan path (D112) also gives `trim`: "
        "the points taken out, the top the node takes, cuts and suggested spikes (D117). "
        "Frames are numbered from 0 in the list's order; get one frame's coordinates with "
        "get_calculation_step.",
        help={
            "geometry": "Also give every frame's coordinates (large; left out unless asked). "
            "For one structure use get_calculation_step."
        },
        defaults={"geometry": False},
    ),
    T(
        "get_calculation_step",
        "read",
        "GET",
        "/api/calculations/{calculation_id}/steps/{frame}",
        "One structure of a calculation's steps as XYZ text, with its energy and scan point, "
        "for check_structure, build_structure or a new node.",
        help={"frame": "The frame's number in get_calculation_steps, from 0; -1 is the last."},
    ),
    T(
        "use_calculation_step",
        "write",
        "POST",
        "/api/calculations/{calculation_id}/steps/{frame}/use",
        '"Use this structure" of the step movie (D112): the structure `frame` becomes a '
        "node's geometry. On a scan path node (get_calculation_steps says in_place) the node "
        "and its scan take that point's geometry and energy in place; on any other node a "
        "new derived node is made beside it, with no calculations (`derived` true), since a "
        "node with calculations keeps its geometry (ID-4, ID-5). Say which frame and why "
        "before using one that is not the top of a scan.",
        help={"frame": "The frame's number in get_calculation_steps, from 0."},
    ),
    T(
        "preview_scan_trim",
        "read",
        "POST",
        "/api/calculations/{calculation_id}/steps/trim-preview",
        "A scan path as it would be with points taken out or put back (D117), changing "
        "nothing: the points removed, the frame that becomes the top, the barrier over the "
        "first kept point, the gaps a removal leaves with the structural jump across each, "
        "and whether the first or last point would be gone.",
        help={
            "remove": "Scan points to take out, numbered from 1 as `point` in the frames.",
            "restore": "Scan points to put back.",
        },
    ),
    T(
        "trim_scan",
        "write",
        "POST",
        "/api/calculations/{calculation_id}/steps/trim",
        "Take points out of a scan path, or put them back (D117): for points that are not on "
        "the path, such as a spike where the structure jumped. The file is untouched and a "
        "point can always be restored. A node showing the automatic top moves with it "
        "(`moved`). Run preview_scan_trim first and tell the user which points and why.",
        help={
            "remove": "Scan points to take out, numbered from 1 as `point` in the frames.",
            "restore": "Scan points to put back.",
            "geometry": "Also give every frame's coordinates in the answer (large).",
        },
        defaults={"geometry": False},
    ),
    T("list_levels", "read", "GET", "/api/levels", "The levels of theory in the investigation."),
    T(
        "list_custom_names",
        "read",
        "GET",
        "/api/custom-names",
        "The names given to custom basis sets and dispersions.",
    ),
    T(
        "list_custom_bases",
        "read",
        "GET",
        "/api/custom-bases",
        "The saved custom basis sets: name, elements and how many calculations use each.",
    ),
    T(
        "get_custom_basis",
        "read",
        "GET",
        "/api/custom-bases/{basis_id}",
        "One saved custom basis set in full: per element the contraction scheme, every shell "
        "with its exponents and coefficients, the ECP, the Gaussian input text, and the "
        "calculations that use it.",
    ),
    T(
        "list_steps",
        "read",
        "GET",
        "/api/steps",
        "The reaction steps (stages of the mechanism) in order.",
    ),
    T(
        "list_branches",
        "read",
        "GET",
        "/api/branches",
        "The branches with their parents, children, lineage paths and node counts.",
    ),
    T("get_branch", "read", "GET", "/api/branches/{branch_id}", "One branch."),
    T(
        "get_branch_pathway",
        "read",
        "GET",
        "/api/branches/{branch_id}/pathway",
        "The branch's pathway: the node and group ids along it, the choices where it forks, "
        "and whether it closes a catalytic cycle.",
    ),
    T(
        "follow_pathway",
        "read",
        "POST",
        "/api/pathways/extend",
        "Follow outgoing transitions from the last id of `path` while exactly one leads on; "
        "stops at an end, at a fork (listing the choices) or when it returns to a visited node "
        "(a closed cycle). Use it to build the `paths` of get_energy_profile. Never pick a "
        "choice by energy: ask the user.",
        help={
            "path": "Node and group ids so far, in order (at least one).",
            "branch_id": "Only follow edges within this branch's lineage (optional).",
            "follow": "False lists every way on from the last id without walking (default true).",
        },
    ),
    T(
        "list_transitions",
        "read",
        "GET",
        "/api/transitions",
        "All transitions (edges) with their status, sides, the free species that join or leave "
        "on them and balance warnings. `direct` means no TS at either end ('no TS'). "
        "`scan_paths` lists the scan path nodes run on each edge (D121) with `top`, the path's "
        "highest point above its first kept point in hartree at the path's own xTB level: it is "
        "not the edge's barrier and never compares with DFT energies.",
    ),
    T(
        "list_groups",
        "read",
        "GET",
        "/api/groups",
        "The group nodes: members in order, representative, branches in and out, layout.",
    ),
    T("list_notes", "read", "GET", "/api/notes", "All notes pinned to node cards (HTML text)."),
    T(
        "get_history",
        "read",
        "GET",
        "/api/history",
        "The change history, newest first; for one record with `record_id` (a node's includes "
        "its calculations). `source` is 'claude' for your changes.",
        help={"record_id": "Only this record's history.", "limit": "At most this many entries."},
    ),
    T(
        "get_energy_options",
        "read",
        "GET",
        "/api/energies/options",
        "The composite levels energies can be shown at (key and label), the energy types each "
        "has, and the G_qh temperature and cutoff.",
    ),
    T(
        "get_energy_view",
        "read",
        "GET",
        "/api/energies/view",
        "Every node's and group's energy at one composite level and type (hartree, absolute), "
        "ΔX on every edge, and with a reference each node's ΔX from it balanced by the free "
        "species along the route. Missing values come with the reason.",
    ),
    T(
        "get_energy_profile",
        "read",
        "POST",
        "/api/energies/profile",
        "Energy profiles of one or more pathways at one composite level and type, relative to "
        "the reference (the first point when none), with the free species balance.",
        help={
            "paths": "Pathways, each a list of node and group ids in order (see follow_pathway).",
            "unit": "Ignored here; values are hartree.",
        },
    ),
    T(
        "get_energy_table",
        "read",
        "POST",
        "/api/energies/table",
        "The energy table of one or more pathways, formatted exactly as the app shows it.",
        help={
            "paths": "Pathways, each a list of node and group ids in order (see follow_pathway).",
            "unit": "kcal/mol, kJ/mol, eV or hartree; the app's setting when left out.",
        },
    ),
    T(
        "list_selectivities",
        "read",
        "GET",
        "/api/selectivities",
        "The saved selectivities (outcomes and their TS nodes or groups).",
    ),
    T(
        "get_selectivity_result",
        "read",
        "GET",
        "/api/selectivities/{selectivity_id}/result",
        "A selectivity's result as the app computes it: ΔΔG‡ per outcome, predicted ratio, ee "
        "or de, each TS's share, or why it is n/a.",
    ),
    T("list_turnovers", "read", "GET", "/api/turnovers", "The saved turnovers (closed pathways)."),
    T(
        "get_turnover_result",
        "read",
        "GET",
        "/api/turnovers/{turnover_id}/result",
        "A turnover's energetic-span result: TOF, δE, TDTS, TDI, ΔG_r and each point's degree "
        "of TOF control, or why it is n/a.",
    ),
    T(
        "list_conformer_equilibria",
        "read",
        "GET",
        "/api/equilibria",
        "The saved conformer equilibria (conformers and the TSs between them, D171).",
    ),
    T(
        "get_conformer_equilibrium_result",
        "read",
        "GET",
        "/api/equilibria/{equilibrium_id}/result",
        "A conformer equilibrium's result: each conformer's ΔG and population, each TS's "
        "barriers, Eyring rates (1/s) and half-lives from both sides, each set of joined "
        "conformers' relaxation times and time to equilibrium (s), the Curtin–Hammett check "
        "against the next step's TSs, or why it is n/a. Energies are in hartree.",
    ),
    T(
        "list_steric_profiles",
        "read",
        "GET",
        "/api/steric-profiles",
        "The steric profiles (%V_bur settings), each node's atoms and the last results, with "
        "why a result is out of date.",
    ),
    T(
        "get_steric_difference",
        "read",
        "POST",
        "/api/steric-profiles/{profile_id}/difference",
        "The steric map of one node minus that of another in the same steric profile (the "
        "height of the ligand surface over the xy-plane, Å), as a summary: the grid points "
        "both cover, the mean difference over them and over each quadrant (NE is x > 0, y > 0), "
        "and where the first is most above (`highest`) and most below (`lowest`) the second. "
        "Both nodes need z-axis and xz-plane atoms in the profile.",
        help={
            "first_id": "Node id: the map the other is taken from.",
            "second_id": "Node id: the map taken away.",
            "grid": "True also returns the whole grid (x positions and z rows, thousands of "
            "values); usually leave false.",
        },
        defaults={"grid": False},
    ),
    T(
        "list_energy_profiles",
        "read",
        "GET",
        "/api/energy-profiles",
        "The saved energy profiles of the drawer (D190): each a named figure of pathways (node "
        "and group ids in order, the branch, `shown`: drawn on the figure, and `shifts`: steps "
        "moved later along the axis by whole columns with every step after them, D194, and "
        "`colour`: the user's choice or null for the branch's, D195) with its own "
        "`reference_id`, which every pathway in it is relative to (D205; null: the first node "
        "shown; not the node cards' reference). `lost` are its pathways through a node deleted "
        "since: never drawn, the user's to remove. Value them with get_energy_profile, passing "
        "that reference_id; make and change them with create_energy_profile and "
        "update_energy_profile.",
    ),
    T(
        "list_alignment_sets",
        "read",
        "GET",
        "/api/alignment-sets",
        "The named alignment sets: per node the 1-based atoms paired in order.",
    ),
    T(
        "overlay_structures",
        "read",
        "POST",
        "/api/overlay",
        "Place structures on a reference (RMSD fit on all atoms, on chosen atoms, or none) and "
        "return the placed coordinates with RMSDs. Changes nothing.",
        help={
            "align": "'all', 'atoms' (fit on `atoms`) or 'none'.",
            "atoms": "Per node id, 1-based atom numbers paired in order with the reference's.",
            "allow_mirror": "Also try the mirror image.",
        },
    ),
    T(
        "match_atoms",
        "read",
        "POST",
        "/api/atom-match",
        "Match the end structure's atoms to the start's numbering (D113): for each start atom "
        "the end's atom, the bonds that form or break, the fitted RMSD, why the match may be "
        "doubtful, and the end renumbered in the start's order. Changes nothing.",
        help={"pairs": "Pairs fixed by hand, [start atom, end atom], 1-based."},
    ),
    T(
        "measure_nodes",
        "read",
        "POST",
        "/api/measurements",
        "Measure distances, angles, dihedrals and angles between two vectors on many nodes at "
        "once (D151): the nodes given and every member of the groups given, one row per node "
        "with the level of theory and convergence of the optimization its geometry came from, "
        "and per column n, mean, SD, min and max (and per level when the rows mix levels). "
        "Atoms are numbered as each node has them, as `reference_node_id` has them (each node "
        "matched to it), or by a SMARTS pattern per measurement. `level` and `converged_only` "
        "pick nodes by their geometry; `keep` keeps rows by value; `skipped` says why a node "
        "was left out. Changes nothing.",
    ),
    T(
        "build_structure",
        "read",
        "POST",
        "/api/builder/run",
        "Build or change a structure with the app's Edit structure tools (D144), as a list of "
        "steps run in order on a node's coordinates, XYZ text, or nothing (a SMILES or a lone "
        "atom first): set a distance, angle or dihedral, turn about a bond, turn any set of "
        "atoms rigidly about an axis or line up two vectors (D150), add or remove a "
        "bond, change an element, delete, add atoms, fragments, built-in molecules or a SMILES, "
        "add hydrogens, freeze atoms and hold coordinates, and Minimize (RDKit UFF that keeps "
        "metals such as Ru in their shape). Atoms are numbered from 1, as in the answer's XYZ. "
        "Changes nothing: check the answer (each step's message and warnings, metals, close "
        "contacts, electrons), then save it by passing its build_id to set_coordinates (a "
        "derived node when the node has calculations) or create_node; never copy coordinates. "
        "To go on from an answer, pass its build_id. A molecule or SMILES binds to a metal's "
        "open site at `value` Å from its donors, where it has the most room; when no site has "
        "room the step fails and says what is in the way (free a site first rather than forcing "
        "it in for Minimize). Use this rather than writing new coordinates yourself.",
    ),
    T(
        "check_structure",
        "read",
        "POST",
        "/api/builder/check",
        "Check a node's structure or XYZ text (D144): formula, atom count, whether the electron "
        "count fits the multiplicity, the distances, angles and dihedrals asked for, each "
        "metal's bonded atoms with distances, and atoms more than two bonds apart closer than "
        "their covalent radii allow (clashes). Each measurement says which atoms setting it "
        "would move (`sides`, and `moves` when `move` is left out). Atoms are numbered from 1. "
        "Changes nothing.",
    ),
    T(
        "get_irc",
        "read",
        "GET",
        "/api/nodes/{node_id}/irc",
        "A TS node's IRC (D131, D132), which is never one of its own calculations: the files of "
        "each direction (forward and reverse are the program's labels) with how each ended, the "
        "points' energies relative to the TS and their mass-weighted distance along the path, "
        "each end's connection check against the nodes on the TS's edges (verdict 'connects' "
        "with the node and the RMSD over the reacting atoms, or 'elsewhere' with the nearest), "
        "nodes an optimized end point is the same structure as, and the end points' E, G and "
        "G_qh relative to the TS. Use it to answer whether a TS connects two intermediates.",
        help={"structures": "Also give every point's coordinates (large; usually not needed)."},
    ),
    T(
        "make_irc_end_node",
        "write",
        "POST",
        "/api/nodes/{node_id}/irc/{direction}/node",
        "Make the optimized end point of one direction of a TS node's IRC a node of its own "
        "(Q38): its optimization's file, and the frequency job's, are imported again as a new "
        "node beside the TS, an ordinary import that undo_import undoes. Needs an end-point "
        "optimization in that direction (get_irc lists them). Use it when get_irc says the "
        "end leads 'elsewhere' or to no node yet; it draws no edge, so connect the new node "
        "yourself where that is what the IRC shows.",
        help={"direction": "'forward' or 'reverse', the program's labels as get_irc gives them."},
    ),
    T(
        "plan_scan_path",
        "read",
        "POST",
        "/api/scan-paths/plan",
        "Check a scan path between two nodes or groups joined by an edge (D114): the atom "
        "match, charge, multiplicity, solvent, and for a TS end the coordinates suggested to "
        "hold. Changes nothing; send_scan_path sends the job, as the app's Scan path dialog "
        "does.",
    ),
    T(
        "send_scan_path",
        "write",
        "POST",
        "/api/scan-paths",
        "Send the scan path between two nodes or groups joined by an edge, as the Send button "
        "of the app's Scan path dialog does (D114, D226): writes the xTB path job and starts "
        "its Claude Code cloud session (start false writes it as a draft only). Call "
        "plan_scan_path first and show the user what it says: warnings, the atom match, the "
        "charge, multiplicity and solvent, and the coordinates held at a TS end. Send only "
        "when they agree. Answers with the job (follow it with get_cloud_job) and, when the "
        "session could not start, `start_error`; the job is then a draft start_cloud_job can "
        "start. Needs the investigation linked to GitHub.",
        help={
            "start_id": "The edge's start: a node, or a group standing for its representative.",
            "end_id": "The edge's end: a node or a group.",
            "held": "Coordinates held at a TS end, each {end: 'start' or 'end', atoms: "
            "[1-based atom numbers]}; the dialog ticks the plan's ts_ends rows marked "
            "suggested.",
            "solvent": "ALPB solvent name from the plan's `solvents`, or null for gas phase; "
            "the dialog starts from the plan's `solvent`.",
        },
    ),
    # ---------- files on a logged-in server such as Saga: read (D124), new ones written (D126)
    T(
        "list_servers",
        "read",
        "GET",
        "/api/server-files",
        "The SSH servers (Saga, ...) the app knows: whether the user is logged in and the "
        "current directory of the server's terminal in the app. Only that directory and the "
        "ones below it can be read; the user moves it with cd. You cannot log in.",
    ),
    T(
        "list_server_files",
        "read",
        "GET",
        "/api/server-files/{server_id}/files",
        "The files in the server terminal's current directory (or a directory below it): "
        "name, kind, size, time.",
        help={
            "server_id": "Id of a logged-in server, from list_servers (e.g. 'saga').",
            "path": "A directory relative to the terminal's current directory (or a full path "
            "under it); empty for the directory itself.",
        },
    ),
    T(
        "read_server_file",
        "read",
        "GET",
        "/api/server-files/{server_id}/read",
        "Read a text file on the server (an output, input, log or job script) with numbered "
        "lines: a page (start, lines), its end (tail) or the lines matching a search with "
        "some lines around each. Outputs can be tens of MB: start with tail or search "
        "(e.g. 'Error termination', 'Normal termination', 'SCF Done', 'Converged?', "
        "'imaginary frequencies'), not with the whole file. At most 2000 lines or 200 kB per "
        "answer.",
        help={
            "server_id": "Id of a logged-in server, from list_servers.",
            "path": "The file, relative to the terminal's current directory or a full path "
            "under it.",
            "start": "First line of the page (1-based); with search, the line to search from.",
            "lines": "Lines in the page (default 400, at most 2000).",
            "tail": "Read the last this many lines instead of a page.",
            "search": "Show the lines containing this text (case-insensitive by default).",
            "regex": "The search is a Python regular expression.",
            "ignore_case": "Ignore upper and lower case in the search (default true).",
            "context": "Lines shown before and after each match (default 2, at most 10).",
            "last": "With search: show the last matches rather than the first (at most 200).",
        },
    ),
    T(
        "summarize_server_file",
        "read",
        "GET",
        "/api/server-files/{server_id}/summary",
        "A Gaussian, ORCA, xTB or CREST output (or an xyz file) on the server read as the "
        "app's Inspect window reads it, without importing it: per job step the route, method, "
        "basis, charge, multiplicity, termination, last SCF energy, whether the optimization "
        "converged, frequencies with the imaginary ones, and every structure's energy and "
        "convergence criteria. The best first look at a job: did it converge, why did it stop, "
        "how many imaginary frequencies. Works on a job still running.",
        help={
            "server_id": "Id of a logged-in server, from list_servers.",
            "path": "The output, relative to the terminal's current directory or a full path "
            "under it.",
        },
    ),
    T(
        "get_server_structure",
        "read",
        "GET",
        "/api/server-files/{server_id}/structure",
        "One structure of an output or xyz file on the server as XYZ text (by default the "
        "last structure of the last step that has any), to look at a geometry or compare it "
        "with a node. Nothing is imported.",
        help={
            "server_id": "Id of a logged-in server, from list_servers.",
            "path": "The file, relative to the terminal's current directory or a full path "
            "under it.",
            "step": "Job step number as summarize_server_file lists it (default: the last with "
            "structures).",
            "frame": "Structure number in the step, 1-based; negative counts from the end "
            "(default -1, the last).",
        },
    ),
    T(
        "import_server_files",
        "write",
        "POST",
        "/api/server-files/{server_id}/import",
        "Copy outputs from the server terminal's current directory (or below) and stage them "
        "for import, as the app's own import from a server does (D225): nothing changes on the "
        "server, and the files keep the server and their path there as their origin. One "
        "path answers with an import plan (then preview_import, commit_import); several "
        "paths of one directory answer with a folder import's plan (then "
        "preview_folder_import, commit_folder_import). Nothing is imported until the commit. "
        "Only when the user asked for these files to be imported.",
        help={
            "server_id": "Id of a logged-in server, from list_servers.",
            "paths": "The outputs, relative to the terminal's current directory or full paths "
            "under it; several must be in one directory.",
            "node_id": "With one path: import onto this node (optional).",
        },
    ),
    T(
        "write_server_file",
        "write",
        "POST",
        "/api/server-files/{server_id}/write",
        "Write a NEW text file (an input, a job script, an xyz, notes) in the server terminal's "
        "current directory or a folder below it (new folders are made), only after the user "
        "turned on 'Let Claude write new files here' in the app's server tab (list_servers "
        "shows `writes`). An existing file is never replaced, edited or deleted: to change one, "
        "write a copy named like name_v2.ext with based_on naming the original, show the user "
        "the difference from the answer, and say the original is unchanged. In a Gaussian copy "
        "point %chk (and %oldchk if needed) at the new name. You cannot delete or run anything: "
        "for deleting, give the user an rm command naming each file to copy themselves.",
        help={
            "server_id": "Id of a logged-in server, from list_servers.",
            "path": "The new file, relative to the terminal's current directory (e.g. "
            "'TS1_opt.gjf' or 'conformers/c01.xyz'); no names starting with a dot.",
            "text": "The whole file as text (at most 1 MB); written with LF line endings.",
            "based_on": "For an edited copy: the original file, which stays unchanged; the "
            "answer then shows the difference.",
        },
        allowed_in_app=True,
    ),
    # ---------- changing ----------
    T(
        "create_node",
        "write",
        "POST",
        "/api/nodes",
        "Create a node (or, with kind 'species', a free species), optionally with coordinates "
        "(xyz, or a build_structure answer's build_id, whose charge and multiplicity it takes "
        "when these are not given). A new node is usually 'planned' until calculations are "
        "imported on it. canvas_id puts it inside a sub-investigation (D193).",
        exclude=("view_rotation",),
        help={"canvas_id": "Id of the sub-investigation whose canvas it goes on; none: main."},
    ),
    T(
        "update_node",
        "write",
        "PATCH",
        "/api/nodes/{node_id}",
        "Change a node's label, role, status, charge, multiplicity, tags, notes, step, branch "
        "or position. Only the fields given change. Coordinates: set_coordinates; kind: "
        "set_node_kind.",
        exclude=("xyz", "kind", "view_rotation", "canvas_id", "build_id"),
    ),
    T(
        "set_coordinates",
        "write",
        "PUT",
        "/api/nodes/{node_id}/geometry",
        "Set a node's coordinates from XYZ text or a build_structure answer's build_id (D148; "
        "give one). A node with no calculations is changed in "
        "place; a node with calculations keeps its geometry and a new derived node is made "
        "with the new coordinates (the answer says which, ID-5). To remove coordinates use "
        "remove_coordinates.",
    ),
    T(
        "link_scan_path",
        "write",
        "PUT",
        "/api/nodes/{node_id}/path-edge",
        "Show a scan path node (one with an xTB path.xyz or xtbscan.log, a joined scan, or a "
        "Gaussian or ORCA scan, D200) on the edge it was run on, as a chip there (D121), or on "
        "none with edge_id null. on_edge_only true takes its "
        "box off the canvas so the chip stands for it; false puts it back. Nothing is rewired.",
        help={
            "edge_id": "Id of the transition, or null to unlink.",
            "on_edge_only": "true: only the chip shows the path; false: its box is on the canvas.",
        },
    ),
    T(
        "set_node_kind",
        "write",
        "PUT",
        "/api/nodes/{node_id}/kind",
        "Turn a node without edges or group into a free species, or a free species on no edge "
        "into a node (D69).",
    ),
    T(
        "split_node",
        "write",
        "POST",
        "/api/nodes/{node_id}/split",
        "Start new child branches at a node (where the mechanism splits into alternatives).",
        help={"branches": "The new branches, each with a name and optional colour."},
    ),
    T("create_step", "write", "POST", "/api/steps", "Create a reaction step (added last)."),
    T(
        "update_step",
        "write",
        "PATCH",
        "/api/steps/{step_id}",
        "Rename a reaction step or change its notes.",
    ),
    T("reorder_steps", "write", "PUT", "/api/steps/order", "Put the reaction steps in order."),
    T("create_branch", "write", "POST", "/api/branches", "Create a branch."),
    T(
        "update_branch",
        "write",
        "PATCH",
        "/api/branches/{branch_id}",
        "Change a branch's name, colour, status, notes or parents.",
    ),
    T(
        "arrange_branch",
        "write",
        "POST",
        "/api/branches/{branch_id}/arrange",
        "Lay out the branch's nodes on the canvas by reaction step.",
    ),
    T(
        "create_transition",
        "write",
        "POST",
        "/api/transitions",
        "Connect two nodes or groups with a transition (edge), from source to target. "
        "Given no status, it starts done when both ends are done, planned otherwise.",
    ),
    T(
        "update_transition",
        "write",
        "PATCH",
        "/api/transitions/{transition_id}",
        "Change a transition's status, notes or the sides its arrow uses.",
        exclude=("source_id", "target_id"),
    ),
    T(
        "set_transition_species",
        "write",
        "PUT",
        "/api/transitions/{transition_id}/species",
        "A free species joins or leaves on this transition (or change its direction or count), "
        "for the mass balance (D69).",
    ),
    T(
        "remove_transition_species",
        "write",
        "DELETE",
        "/api/transitions/{transition_id}/species/{species_id}",
        "Take a free species off a transition (the species itself stays).",
    ),
    T(
        "create_group",
        "write",
        "POST",
        "/api/groups/reconnect",
        "Make a group node of several nodes (e.g. conformers standing for one point). Members "
        "keep their branch and their edges. The representative is the user's choice.",
        help={
            "label": "The group's name.",
            "outgoing": "Optionally a new outgoing branch {name, colour} whose parents are the "
            "members' branches; leave it out unless the user asks for one (D152).",
        },
    ),
    T(
        "add_group_members",
        "write",
        "POST",
        "/api/groups/{group_id}/members",
        "Add nodes to a group.",
    ),
    T(
        "take_out_of_group",
        "write",
        "DELETE",
        "/api/groups/{group_id}/members/{node_id}",
        "Take a member out of a group; it stays as a node with its calculations, edges and "
        "branch (D88). To delete it instead, use delete_node.",
    ),
    T(
        "reorder_group_members",
        "write",
        "PUT",
        "/api/groups/{group_id}/order",
        "Put a group's members in order (every member layout follows it).",
    ),
    T(
        "update_group",
        "write",
        "PATCH",
        "/api/groups/{group_id}",
        "Change a group's label, notes, step, representative (only as the user says), "
        "outgoing branch, position or layout ('grid', 'vertical' or 'horizontal').",
    ),
    T(
        "create_sub_investigation",
        "write",
        "POST",
        "/api/sub-investigations",
        "Tuck nodes, groups and sub-investigations that are on one canvas into a new "
        "sub-investigation (D193): a box with a canvas of its own for a side mechanism, e.g. a "
        "conformer interconversion. Nothing is deleted and edges stay: an edge to a node inside "
        "is drawn to the box. The box has no energy; pathways go through the real nodes inside.",
        help={
            "label": "The sub-investigation's name.",
            "ids": "Ids of the nodes, groups and sub-investigations that move into it.",
            "face_id": "The node inside whose structure the closed box shows (default: the "
            "first node given).",
            "with_groups": "A group member given alone moves its whole group; set it only "
            "after telling the user.",
            "canvas_id": "Only for an empty box: the sub-investigation it goes in (none: main).",
        },
    ),
    T(
        "list_sub_investigations",
        "read",
        "GET",
        "/api/sub-investigations",
        "The sub-investigations (D193): name, notes, the box it sits in (parent_id), its face, "
        "the chain of boxes from the main canvas (path) and how many nodes, TSs, groups and "
        "sub-investigations it holds at any depth.",
    ),
    T(
        "move_to_sub_investigation",
        "write",
        "POST",
        "/api/sub-investigations/move",
        "Move nodes, groups and sub-investigations onto a sub-investigation's canvas, or back to "
        "the main canvas (target_id left out). Edges stay.",
        help={
            "ids": "Ids of the nodes, groups and sub-investigations to move.",
            "target_id": "Id of the sub-investigation; leave out for the main canvas.",
            "with_groups": "A group member given alone moves its whole group; set it only "
            "after telling the user.",
        },
    ),
    T(
        "update_sub_investigation",
        "write",
        "PATCH",
        "/api/sub-investigations/{sub_investigation_id}",
        "Rename a sub-investigation, change its notes, its face (a node inside it) or the box's "
        "position on its canvas.",
    ),
    T(
        "move_on_canvas",
        "write",
        "PUT",
        "/api/positions",
        "Move nodes, groups and sub-investigation boxes on their canvas.",
        help={"positions": "Per node, group or sub-investigation id, its new {x, y} in px."},
    ),
    T(
        "create_note",
        "write",
        "POST",
        "/api/nodes/{node_id}/notes",
        "Pin a note to a node's card. The text is simple HTML: p, br, b, i, u, s, sub, sup, "
        "ul, ol, li, h1-h4, blockquote, pre, code and links.",
        help={
            "corner": "top-left, top-right, bottom-left or bottom-right.",
            "colour": "yellow, blue, green, pink or grey.",
            "body": "The note's text as simple HTML.",
            "placement": "'corner', or floating with a 'line' to the corner, or 'free'.",
        },
    ),
    T(
        "update_note",
        "write",
        "PATCH",
        "/api/notes/{note_id}",
        "Change a pinned note; only the fields given change. Keep any "
        "<img data-note-image> in the body: they are the note's pictures.",
        help={
            "corner": "top-left, top-right, bottom-left or bottom-right.",
            "colour": "yellow, blue, green, pink or grey.",
            "body": "The note's text as simple HTML.",
            "placement": "'corner', or floating with a 'line' to the corner, or 'free'.",
        },
    ),
    T(
        "update_calculation",
        "write",
        "PATCH",
        "/api/calculations/{calculation_id}",
        "Correct a calculation's level of theory (method, basis, dispersion, solvation) or the "
        "level its geometry came from, or change its notes.",
        help={"geometry_level_id": "Id of the level the geometry was optimised at."},
    ),
    T(
        "update_source_file",
        "write",
        "PATCH",
        "/api/source-files/{source_id}",
        "Correct where an imported file came from (its original name, computer and path).",
        help={"source_id": "Id of the source file (from a calculation's source_file)."},
    ),
    T(
        "import_file",
        "write",
        "POST",
        "/api/imports/from-path",
        "Stage an output file (Gaussian, ORCA, xTB, CREST) from a path on this computer that "
        "the user named, or one that fetch_cloud_job returned, and get the import plan: what "
        "was found, which steps attach to which node, possible duplicates, names needed for "
        "custom basis sets. Nothing is imported until commit_import. Import no other paths.",
        help={
            "path": "Full path of the output file.",
            "node_id": "Attach to this node (optional).",
        },
    ),
    T(
        "preview_import",
        "write",
        "POST",
        "/api/imports/{token}/preview",
        "Recompute a staged import's plan with options (target node, step, duplicate handling, "
        "custom basis and dispersion names, label, role, status, conformers).",
        help=IMPORT_HELP,
    ),
    T(
        "commit_import",
        "write",
        "POST",
        "/api/imports/{token}/commit",
        "Import a staged file with the options of the last preview; answers with the node and "
        "calculation ids created.",
        help=IMPORT_HELP,
    ),
    T(
        "cancel_import",
        "write",
        "DELETE",
        "/api/imports/{token}",
        "Drop a staged import (nothing in the investigation changes).",
    ),
    T(
        "list_folder",
        "read",
        "GET",
        "/api/folders",
        "The folders, and with `files` the files, in a folder on this computer: names, paths "
        "and file sizes, never contents (D225). For finding the outputs in a folder the user "
        "named before import_folder, import_file or summarize_file. Look only where the user "
        "pointed you; do not browse the rest of the computer.",
        help={
            "path": "Full path of the folder (left out: the user's home folder).",
            "files": "Also list the files (left out: folders only).",
        },
        asked=True,
    ),
    T(
        "summarize_file",
        "read",
        "GET",
        "/api/local-files/summary",
        "A Gaussian, ORCA, xTB or CREST output (or an xyz file) on this computer read as the "
        "app's Inspect window reads it, without importing it (D225): per job step the route, "
        "method, basis, charge, multiplicity, termination, last SCF energy, whether the "
        "optimization converged, frequencies with the imaginary ones, and every structure's "
        "energy and convergence criteria. For a first look at a file the user named: did it "
        "converge, why did it stop, is it worth importing. Any other kind of file is refused.",
        help={"path": "Full path of the output."},
    ),
    T(
        "get_file_structure",
        "read",
        "GET",
        "/api/local-files/structure",
        "One structure of an output or xyz file on this computer as XYZ text (by default the "
        "last structure of the last step that has any). Nothing is imported.",
        help={
            "path": "Full path of the file.",
            "step": "Job step number as summarize_file lists it (default: the last with "
            "structures).",
            "frame": "Structure number in the step, 1-based; negative counts from the end "
            "(default -1, the last).",
        },
    ),
    T(
        "import_folder",
        "write",
        "POST",
        "/api/batch-imports",
        "Read every output in a folder on this computer that the user named and get the "
        "proposed import of all of them at once (D97), as the app's Import, Folder does: one "
        "row per file with what it is, the node it would go onto or make (files of one "
        "structure are put together), duplicates and what blocks it. Nothing is imported "
        "until commit_folder_import. Show the user the rows that need a choice.",
        help={
            "folder": "Full path of the folder.",
            "recursive": "Also read the folders below it (default true).",
        },
    ),
    T(
        "preview_folder_import",
        "write",
        "POST",
        "/api/batch-imports/{token}/preview",
        "Recompute a folder import's plan with choices; nothing is imported.",
        help={
            "token": "The plan's token, from import_folder or import_server_files.",
            "rows": "Choices per file, keyed by the row's `id` in the plan: {included: false "
            "to leave it out, target: 'auto', 'new', 'node:<node id>' or 'file:<row id>' (the "
            "node that file makes), duplicate_action: 'attach' or 'new' for a possible "
            "duplicate, label: a new node's name, basis: this file's basis set if it names "
            "none}.",
            "basis_names": "Names for custom basis sets the plan asks about, by its key.",
            "dispersion_names": "Names for custom dispersion corrections, by the plan's key.",
            "missing_basis": "The basis set for every file that names none (D104).",
            "origin_device": "The computer the files came from (left out: this one).",
            "suffixes": "Endings of file names that mark a second job of the same structure "
            "(such as _sp), which the user confirmed.",
            "canvas_id": "A sub-investigation's id: new nodes go on its canvas (D193).",
        },
    ),
    T(
        "commit_folder_import",
        "write",
        "POST",
        "/api/batch-imports/{token}/commit",
        "Import a folder import's files with these choices (give the same as in the last "
        "preview); answers with what each file became. undo_import undoes one file's import.",
        help={"token": "The plan's token, from import_folder or import_server_files."},
    ),
    T(
        "cancel_folder_import",
        "write",
        "DELETE",
        "/api/batch-imports/{token}",
        "Drop a folder import's staged files (nothing in the investigation changes).",
    ),
    # ---------- the open page (D226): what only it can do, done by a tab the user has open ----
    T(
        "tidy_canvas",
        "write",
        "POST",
        "/api/page/tidy",
        "Tidy the canvas the app's page shows, as its Tidy menu does (D199, D207, D208): "
        "'columns' puts each reaction step in a column, 'springs' lays the cards out afresh "
        "with arrows of about one length and turns the arrow ends to face each other, "
        "'settle' does that starting from where the cards are. It moves every card on the "
        "open canvas (open another with open_canvas first), so do it when the user asks or "
        "after you added many nodes; undo_tidy puts them back. Answers with how many cards "
        "moved. Needs the app open in a browser tab.",
    ),
    T(
        "undo_tidy",
        "write",
        "POST",
        "/api/page/tidy/undo",
        "Put the cards back where they were before the last Tidy, as the page's Undo tidy "
        "does. Refused once a card was dragged or another canvas opened since.",
    ),
    T(
        "open_groups",
        "write",
        "POST",
        "/api/page/groups",
        "Expand or collapse groups on the app's page (D210): every group on the canvas shown, "
        "or the groups and sub-investigation boxes named in `ids`. Changes only what the page "
        "shows.",
        allowed_in_app=True,
    ),
    T(
        "show_in_app",
        "write",
        "POST",
        "/api/page/show",
        "Select a node, group, sub-investigation, edge or branch on the app's page and bring "
        "it into view, opening its canvas if needed, so the user sees what you are talking "
        "about; with no id, fit the whole canvas in the window. Changes only what the page "
        "shows.",
        allowed_in_app=True,
    ),
    T(
        "open_canvas",
        "write",
        "POST",
        "/api/page/canvas",
        "Open a sub-investigation's canvas on the app's page, or the main canvas when sub_id "
        "is left out (D193). Changes only what the page shows.",
        allowed_in_app=True,
    ),
    T(
        "set_view",
        "write",
        "POST",
        "/api/page/view",
        "Change what the app's page shows, as the user would with its view controls: what the "
        "cards show (mode), the level of theory, energy type and reference the canvas "
        "energies use, energies on arrows, and the 'Profile and table' drawer (open or "
        "closed, its tab, the saved energy profile open in it). Only what you give changes. "
        "Nothing in the investigation changes; get_selection reads the view back.",
        allowed_in_app=True,
        help={"level": FIELD_HELP_LEVEL},
    ),
    T(
        "save_canvas_image",
        "write",
        "POST",
        "/api/page/canvas-image",
        "Have the app's page draw its canvas as an image, as its Export menu does (the whole "
        "open canvas or what the window shows, PNG or SVG, in the view the page shows), and "
        "save it as a new file in the investigation's `exports` folder; answers with the "
        "file's path there. Set the view first (set_view, open_groups, open_canvas). Needs "
        "the app open in a browser tab.",
    ),
    T(
        "save_profile_image",
        "write",
        "POST",
        "/api/page/profile-image",
        "Have the app's page draw an energy profile as its drawer's Export does (the ticked "
        "pathways of a saved profile, in the profile style, at the level and energy type the "
        "page shows; SVG or PNG) and save it as a new file in the investigation's `exports` "
        "folder; answers with the file's path there. Without profile_id it draws the profile "
        "open in the drawer. Needs the app open in a browser tab.",
        help={"profile_id": "Id of a saved energy profile, from list_energy_profiles."},
    ),
    T(
        "save_read_only_copy",
        "write",
        "POST",
        "/api/snapshot/save",
        "Save the read-only copy of the investigation (one self-contained HTML file to share, "
        "D79) as a new file in the investigation's `exports` folder, in the view the "
        "investigation remembers; answers with the file's path there. With profile_id its "
        "drawer shows that saved profile's ticked pathways, otherwise one pathway per "
        "branch. It holds every structure and energy, so tell the user where it is and let "
        "them decide who gets it.",
        help={"profile_id": "Id of a saved energy profile, from list_energy_profiles."},
    ),
    # ---------- calculations in Claude Code cloud sessions (D93) ----------
    T(
        "list_cloud_jobs",
        "read",
        "GET",
        "/api/jobs",
        "The calculation jobs of this investigation (folders in jobs/), as recorded on this "
        "computer: name, state, cloud session link. get_cloud_job tells where each stands.",
    ),
    T(
        "get_cloud_job",
        "read",
        "GET",
        "/api/jobs/{job_id}",
        "Where a calculation job stands: draft, starting, waiting_for_answer (the user answers "
        "Claude Code's question in the Claude panel; `wait` waits for that), launch_failed "
        "(with what Claude Code said or showed and what the user can do; start it again "
        "afterwards), running, "
        "finished (its result.json is on GitHub: status, summary, outputs) or fetched. Checks "
        "GitHub for the result first. With `wait`, keeps checking for up to that many seconds "
        "until the result is there; call again to keep waiting.",
        help={
            "refresh": "Check GitHub for the result (default true).",
            "wait": "Seconds to keep checking until the result is there (0 to 300).",
        },
    ),
    T(
        "create_cloud_job",
        "write",
        "POST",
        "/api/jobs",
        "Write a calculation job for a Claude Code cloud session (xTB or CREST): a folder "
        "jobs/<date>-<name>/ in the investigation with the nodes' coordinates as XYZ inputs "
        "(their charge and multiplicity go into job.md; where a node has none recorded, give "
        "them in the instructions), any other input files, and what to run and return. "
        "Nothing leaves this computer yet: show the user the job (program, "
        "method, settings, inputs) and start it with start_cloud_job only when they agree.",
        help={
            "nodes": "Nodes whose coordinates go in, each {node_id, file (optional name)}.",
            "files": "Other text inputs, each {name, content}; e.g. an xcontrol file.",
        },
    ),
    T(
        "start_cloud_job",
        "write",
        "POST",
        "/api/jobs/{job_id}/start",
        "Start a job in a Claude Code cloud session on the user's account: commits only the job "
        "folder (and the cloud setup files in .claude/), pushes it to the investigation's "
        "GitHub repository and runs `claude --cloud` (in auto mode, so the session runs to the "
        "end). Answers with the session link for the user, who can follow or steer it on "
        "claude.ai. When Claude Code first asks something (status waiting_for_answer, its "
        "screen in `question`, e.g. whether it may trust the investigation folder), the user "
        "answers it in the box the app shows at the top of the Claude panel; then call "
        "get_cloud_job with `wait` for the session link. Needs the investigation linked to "
        "GitHub. Only start a job the user agreed to.",
    ),
    T(
        "message_cloud_job",
        "write",
        "POST",
        "/api/jobs/{job_id}/message",
        "Send a message to a started job's cloud session, as the user would type it on "
        "claude.ai: for example to ask it to push its results again once the user has "
        "installed the Claude GitHub App on the investigation's repository, or to correct "
        "something the user asked for. Say what you will send before sending it.",
    ),
    T(
        "fetch_cloud_job",
        "write",
        "POST",
        "/api/jobs/{job_id}/fetch",
        "Copy a finished job's outputs and result.json from the branch the cloud session "
        "pushed into the job folder on this computer. Answers with the local paths: import "
        "them with import_file and commit_import, or read a structure and set_coordinates on "
        "the node the user names. Report anything listed in changed_outside_job to the user.",
    ),
    # ---------- calculations on this computer (D213) ----------
    T(
        "get_local_tools",
        "read",
        "GET",
        "/api/local/tools",
        "What this computer can run (D222): whether the local tools are installed and current "
        "(with their versions, and an install under way), whether the optional g-xTB is "
        "installed, the cores and memory a job may use, and `methods`: the methods "
        "start_local_refine takes here. With `atoms` (a structure's atom count) each method "
        "carries a time estimate on this computer. Call it before the first local job of a "
        "conversation: when the tools are not installed, tell the user to install them in "
        "Settings, Local calculations (you cannot), or use a cloud job instead.",
        help={
            "atoms": "Atom count of the structure, for the time estimates.",
            "model": "oniom only: atoms in the DFT core plus its link atoms, for its estimate "
            "(check_local_core gives the estimate for a real core).",
        },
    ),
    T(
        "list_local_jobs",
        "read",
        "GET",
        "/api/local/jobs",
        "The calculations run on this computer for this investigation (folders in "
        "local-jobs/), newest first: node, method, status (starting, queued behind another "
        "local job, running, cancelling, converged, not_converged, failed, cancelled, or "
        "stopped when the process ended without a result), the optimizer's progress and "
        "whether the result was imported.",
    ),
    T(
        "get_local_job",
        "read",
        "GET",
        "/api/local/jobs/{job_id}",
        "Where one local job stands, as in list_local_jobs.",
    ),
    T(
        "get_local_job_steps",
        "read",
        "GET",
        "/api/local/jobs/{job_id}/steps",
        "How a local job is going, while it runs or after (D220, what Inspect shows): per "
        "step of the job its type, how it ended, the last energy and the frequencies once "
        "there are any, and every structure written so far with its energy (hartree), scan "
        "point, convergence and the optimizer's criteria, without coordinates. Use it to "
        "see whether an optimization is converging, wandering or stuck before deciding to "
        "wait or cancel; get a structure with get_local_job_step.",
    ),
    T(
        "get_local_job_step",
        "read",
        "GET",
        "/api/local/jobs/{job_id}/steps/{frame}",
        "One structure of a local job so far as XYZ text, with its energy: -1 is the latest. "
        "A running job's structure is not a result: do not save it as a node's geometry "
        "unless the user asks for that.",
        help={
            "frame": "The frame's number in get_local_job_steps, from 0; -1 is the last.",
            "step": "The job's step, as numbered in get_local_job_steps; left out, the "
            "first that has structures.",
        },
    ),
    T(
        "start_local_refine",
        "write",
        "POST",
        "/api/local/jobs",
        "Refine a node's geometry on this computer (D213): a geometry optimization run by the "
        "tools the user installed in Settings (Local calculations), in a process of its own "
        "that goes on when the app is closed. Methods (get_local_tools lists those this "
        "computer offers, with time estimates): gfn2 (GFN2-xTB with tblite, minutes), gxtb "
        "(g-xTB, D216: closer to DFT than GFN2-xTB at a few times its cost, only when the user "
        "installed it), b97-3c and r2scan-3c (the whole molecule with Psi4, hours for a 70-atom "
        "complex), oniom (D214: r2SCAN-3c on a core around the "
        "metal or chosen atoms, GFN2-xTB on the rest, H link atoms on the cut bonds; "
        "several times faster than r2scan-3c; check the core with check_local_core first and "
        "pass it as core). With transition true it searches for a transition state (D215) "
        "from a GFN2-xTB Hessian instead of a minimum, and with reacting atoms says whether the "
        "final imaginary mode stays on them (check_local_core gives the atoms of the node's "
        "own imaginary mode as mode_atoms). A gfn2 or gxtb TS search starts from a Hessian at "
        "its own level and ends with frequencies at that level, imported as a frequency "
        "calculation (D217; frequencies true asks for them after a minimum too). With scan "
        "{coordinates: [{kind, atoms, start, end}], points} it runs a relaxed scan instead "
        "(D218): every coordinate goes from start (left out: its value in the structure now) to "
        "end over the same points (concerted), each "
        "point optimized from the one before, imported as a node of its own; with grid true and "
        "two coordinates each given points it runs every pair of their values (D219, gfn2 and "
        "gxtb only). Charge and "
        "multiplicity default to the node's. Optional constraints (atoms from 0) hold atoms "
        "frozen, or distances, angles and dihedrals at their current value or at a target. "
        "The app imports the result onto the node when it finishes. It uses the computer's "
        "cores for minutes to hours, so only start one the user asked for, and say the "
        "estimate and ask first when it is over an hour.",
    ),
    T(
        "check_local_core",
        "read",
        "POST",
        "/api/local/core",
        "The DFT core of an oniom refine (D214) of a node: suggested from its metal atoms, or "
        "from seed atoms (a reaction's atoms in a metal-free molecule), or checked as given; "
        "with the H link atoms on the bonds it cuts, errors (a cut bond to a metal or a "
        "hydrogen, an electron count that cannot have the multiplicity), warnings and the "
        "time estimate. Atoms are counted from 0. Changes nothing.",
    ),
    T(
        "cancel_local_job",
        "write",
        "POST",
        "/api/local/jobs/{job_id}/cancel",
        "Stop a local job that is waiting or running; nothing is imported from it.",
    ),
    T(
        "import_local_result",
        "write",
        "POST",
        "/api/local/jobs/{job_id}/import",
        "Import a finished local job's result onto the node it was started from. The app does "
        "this by itself while it is open; use it when that failed and the user asks.",
    ),
    T(
        "import_scan_path",
        "write",
        "POST",
        "/api/jobs/{job_id}/import-path",
        "Import a finished scan path job's outputs/path.xyz (D115) as a new node between its "
        "two ends: no edges, the start's step and branch, its scan playing as a movie. The app "
        "does this by itself while it is open; use it when that failed and the user asks, with "
        "again=true only when the user wants a path whose node was removed imported once more.",
    ),
    T(
        "create_selectivity",
        "write",
        "POST",
        "/api/selectivities",
        "Save a selectivity: two or more named outcomes, each realised by TS nodes or groups "
        "the user names.",
        help={
            "outcomes": "Each {name, members: TS node or group ids, experimental: optional "
            "amount}.",
            "conformers": "'boltzmann' (sum over each outcome's TSs) or 'lowest'.",
            "excess": "'ee', 'de' or 'none' (ratio only).",
            "reference_id": "Optional node or group: each TS is then balanced by the free "
            "species along its route from it, as its node card is. null for none.",
        },
    ),
    T(
        "update_selectivity",
        "write",
        "PATCH",
        "/api/selectivities/{selectivity_id}",
        "Change a saved selectivity; only the fields given change.",
        help={
            "outcomes": "Each {name, members: TS node or group ids, experimental: optional "
            "amount}.",
            "conformers": "'boltzmann' or 'lowest'.",
            "excess": "'ee', 'de' or 'none'.",
            "reference_id": "Optional node or group: each TS is then balanced by the free "
            "species along its route from it, as its node card is. null for none.",
        },
    ),
    T(
        "create_turnover",
        "write",
        "POST",
        "/api/turnovers",
        "Save a turnover (energetic-span TOF) of a closed pathway the user chose.",
        help={
            "path": "Node and group ids; the last returns to an earlier one.",
            "compare_id": "Another turnover to compare with (optional).",
        },
    ),
    T(
        "update_turnover",
        "write",
        "PATCH",
        "/api/turnovers/{turnover_id}",
        "Change a saved turnover; only the fields given change.",
        help={"compare_id": "Another turnover to compare with (optional)."},
    ),
    T(
        "create_conformer_equilibrium",
        "write",
        "POST",
        "/api/equilibria",
        "Save a conformer equilibrium (D171): populations of conformers the user chose and the "
        "rates of the TSs between them.",
        help={
            "members": "Each {id: node or group id, count: equivalent structures it stands "
            "for, 1 unless given}. TS nodes are the barriers; they join the two conformers "
            "they have edges to.",
            "start_id": "The conformer the populations start from over time; null for the "
            "slowest start.",
            "next_ids": "TS nodes or groups of the step that follows, for the Curtin–Hammett "
            "check; each is balanced by the free species from the lowest conformer.",
        },
    ),
    T(
        "update_conformer_equilibrium",
        "write",
        "PATCH",
        "/api/equilibria/{equilibrium_id}",
        "Change a saved conformer equilibrium; only the fields given change.",
        help={
            "members": "Each {id: node or group id, count: equivalent structures it stands "
            "for, 1 unless given}. TS nodes are the barriers; they join the two conformers "
            "they have edges to.",
            "start_id": "The conformer the populations start from over time; null for the "
            "slowest start.",
            "next_ids": "TS nodes or groups of the step that follows, for the Curtin–Hammett "
            "check; each is balanced by the free species from the lowest conformer.",
        },
    ),
    T(
        "create_steric_profile",
        "write",
        "POST",
        "/api/steric-profiles",
        "Save a steric profile (%V_bur settings) and, per node, its atoms.",
        help={
            "radii": "'bondi' or 'crc'.",
            "atoms": "Per node id: {centre, z_axis, xz_plane, excluded} as 1-based atom "
            "lists, or {same_as: node id}, or null to take the node out.",
        },
    ),
    T(
        "update_steric_profile",
        "write",
        "PATCH",
        "/api/steric-profiles/{profile_id}",
        "Change a steric profile; only the fields given change.",
        help={
            "radii": "'bondi' or 'crc'.",
            "atoms": "Per node id: {centre, z_axis, xz_plane, excluded} as 1-based atom "
            "lists, or {same_as: node id}, or null to take the node out.",
        },
    ),
    T(
        "compute_sterics",
        "write",
        "POST",
        "/api/steric-profiles/{profile_id}/compute",
        "Compute %V_bur, quadrants and octants for nodes in a steric profile; results are "
        "saved with the profile.",
        help={"maps": "Also return the steric maps (large grids); usually leave false."},
    ),
    T(
        "create_alignment_set",
        "write",
        "POST",
        "/api/alignment-sets",
        "Save a named alignment set: per node id, 1-based atoms paired in order.",
    ),
    T(
        "update_alignment_set",
        "write",
        "PATCH",
        "/api/alignment-sets/{set_id}",
        "Change an alignment set; a node's list set to null takes it out.",
    ),
]

# D227: the saved energy profiles, the thermochemistry
PROFILE_PATHS_HELP = (
    "Each {ids: node and group ids in order, each joined to the next by a transition "
    "(build them with follow_pathway or get_branch_pathway), branch_id: the branch it was "
    "followed in or null, shown: drawn on the figure (default true), shifts: optional [{at: a "
    "step's index, by: whole columns later}], colour: optional '#rrggbb' or null for the "
    "branch's}."
)
PROFILE_REFERENCE_HELP = (
    "A node or group on one of its pathways that all of them are relative to (D205); null: "
    "the first node shown."
)
TOOLS += [
    T(
        "create_energy_profile",
        "write",
        "POST",
        "/api/energy-profiles",
        "Save a new energy profile (D190): a named figure of pathways the energy drawer opens. "
        "Without a name it is the first free 'Profile N'. Which pathways go in it is the "
        "user's choice of what to compare: never pick a fork by energy. Open it for the user "
        "with set_view (profile_id); save its figure with save_profile_image.",
        help={
            "name": "The profile's name; no two profiles share one.",
            "paths": PROFILE_PATHS_HELP,
            "reference_id": PROFILE_REFERENCE_HELP,
        },
    ),
    T(
        "update_energy_profile",
        "write",
        "PATCH",
        "/api/energy-profiles/{energy_profile_id}",
        "Change a saved energy profile; only the fields given change. `paths` replaces all its "
        "pathways, so read them with list_energy_profiles first and give back the ones that "
        "stay (its `lost` pathways stay whatever is given). The open drawer shows the change "
        "at once.",
        help={
            "name": "The profile's name; no two profiles share one.",
            "paths": PROFILE_PATHS_HELP,
            "reference_id": PROFILE_REFERENCE_HELP,
        },
    ),
    T(
        "duplicate_energy_profile",
        "write",
        "POST",
        "/api/energy-profiles/{energy_profile_id}/duplicate",
        "A copy of a saved energy profile, named '<name> (copy)': change the copy to try "
        "something without touching the user's own.",
    ),
    T(
        "set_thermochemistry",
        "write",
        "PUT",
        "/api/thermochemistry",
        "Change the investigation's thermochemistry (D159): the temperature (K) and the "
        "frequency cutoff (cm⁻¹) of the quasi-harmonic G_qh, and the standard state of G and "
        "G_qh. Every G and G_qh in the investigation, and all that is worked out from them, "
        "changes with it; each value changed is one entry in the history. Only the values "
        "given change; get_energy_options shows the current ones. This is a choice of method: "
        "change it only when the user asked for these values.",
        help={
            "qh_temperature": "Kelvin, above zero.",
            "qh_cutoff": "cm⁻¹, zero or more: frequencies below it are raised to it for the "
            "entropy (Truhlar).",
            "standard_state": "'1 atm' or '1 M'.",
        },
    ),
]

# ---------- deleting: the app asks the user (D91) ----------

CONFIRM_DESCRIPTIONS = {
    "delete_node": "Delete a node (or free species) with its calculations, notes and every "
    "edge touching it.",
    "delete_group": "Delete a group with its contents: its members, their calculations and "
    "every edge touching any of them.",
    "dissolve_group": "Dissolve a group: the members stay as nodes, edges to the group go.",
    "delete_sub_investigation": "Delete a sub-investigation with everything inside it: its "
    "nodes with their calculations, its groups, the sub-investigations inside it and every edge "
    "touching any of them. To keep the contents, use dissolve_sub_investigation.",
    "dissolve_sub_investigation": "Dissolve a sub-investigation: what it holds goes back onto "
    "the canvas the box is on, where the box was; nothing is deleted.",
    "delete_step": "Delete a reaction step; its nodes stay with no step.",
    "delete_branch": "Delete a branch; its nodes stay with no branch.",
    "delete_transition": "Delete a transition (edge).",
    "remove_coordinates": "Remove the coordinates of a node with no calculations (D90).",
    "undo_import": "Undo the import of one file (D102): give the id of a history entry of type "
    "'calculation' and action 'create' whose `undo` is 'import'. Its calculations, the nodes it "
    "made and the stored copy go, and what it changed on its node is put back. Refused, with "
    "what depends on it, when later work builds on the import.",
    "delete_note": "Delete a note pinned to a node.",
    "delete_selectivity": "Delete a saved selectivity.",
    "delete_turnover": "Delete a saved turnover.",
    "delete_conformer_equilibrium": "Delete a saved conformer equilibrium.",
    "delete_steric_profile": "Delete a steric profile with its results.",
    "delete_energy_profile": "Delete a saved energy profile (the nodes on its pathways stay). "
    "The user is always asked, also for one you made.",
    "delete_alignment_set": "Delete an alignment set.",
}

CONFIRM_NOTE = (
    " The app asks the user in its window and this tool waits for the answer; nothing changes "
    "unless they confirm. If they refuse, do not ask again: ask what they want instead."
)

for _name, _action in ACTIONS.items():
    TOOLS.append(T(_name, "confirm", _action.method, _action.path, CONFIRM_DESCRIPTIONS[_name]))

BY_NAME = {t.name: t for t in TOOLS}
# The tools that change nothing; a client may allow them without asking (the panel, D92).
READ_ONLY_TOOLS = frozenset(t.name for t in TOOLS if t.kind == "read")
# What the Claude panel allows without asking (D92): the read tools, and those whose go-ahead the
# user gives in the app's page (D126).
PANEL_ALLOWED = frozenset(
    t.name for t in TOOLS if (t.kind == "read" and not t.asked) or t.allowed_in_app
)


# ---------- input schemas ----------


def _inline(schema: Any, components: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """The schema with every $ref to the app's components written out and titles dropped,
    since MCP clients cannot follow references into the app's OpenAPI description."""
    if isinstance(schema, list):
        return [_inline(item, components, seen) for item in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[1]
        if name in seen:
            return {"type": "object"}
        return _inline(components[name], components, seen | {name})
    out = {k: _inline(v, components, seen) for k, v in schema.items() if k != "title"}
    return _not_null(out)


def _not_null(schema: dict[str, Any]) -> dict[str, Any]:
    """An optional field written as anyOf [its type, null] becomes its type alone (D149): some
    clients drop or hide such fields, and a field left out is the same as null here."""
    choices = schema.get("anyOf")
    if not isinstance(choices, list) or len(choices) != 2:
        return schema
    kept = [c for c in choices if c != {"type": "null"}]
    if len(kept) != 1 or not isinstance(kept[0], dict):
        return schema
    rest = {k: v for k, v in schema.items() if k != "anyOf" and not (k == "default" and v is None)}
    return {**kept[0], **rest}


@cache
def openapi() -> dict[str, Any]:
    from chembook3d.app import create_app

    return create_app().openapi()


def _describe(prop: dict[str, Any], name: str, spec: ToolSpec) -> dict[str, Any]:
    text = spec.help.get(name) or FIELD_HELP.get(name)
    if text and "description" not in prop:
        prop = {**prop, "description": text}
    return prop


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    """What the tool takes: the route's path and query parameters and its JSON body's fields,
    side by side."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    if spec.kind == "confirm":
        for name in ACTIONS[spec.name].params:
            properties[name] = _describe({"type": "string"}, name, spec)
            required.append(name)
        if spec.name == "dissolve_group":
            properties["restore_branches"] = {
                "type": "boolean",
                "description": "Members go back to the branches they came from.",
                "default": False,
            }
        properties["reason"] = {
            "type": "string",
            "description": "One sentence for the user on why, shown in the app's dialog.",
        }
        return {"type": "object", "properties": properties, "required": required}

    document = openapi()
    components = document.get("components", {}).get("schemas", {})
    operation = document["paths"][spec.path][spec.method.lower()]
    for parameter in operation.get("parameters", []):
        name = parameter["name"]
        properties[name] = _describe(_inline(parameter["schema"], components), name, spec)
        if name in spec.defaults:
            properties[name]["default"] = spec.defaults[name]
        if parameter.get("required"):
            required.append(name)
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if content is not None:
        body = _inline(content["schema"], components)
        for name, prop in body.get("properties", {}).items():
            if name in spec.exclude:
                continue
            if name in properties:
                raise ValueError(f"{spec.name}: body field '{name}' clashes with a parameter")
            properties[name] = _describe(prop, name, spec)
        required += [n for n in body.get("required", []) if n not in spec.exclude]
    return {"type": "object", "properties": properties, "required": required}


def body_fields(spec: ToolSpec) -> set[str] | None:
    """The fields of the route's JSON body, or None when it takes no body."""
    operation = openapi()["paths"][spec.path][spec.method.lower()]
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if content is None:
        return None
    components = openapi().get("components", {}).get("schemas", {})
    return set(_inline(content["schema"], components).get("properties", {})) - set(spec.exclude)


def description(spec: ToolSpec) -> str:
    text = spec.description
    if spec.kind == "confirm":
        text += CONFIRM_NOTE
    return text

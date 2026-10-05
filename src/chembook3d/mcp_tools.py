"""The tools `chembook3d mcp` offers Claude (D91, FR-MCP), each one request to the running
app's API. Their inputs come from the API's own schemas (the app's OpenAPI description, built
in-process), so a tool always takes what its route takes.

Tools come in three kinds: "read" (changes nothing), "write" (changes the investigation, like a
click in the app) and "confirm" (deletes something: the app asks the user first, D91).
Not offered: opening, closing, creating or cloning investigations, sync, folder browsing,
opening files with other programs, app settings and the read-only export. Calculation jobs for
cloud sessions (D93) commit and push only their own job folder.
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


ID_HELP = "Ids come from list or get tools (or get_selection); they are opaque strings."

# Descriptions of inputs that recur across tools; a tool's own `help` comes first.
FIELD_HELP = {
    "node_id": "Id of a node or free species.",
    "group_id": "Id of a group node.",
    "step_id": "Id of a reaction step.",
    "branch_id": "Id of a branch.",
    "transition_id": "Id of a transition (edge).",
    "species_id": "Id of a free species (a node with kind 'species').",
    "calculation_id": "Id of a calculation.",
    "note_id": "Id of a note pinned to a node.",
    "selectivity_id": "Id of a saved selectivity.",
    "turnover_id": "Id of a saved turnover.",
    "profile_id": "Id of a steric profile.",
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
    "temperature": "Kelvin; null uses the app's G_qh temperature setting.",
    "token": "The import token returned by import_file.",
    "job_id": "Id of a calculation job (its folder name in jobs/), from create_cloud_job or "
    "list_cloud_jobs.",
    "xyz": "XYZ text: atom count line, comment line, then 'Element x y z' lines in Å.",
    "ids": "All ids in the new order.",
    "node_ids": "Node ids.",
    "member_ids": "Ids of the nodes that become the group's members.",
}


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
        "transitions with their free species, groups and pinned notes. Can be large; prefer the "
        "list and get tools for one kind of record.",
    ),
    T("list_nodes", "read", "GET", "/api/nodes", "All nodes and free species, with coordinates."),
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
    T("list_levels", "read", "GET", "/api/levels", "The levels of theory in the investigation."),
    T(
        "list_custom_names",
        "read",
        "GET",
        "/api/custom-names",
        "The names given to custom basis sets and dispersions.",
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
        },
    ),
    T(
        "list_transitions",
        "read",
        "GET",
        "/api/transitions",
        "All transitions (edges) with their status, sides, the free species that join or leave "
        "on them and balance warnings. `direct` means no TS at either end ('no TS').",
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
        "list_steric_profiles",
        "read",
        "GET",
        "/api/steric-profiles",
        "The steric profiles (%V_bur settings), each node's atoms and the last results, with "
        "why a result is out of date.",
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
    # ---------- changing ----------
    T(
        "create_node",
        "write",
        "POST",
        "/api/nodes",
        "Create a node (or, with kind 'species', a free species), optionally with coordinates. "
        "A new node is usually 'planned' until calculations are imported on it.",
        exclude=("view_rotation",),
    ),
    T(
        "update_node",
        "write",
        "PATCH",
        "/api/nodes/{node_id}",
        "Change a node's label, role, status, charge, multiplicity, tags, notes, step, branch "
        "or position. Only the fields given change. Coordinates: set_coordinates; kind: "
        "set_node_kind.",
        exclude=("xyz", "kind", "view_rotation"),
    ),
    T(
        "set_coordinates",
        "write",
        "PUT",
        "/api/nodes/{node_id}/geometry",
        "Set a node's coordinates from XYZ text. A node with no calculations is changed in "
        "place; a node with calculations keeps its geometry and a new derived node is made "
        "with the new coordinates (the answer says which, ID-5). To remove coordinates use "
        "remove_coordinates.",
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
        "Connect two nodes or groups with a transition (edge), from source to target.",
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
        "Make a group node of several nodes (e.g. conformers standing for one point); their "
        "edges are reconnected to the group. The representative is the user's choice.",
        help={
            "label": "The group's name.",
            "outgoing": "Optionally a new outgoing branch {name, colour}.",
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
        "move_on_canvas",
        "write",
        "PUT",
        "/api/positions",
        "Move nodes and groups on the canvas.",
        help={"positions": "Per node or group id, its new {x, y} in px."},
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
        help={
            "duplicate_action": "'attach' to the possible duplicate, or 'new'.",
            "step": "Index of the job step to import when the file holds several.",
        },
    ),
    T(
        "commit_import",
        "write",
        "POST",
        "/api/imports/{token}/commit",
        "Import a staged file with the options of the last preview; answers with the node and "
        "calculation ids created.",
        help={
            "duplicate_action": "'attach' to the possible duplicate, or 'new'.",
            "step": "Index of the job step to import when the file holds several.",
        },
    ),
    T(
        "cancel_import",
        "write",
        "DELETE",
        "/api/imports/{token}",
        "Drop a staged import (nothing in the investigation changes).",
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

# ---------- deleting: the app asks the user (D91) ----------

CONFIRM_DESCRIPTIONS = {
    "delete_node": "Delete a node (or free species) with its calculations, notes and every "
    "edge touching it.",
    "delete_group": "Delete a group with its contents: its members, their calculations and "
    "every edge touching any of them.",
    "dissolve_group": "Dissolve a group: the members stay as nodes, edges to the group go.",
    "delete_step": "Delete a reaction step; its nodes stay with no step.",
    "delete_branch": "Delete a branch; its nodes stay with no branch.",
    "delete_transition": "Delete a transition (edge).",
    "remove_coordinates": "Remove the coordinates of a node with no calculations (D90).",
    "delete_note": "Delete a note pinned to a node.",
    "delete_selectivity": "Delete a saved selectivity.",
    "delete_turnover": "Delete a saved turnover.",
    "delete_steric_profile": "Delete a steric profile with its results.",
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
    return {k: _inline(v, components, seen) for k, v in schema.items() if k != "title"}


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

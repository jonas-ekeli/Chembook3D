# 03 · Workflows and UX

_Chembook3D specification pack · v1.1 · 2026-09-29. Terms as defined in [02-domain-model.md](02-domain-model.md). Requirements referenced as FR-… are in [04-functional-requirements.md](04-functional-requirements.md). Layout per P6 (confirmed); the behaviours listed are the requirement._

## 1. Screen layout (P6)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Investigation ▾  | View: [level ▾] [energy type ▾]  | Mode: compact │ energy │ structure │ Filters ▾ │
├───────────────┬──────────────────────────────────────┬───────────────┤
│ Overview /    │                                      │ Inspector     │
│ outline       │        Infinite canvas               │ (selected     │
│ (branches,    │                                      │  node, edge,  │
│  steps,       │                                      │  group,       │
│  open items)  │                                      │  branch)      │
│               │                                      │  • 3D viewer  │
│               │                                      │  • xyz text   │
│               │                                      │  • calcs      │
│               │                                      │  • notes      │
│               │                                      │  • history    │
├───────────────┴──────────────────────────────────────┴───────────────┤
│ Bottom drawer: energy profile │ energy table                          │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. User journeys

### WF-01 Create and open an investigation
1. User chooses **New investigation**, gives a name and a folder location.
2. The app creates the self-contained folder (FR-INV-01) and opens an empty canvas.
3. **Open investigation** lists recently opened ones and allows choosing a folder.
Result: a folder that can be zipped and shared (D21).

### WF-02 Create a node manually
1. User double-clicks the canvas or chooses **Add node**.
2. The inspector opens with empty fields: label, role, charge, multiplicity, status (default *planned*), step, branch, notes, coordinates. None are mandatory (D37).
3. User pastes xyz text into the coordinates editor. The app parses it, shows parse errors per line, and updates the 3D view (FR-NODE-03).
Result: a node on the canvas at the clicked position.

### WF-03 Plan a node, then import its finished calculation (D24)
1. User creates a planned node with a guess geometry (WF-02).
2. Later, user drags an output file onto the node, or chooses **Import onto node**.
3. Because the node has no calculations, the imported geometry replaces the guess. The history records the old geometry (ID-6).
4. The app suggests setting the status to *done* (or *failed* if termination was abnormal). The user confirms or changes it.

### WF-04 Import a Gaussian, ORCA or xTB output
1. User drops one or more files on the canvas (new nodes) or on a node (onto existing).
2. For each file, an **import preview** shows what was parsed: program, job steps, level of theory, charge and multiplicity, final geometry, energies, thermochemistry with T and P, frequencies and imaginary count, termination, and any warnings. It also asks for origin metadata: device or server name and path (FR-FILE-02; the last used values are pre-filled).
3. Multi-step jobs: the geometry used is the last one from the highest-level step (D25). Failed optimizations: the last geometry, tagged `optimization-incomplete` (D26).
4. If the geometry nearly matches an existing node, the preview shows a **possible duplicate** with a link, and offers *attach to that node*, *create new node* or *cancel* (D47).
5. If imported onto a node that already has calculations and the geometry differs, the app offers *create derived node* instead (ID-7).
   - **Adding a frequency job later (D52):** a frequency calculation can be imported onto an existing node at any time. The file may contain other steps too (e.g. a re-optimization before the freq, or a single point after it). The preview lists every step with its geometry. Steps whose geometry matches the node are attached to it. If a step changed the geometry, the app offers a derived node for that step and its later steps. The preview also shows whether the freq's level matches the node's geometry level, since G uses the freq at the geometry level (EN-4).
6. On confirm, the files are copied into the investigation (D16) and records are created.

### WF-05 Import a CREST conformer ensemble (D34, D46)
1. User drops the ensemble file.
2. The preview lists the conformers by energy and asks for N (default from settings). The lowest N are ticked.
3. User can untick any conformer before import and remove members after import.
4. The result is a group node containing N member nodes. No representative is chosen until the user picks one.

### WF-06 Build steps, branches and transitions
1. **Steps:** user manages an ordered list of reaction steps in the outline, and assigns nodes to steps from the inspector or by multi-select.
2. **Branches:** user creates a branch (name, colour, parent branches), then assigns nodes. A **split** is made by selecting a node and choosing **Split into branches…**, which creates child branches with the selected branch as parent.
3. **Transitions:** user drags from one node's handle to another. The transition gets status *planned* by default and optional notes. Connecting two intermediates directly is allowed; such an edge is a **direct connection** and is marked "no TS" (D53).
4. **Layout helper:** **Arrange branch** lays out the selected branch left to right in step order (D33). Manual positions are otherwise preserved.

### WF-07 Reconnect branches (D13, D14, D18)
1. User selects the final nodes of several branches, or other conformers, and chooses **Reconnect as group**.
2. The app creates a group node with those members, records the incoming branches, and asks whether to create an outgoing branch (its parents are the incoming branches).
3. The members can be sorted by energy at the view's level. The user marks one as **representative**. The app never pre-selects it (EN-10).
4. Transitions from the incoming branches into the group are optional (D14).
5. **Deleting a group (D54):** the user chooses **Dissolve group** (members kept as nodes, P4) or **Delete group with contents** (group and members removed, after a confirmation listing members, calculations and edges).
6. **Adding to a group (D65, A20):** the user selects the group together with one or more nodes and chooses **Add to group**. Loose nodes join as in a reconnection; a node in another group moves.

### WF-08 View energies
1. User picks a **level** and an **energy type** from the view's drop-downs. Only combinations that exist in the investigation are listed (D32).
2. Edges show ΔX when the energy mode is on (D33). Missing values show "n/a" (EN-3). Direct connections carry a "no TS" marker.
3. **Energy profile:** user selects a pathway, meaning a start node and an end node along edges within one branch lineage (or clicks a branch), and picks a reference node. The drawer shows the profile. Branches can be overlaid in their colours (D39). A direct connection is drawn as a dotted connector labelled "no TS", never as a barrier (D53, INV-8).
4. **Energy table:** the drawer tab lists the nodes of the viewed branch or pathway: label, step, branch, level, E, G, ΔX from the reference. There is a CSV export (D44).

### WF-09 Inspect a structure in 3D (D20)
- Selecting a node shows it in the inspector's 3D panel. The user can rotate, zoom, measure distances and angles, and animate an imaginary mode (choosing the mode from a list).
- **Overlay:** select two nodes and choose **Overlay in 3D** to see both aligned.
- The xyz text is shown beside the viewer, with **Copy** and **Save .xyz** buttons (D39).
- Editing the xyz text follows ID-4 and ID-5. With calculations present, **Save** reads "Save as derived node".

### WF-10 Resume after a gap (D45)
Opening an investigation shows the **Overview** panel:
- each branch with its status and counts of nodes by status;
- open items: planned, running externally and failed nodes and transitions, nodes with warnings, and direct connections ("no TS") per branch (D53);
- recent changes from the history (newest first, filterable by date);
- notes per reaction step.
Clicking any item selects it on the canvas and zooms to it.

### WF-11 Export and share
- **Canvas image:** export the current viewport or the whole canvas (D39).
- **Profile image:** export the current profile.
- **xyz:** per node (copy or save). Branch-wide multi-xyz (P7).
- **Share:** the user zips the investigation folder. The app can offer **Export as zip** (P8).

## 3. Canvas behaviour (D33, D40, D43)

| Aspect | Behaviour |
|---|---|
| Branch colour | Nodes and edges take their branch's colour. Interconversion edges and edges between branches are drawn dashed (P9). Nodes with no branch are grey. |
| Direct connection | Drawn dotted with a "no TS" marker, in the branch colour (D53). |
| Status | A badge on each node and edge. Rejected and superseded items are drawn faded, and can be hidden by a filter. |
| Warnings | A warning icon on the node; hovering lists the codes. |
| View modes | **Compact:** label and status. **Energy:** label and ΔX from the reference node. **Structure:** a small rendered picture of the geometry. |
| Group node | A container showing member count, with the representative highlighted. It expands to show members, in a grid, a vertical line or a horizontal line; a button in the expanded group's header switches between the two lines (A21). Members keep their branch colour; a group left with one member by the filters is drawn as that node, and parallel edges between two collapsed groups as one line (D66). |
| Filters | By branch, status, reaction step. Filters hide items but never change data. |
| Navigation | Pan, zoom, fit to selection, and search by label, tag or note text (P10). |
| Consistency | Everything shown on the canvas is derived from records. The canvas has no free-text energy fields (EN-1). |

"""Merging two nodes (D262, FR-NODE-09, A181): T-ID-12 to T-ID-18."""

from chembook3d.models import Calculation
from tests import gaussian_text as g
from tests.test_energies import level_key, view
from tests.test_import import calculations, node, upload
from tests.test_live import ORIGIN, ask
from tests.test_node_geometries import onto
from tests.test_pre_optimization import xyz

# A water with one O–H longer, so its two hydrogens can be told apart.
WATER = [
    ("O", 8, 0.000000, 0.000000, 0.117300),
    ("H", 1, 0.000000, 0.757200, -0.469200),
    ("H", 1, 0.000000, -0.790000, -0.500000),
]
# The same water numbered H, O, H (its first and second atoms swapped).
SWAPPED = [WATER[1], WATER[0], WATER[2]]
SP_ROUTE = "#P B3LYP/def2TZVP SP"
SP_LEVEL = "Gaussian B3LYP/DEF2TZVP // Gaussian B3LYP/6-31G(D)"
OPT_LEVEL = "Gaussian B3LYP/6-31G(D)"


def water(client, label, end=WATER, energy=-76.4089, sp_energy=-76.45, kind="node", **fields):
    start = g.moved(end, 0.05)
    made = client.post(
        "/api/nodes", json={"label": label, "xyz": xyz(start), "kind": kind, **fields}
    ).json()
    onto(client, upload(client, f"{label}.log", g.opt_freq(start, end, energy=energy), made["id"]))
    onto(
        client,
        upload(client, f"{label}_sp.log", g.single_point(end, SP_ROUTE, sp_energy), made["id"]),
    )
    return made["id"]


def preview(client, source, into, **options):
    response = client.post(f"/api/nodes/{source}/merge-preview", json={"into_id": into, **options})
    assert response.status_code == 200, response.text
    return response.json()


def merge(client, source, into, **options):
    return client.post(f"/api/nodes/{source}/merge/{into}", json=options)


def rows(atoms):
    return [[a[0], a[2], a[3], a[4]] for a in atoms]


def test_merge_moves_calculations_renumbered_and_keeps_the_lower_energy(open_client):
    """T-ID-12: A, numbered otherwise, dissolves into B; its calculations come along in B's
    numbering with the map to their files; at the shared levels the lower energy counts."""
    b = water(open_client, "B", energy=-76.4089, sp_energy=-76.45)
    a = water(open_client, "A", SWAPPED, energy=-76.4100, sp_energy=-76.44)
    found = preview(open_client, a, b)
    assert found["refusals"] == [] and found["can_merge"]
    assert found["match"]["mapping"] == [2, 1, 3]  # B's O is A's atom 2
    counts = {lv["label"]: lv["counts"] for lv in found["levels"]}
    assert counts[OPT_LEVEL] == "from"  # A's optimization is lower
    assert counts[SP_LEVEL] == "into"  # B's single point is lower
    assert len(found["calculations"]) == 3

    response = merge(open_client, a, b)
    assert response.status_code == 200, response.text
    assert open_client.get(f"/api/nodes/{a}").status_code == 404
    moved = calculations(open_client, b)
    assert len(moved) == 6
    from_a = [c for c in moved if c["merged_from"]]
    assert len(from_a) == 3
    assert all(c["renumbered"] and c["merged_from"]["label"] == "A" for c in from_a)
    with open_client.app.state.investigation.sessions() as session:
        for c in from_a:
            stored = session.get(Calculation, c["id"])
            assert stored.atom_map == [1, 0, 2]
            if stored.geometry:
                assert stored.geometry == rows([SWAPPED[1], SWAPPED[0], SWAPPED[2]])
            if stored.result.normal_modes:
                assert len(stored.result.normal_modes[0]) == 3
    aside = {c["source_file"]["original_name"]: c["set_aside"] for c in moved}
    assert aside["A_sp.log"] == [SP_LEVEL]  # B's single point is lower
    assert OPT_LEVEL in aside["B.log"]  # A's optimization is lower

    # Values: at the optimization level A's counts, at the single-point level B's.
    opt_key = level_key(open_client, OPT_LEVEL)
    sp_key = level_key(open_client, SP_LEVEL)
    at_opt = view(open_client, opt_key, "E")["values"][b]
    at_sp = view(open_client, sp_key, "E")["values"][b]
    assert at_opt["value"] is not None and abs(at_opt["value"] - -76.4100) < 1e-9
    assert abs(at_sp["value"] - -76.45) < 1e-9
    entries = open_client.get(f"/api/history?record_id={b}").json()
    assert any(e["action"] == "merge_nodes" for e in entries)


def test_frames_from_a_merged_file_come_in_the_new_numbering(open_client):
    """T-ID-13: the stored file is never rewritten; its frames go through the atom map."""
    b = water(open_client, "B")
    a = water(open_client, "A", SWAPPED, energy=-76.4000, sp_energy=-76.40)
    assert merge(open_client, a, b).status_code == 200
    optimization = next(
        c for c in calculations(open_client, b) if c["type"] == "optimization" and c["merged_from"]
    )
    last = open_client.get(f"/api/calculations/{optimization['id']}/steps/-1").json()
    assert [line.split()[0] for line in last["xyz"].splitlines()[2:]] == ["O", "H", "H"]


def test_merge_is_refused_for_another_molecule_or_role(open_client):
    """T-ID-14: free species and canvas node, charge, TS and minimum: refused."""
    b = water(open_client, "B", role="minimum")
    species = water(open_client, "S", kind="species")
    found = preview(open_client, species, b)
    assert "free species" in found["refusals"][0]
    ts = water(open_client, "T", role="transition_state")
    assert "transition state" in preview(open_client, ts, b)["refusals"][0]
    charged = water(open_client, "C")
    assert node(open_client, b)["charge"] == 0
    patched = open_client.patch(f"/api/nodes/{charged}", json={"charge": 1})
    assert patched.status_code == 200, patched.text
    assert "charge" in preview(open_client, charged, b)["refusals"][0]
    assert merge(open_client, charged, b).status_code == 409
    assert node(open_client, charged)["id"] == charged


def test_a_real_bond_change_warns_and_a_weak_one_does_not(open_client):
    """T-ID-14, A181: within 1.2 × the covalent radii in one and beyond 1.5 × in the other."""
    b = water(open_client, "B")
    weak = [WATER[0], WATER[1], ("H", 1, 0.0, -1.0, -0.85)]  # O–H 1.3 Å: between the two
    far = [WATER[0], WATER[1], ("H", 1, 0.0, -1.6, -1.4)]  # O–H 2.2 Å: really broken
    a = water(open_client, "A", weak)
    found = preview(open_client, a, b)
    assert not any("bonded" in w for w in found["warnings"])
    c = water(open_client, "C", far)
    found = preview(open_client, c, b)
    assert any("bonded in “B” but not in “C”" in w for w in found["warnings"])
    assert merge(open_client, c, b).status_code == 409
    assert merge(open_client, c, b, anyway=True).status_code == 200


def test_same_level_geometries_apart_warn(open_client):
    """T-ID-14, Q84: two geometries at one level further apart than the duplicate tolerance."""
    b = water(open_client, "B")
    a = water(open_client, "A", g.moved(WATER, 0.25))
    found = preview(open_client, a, b)
    assert any("duplicate tolerance" in w for w in found["warnings"])
    assert any(row["warned"] for row in found["geometries"])


def test_edges_and_records_move_to_the_kept_node(open_client):
    """T-ID-15: A's edges move, an A–B edge and one parallel to B's go; a saved profile and
    the energy reference name B."""
    b = water(open_client, "B")
    a = water(open_client, "A", SWAPPED)
    x = open_client.post("/api/nodes", json={"label": "X"}).json()["id"]
    y = open_client.post("/api/nodes", json={"label": "Y"}).json()["id"]

    def edge(source, target):
        response = open_client.post(
            "/api/transitions", json={"source_id": source, "target_id": target}
        )
        assert response.status_code in (200, 201), response.text
        return response.json()["id"]

    edge(b, x)
    edge(a, x)  # parallel to B's: dropped
    kept = edge(a, y)  # moves to B
    edge(a, b)  # between the two: dropped
    profile = open_client.post(
        "/api/energy-profiles",
        json={"name": "P", "paths": [{"ids": [a, y], "branch_id": None, "shown": True}]},
    )
    assert profile.status_code in (200, 201), profile.text
    open_client.put("/api/view-state", json={"reference_id": a})

    found = preview(open_client, a, b)
    assert len(found["edges_moved"]) == 1 and len(found["edges_dropped"]) == 2
    assert merge(open_client, a, b).status_code == 200
    transitions = open_client.get("/api/transitions").json()
    ends = sorted((t["source_id"], t["target_id"]) for t in transitions)
    assert ends == sorted([(b, x), (b, y)])
    assert any(t["id"] == kept for t in transitions)
    saved = open_client.get("/api/energy-profiles").json()
    assert saved[0]["paths"][0]["ids"] == [b, y]
    assert open_client.get("/api/view-state").json()["reference_id"] == b


def test_claude_asks_before_merging(open_client):
    """D262i: merge_nodes is a confirm action; the merge waits for the user's confirm."""
    b = water(open_client, "B")
    a = water(open_client, "A", SWAPPED)
    asked = ask(open_client, "merge_nodes", node_id=a, into_id=b)
    assert asked.status_code == 201, asked.text
    request = asked.json()
    assert request["summary"].startswith("Merge “A” into “B”: 3 calculations move")
    assert node(open_client, a)["id"] == a  # nothing yet
    url = f"/api/confirmations/{request['id']}/answer"
    done = open_client.post(url, json={"confirm": True}, headers=ORIGIN).json()
    assert done["status"] == "done", done
    assert open_client.get(f"/api/nodes/{a}").status_code == 404


# ---------- Undo merge (D262h) ----------

SKIPPED = {"history", "alembic_version"}


def everything(client) -> dict:
    """Every row of every table but the history, times of change left out, normal modes as
    read back."""
    from sqlalchemy import select

    from chembook3d.models import Base, NormalModes, unpack_modes

    found = {}
    with client.app.state.investigation.sessions() as session:
        for table in Base.metadata.sorted_tables:
            if table.name in SKIPPED:
                continue
            rows = []
            for row in session.execute(select(table)):
                values = dict(row._mapping)
                values.pop("updated_at", None)
                if table.name == NormalModes.__tablename__:
                    values["data"] = unpack_modes(values["data"])
                rows.append(repr(sorted(values.items())))
            found[table.name] = sorted(rows)
    return found


def merge_entry(client, b):
    entries = client.get("/api/history").json()
    return next(e for e in entries if e["action"] == "merge_nodes" and e["record_id"] == b)


def two_with_edges(client):
    b = water(client, "B", tags=["kept"])
    a = water(client, "A", SWAPPED, tags=["merged"], notes="A's notes")
    x = client.post("/api/nodes", json={"label": "X"}).json()["id"]
    y = client.post("/api/nodes", json={"label": "Y"}).json()["id"]
    for source, target in ((b, x), (a, x), (a, y), (a, b)):
        made = client.post("/api/transitions", json={"source_id": source, "target_id": target})
        assert made.status_code in (200, 201), made.text
    client.put("/api/view-state", json={"reference_id": a})
    return a, b


def test_undo_merge_puts_everything_back(open_client):
    """T-ID-16: one Undo merge brings A back with its id and puts every row the merge changed
    back as it was, normal modes in A's numbering again; it is offered once."""
    a, b = two_with_edges(open_client)
    before = everything(open_client)
    assert merge(open_client, a, b).status_code == 200
    entry = merge_entry(open_client, b)
    assert entry["undo"] == "node_merge"

    found = open_client.get(f"/api/node-merges/{entry['id']}/undo")
    assert found.status_code == 200, found.text
    assert found.json()["blockers"] == [] and found.json()["calculations"] == 3
    done = open_client.post(f"/api/node-merges/{entry['id']}/undo")
    assert done.status_code == 200, done.text

    after = everything(open_client)
    for table in before:
        assert after[table] == before[table], table
    assert open_client.get("/api/view-state").json()["reference_id"] == a
    assert node(open_client, a)["label"] == "A"
    assert merge_entry(open_client, b)["undo"] is None
    again = open_client.post(f"/api/node-merges/{entry['id']}/undo")
    assert again.status_code >= 400
    undone = open_client.get("/api/history").json()[0]
    assert undone["action"] == "undo_merge_nodes" and undone["new_value"]["entry_id"] == entry["id"]


def test_undo_merge_is_refused_when_later_work_changed_what_it_wrote(open_client):
    """T-ID-17: a moved calculation removed since refuses the undo, naming it; a change to a
    column the merge did not write (B's label) does not, and stays."""
    b = water(open_client, "B")
    a = water(open_client, "A", SWAPPED)
    assert merge(open_client, a, b).status_code == 200
    entry = merge_entry(open_client, b)
    open_client.patch(f"/api/nodes/{b}", json={"label": "B2"})
    assert open_client.get(f"/api/node-merges/{entry['id']}/undo").json()["blockers"] == []

    moved = [
        c for c in calculations(open_client, b) if c["source_file"]["original_name"] == "A_sp.log"
    ]
    removed = open_client.delete(f"/api/calculations/{moved[0]['id']}")
    assert removed.status_code in (200, 204), removed.text
    blockers = open_client.get(f"/api/node-merges/{entry['id']}/undo").json()["blockers"]
    assert any("was removed after the merge" in text for text in blockers), blockers
    refused = open_client.post(f"/api/node-merges/{entry['id']}/undo")
    assert refused.status_code == 409
    assert node(open_client, b)["label"] == "B2"


def test_undo_import_of_a_merged_file_asks_to_undo_the_merge_first(open_client):
    """T-ID-18: Undo import of a file whose calculations were merged is blocked until the
    merge is undone (D262h); then it goes through."""
    b = water(open_client, "B")
    a = water(open_client, "A", SWAPPED)
    assert merge(open_client, a, b).status_code == 200
    entries = open_client.get("/api/history").json()
    imported = next(
        e
        for e in entries
        if e["undo"] == "import" and e["new_value"] and "A_sp" in str(e["new_value"])
    )
    blocked = open_client.get(f"/api/history/{imported['id']}/undo").json()
    assert any("undo that merge first" in text for text in blocked["blockers"]), blocked
    entry = merge_entry(open_client, b)
    assert open_client.post(f"/api/node-merges/{entry['id']}/undo").status_code == 200
    assert open_client.get(f"/api/history/{imported['id']}/undo").json()["blockers"] == []

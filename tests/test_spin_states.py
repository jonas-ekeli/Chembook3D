"""A node's energies are those of its own electronic state (D155): T-EN-22…24. A single point
for another charge or multiplicity never replaces the node's value at the same composite level,
and two states at one level give no value instead of the latest one."""

from tests import gaussian_text as g
from tests.test_energies import import_file, level_key, view
from tests.test_import import node, upload
from tests.test_pathway import patch

TZ_DZ = "Gaussian B3LYP/DEF2TZVP // Gaussian B3LYP/6-31G(D)"
SP_ROUTE = "#P B3LYP/def2TZVP SP"


def single_point(energy: float, multiplicity: int = 1) -> str:
    return g.output(g.step(SP_ROUTE, [g.WATER], energy, multiplicity=multiplicity))


def singlet_node(client) -> str:
    return import_file(client, "opt.log", g.opt_freq(g.WATER, g.WATER, -76.45))["node_id"]


def test_another_spin_state_does_not_replace_the_nodes_energy(open_client):
    # T-EN-22: the reviewer's case: a triplet single point imported after the singlet one at
    # the same level used to become the node's E, and G_qh paired it with the singlet's
    # thermal correction.
    node_id = singlet_node(open_client)
    import_file(open_client, "singlet.log", single_point(-76.50), node_id)
    import_file(open_client, "triplet.log", single_point(-76.40, 3), node_id)
    key = level_key(open_client, TZ_DZ)
    assert view(open_client, key, "E")["values"][node_id]["value"] == -76.50
    g_qh = view(open_client, key, "G_qh")["values"][node_id]
    assert g_qh["value"] is not None and g_qh["value"] < -76.45
    (warning,) = [w for w in node(open_client, node_id)["warnings"] if w["code"] == "W-CHG"]
    assert warning["message"] == (
        "calculations for charge 0 mult 3 are left out of the energies of this node "
        "(charge 0 mult 1)"
    )


def test_a_level_with_only_another_state_says_why_it_has_no_value(open_client):
    # T-EN-23
    node_id = singlet_node(open_client)
    other = singlet_node(open_client)
    import_file(open_client, "singlet.log", single_point(-76.50), other)
    import_file(open_client, "triplet.log", single_point(-76.40, 3), node_id)
    value = view(open_client, level_key(open_client, TZ_DZ), "E")["values"][node_id]
    assert value["value"] is None and value["code"] == "W-CHG"
    assert value["message"] == (
        "the calculations at this level are for charge 0, multiplicity 3, not the node's "
        "charge 0, multiplicity 1"
    )


def test_import_preview_says_another_state_gives_no_energies(open_client):
    node_id = singlet_node(open_client)
    plan = upload(open_client, "triplet.log", single_point(-76.40, 3), node_id)
    (warning,) = [w for w in plan["warnings"] if w["code"] == "W-CHG"]
    assert "import it as a new node" in warning["message"]


def test_without_a_node_state_two_states_at_one_level_give_no_value(open_client):
    # T-EN-24: a node whose charge and multiplicity are not set (cleared by hand here) takes
    # neither of two states at one level.
    node_id = singlet_node(open_client)
    import_file(open_client, "singlet.log", single_point(-76.50), node_id)
    import_file(open_client, "triplet.log", single_point(-76.40, 3), node_id)
    key = level_key(open_client, TZ_DZ)
    patch(open_client, f"/nodes/{node_id}", {"charge": None, "multiplicity": None})
    value = view(open_client, key, "E")["values"][node_id]
    assert value["value"] is None and value["code"] == "W-CHG"
    assert value["message"] == (
        "the calculations at this level are for charge 0, multiplicity 1 and charge 0, "
        "multiplicity 3"
    )

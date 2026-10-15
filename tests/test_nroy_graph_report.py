"""Connectivity, reporting and end-to-end runner tests.

Covers section 4 (connectivity without Euclidean overclaiming) and the
"Reporting" block of section 9 of ``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md``,
plus a full ``prepare -> evaluate -> merge -> report`` pass driven through the
real CLI with the surrogate evaluator.

No simulation engine is constructed and no trajectory is run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402
from nroy_fixtures import ROOT  # noqa: E402

import coupled_calibration_search as ccs  # noqa: E402
import nroy_acceptance_inputs as nai  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
import nroy_graph as ng  # noqa: E402
import nroy_identity_gate as nig  # noqa: E402
import nroy_report as nrp  # noqa: E402
import run_robust_nroy_freeze as runner  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402
from tools import audit_robust_nroy_seeds as seed_audit_tool  # noqa: E402

EXTERNAL_WORKBOOK = (ROOT / "../data/raw/validation/DiseaseData.xlsx").resolve()
EXTERNAL_TARGET_CANDIDATE = ROOT / (
    "outputs/calibration/rivm_aids_vintage_audit_20260916/"
    "candidate_newest_vetted_20260916.json"
)
EXTERNAL_INPUTS = nc.strict_json_load(
    ROOT / "data" / "external_inputs.json", what="external-input manifest"
)
EXTERNAL_INPUTS_BY_ID = {
    item["logical_id"]: item for item in EXTERNAL_INPUTS["inputs"]
}


def _is_exact_external_input(path: Path, logical_id: str) -> bool:
    expected = EXTERNAL_INPUTS_BY_ID[logical_id]["sha256"]
    return path.is_file() and ccs.sha256_file(path) == expected


EXTERNAL_ACCEPTANCE_INPUTS_PRESENT = (
    _is_exact_external_input(EXTERNAL_WORKBOOK, "history_matching_target_workbook")
    and _is_exact_external_input(
        EXTERNAL_TARGET_CANDIDATE, "rivm_aids_newest_vetted_target_candidate"
    )
)
EXTERNAL_ACCEPTANCE_INPUTS_REASON = (
    "external-data lane requires the exact manifest-hashed DiseaseData.xlsx and "
    "candidate_newest_vetted_20260916.json"
)

#: The only bridge pass rule the implementation provides; the plan signs it and
#: nroy_graph.classify_edge checks it rather than assuming it.
PASS_RULE = ng.BRIDGE_PASS_RULES[0]

TARGETS = ccs.FROZEN_TARGET_ORDER
CONTRACT = nc.TargetContract(frozen_order=TARGETS, register_path="x", register_sha256="h")


# ==========================================================================
# Candidate edges: proposal only
# ==========================================================================

def two_basins() -> dict[str, list[float]]:
    points = {}
    for i in range(3):
        points[f"A{i}"] = [0.10 + 0.01 * i, 0.10, 0.50]
    for i in range(3):
        points[f"B{i}"] = [0.90 + 0.01 * i, 0.90, 0.50]
    return points


def test_candidate_edges_are_deterministic_and_tie_broken_by_id():
    points = {"a": [0.0, 0.0], "b": [1.0, 0.0], "c": [-1.0, 0.0]}  # b and c equidistant
    first = ng.candidate_edges(points, proposal_rule="complete_pairwise_distances")
    shuffled = {k: points[k] for k in ("c", "b", "a")}
    second = ng.candidate_edges(shuffled, proposal_rule="complete_pairwise_distances")
    assert [e.edge_id for e in first] == [e.edge_id for e in second]
    assert [e.edge_id for e in first] == ["a--b", "a--c", "b--c"]


def test_unknown_graph_rules_are_refused():
    points = two_basins()
    with pytest.raises(ContractError, match="unknown edge-proposal rule"):
        ng.candidate_edges(points, proposal_rule="whatever")
    with pytest.raises(ContractError, match="unknown tie-breaking"):
        ng.candidate_edges(
            points, proposal_rule="complete_pairwise_distances", tie_breaking="random"
        )
    with pytest.raises(ContractError, match="unknown minimal-edge algorithm"):
        ng.minimal_edge_set([], algorithm="magic", node_ids=list(points))


@pytest.mark.parametrize(
    "points",
    (
        {"a": [0.0], "b": [float("nan")]},
        {"a": [0.0], "b": [1.0, 2.0]},
    ),
)
def test_pairwise_graph_refuses_nonfinite_or_ragged_geometry(points):
    with pytest.raises(ContractError, match="rectangular finite matrix"):
        ng.candidate_edges(points, proposal_rule="complete_pairwise_distances")


def test_complete_pairwise_kruskal_freezes_exactly_n_minus_one_edges():
    points = two_basins()
    edges = ng.candidate_edges(points, proposal_rule="complete_pairwise_distances")
    minimal = ng.minimal_edge_set(
        edges, algorithm="kruskal_minimum_spanning_tree", node_ids=list(points)
    )
    assert len(edges) == len(points) * (len(points) - 1) // 2
    assert len(minimal) == len(points) - 1


def test_complete_pairwise_proposal_does_not_drop_cross_group_edges():
    points = two_basins()
    edges = ng.candidate_edges(points, proposal_rule="complete_pairwise_distances")
    pairs = {(edge.left[0], edge.right[0]) for edge in edges}
    assert ("A", "B") in pairs


# ==========================================================================
# Edge classification
# ==========================================================================

def _edge(left="L", right="R", distance=0.5):
    return ng.CandidateEdge(
        edge_id=f"{left}--{right}", left=left, right=right, distance=distance, rank=0
    )


def test_an_edge_is_supported_only_when_every_declared_probe_confirms():
    verdict = ng.classify_edge(
        _edge(), endpoint_confirmed={"L": True, "R": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED] * 3, n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_SAMPLED_PATH_SUPPORTED
    assert "not continuous connectivity" in verdict.reason


def test_a_failed_straight_bridge_is_unresolved_not_disconnection():
    """The central anti-overclaim rule of section 4."""
    verdict = ng.classify_edge(
        _edge(), endpoint_confirmed={"L": True, "R": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED, nc.EVIDENCE_NO_SUPPORT, nc.EVIDENCE_CONFIRMED],
        n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_UNRESOLVED
    assert "not proof of disconnection" in verdict.reason
    assert "may curve" in verdict.reason


def test_a_partially_run_bridge_is_unresolved_and_names_the_missing_probes():
    verdict = ng.classify_edge(
        _edge(), endpoint_confirmed={"L": True, "R": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED], n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_UNRESOLVED
    assert verdict.evidence == nc.EVIDENCE_NOT_RUN
    assert verdict.n_probes_not_run == 2


def test_a_bridge_execution_failure_is_not_evidence_of_disconnection():
    verdict = ng.classify_edge(
        _edge(), endpoint_confirmed={"L": True, "R": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED, nc.EVIDENCE_EXECUTION_FAILURE,
                        nc.EVIDENCE_CONFIRMED],
        n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_UNRESOLVED
    assert verdict.evidence == nc.EVIDENCE_EXECUTION_FAILURE
    assert "not evidence of disconnection" in verdict.reason


def test_an_unconfirmed_endpoint_blocks_support():
    verdict = ng.classify_edge(
        _edge(), endpoint_confirmed={"L": True, "R": False},
        probe_evidence=[nc.EVIDENCE_CONFIRMED] * 3, n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_UNRESOLVED
    assert "not confirmed" in verdict.reason


# ==========================================================================
# Components on synthetic geometry
# ==========================================================================

def test_disconnected_basins_yield_two_components():
    points = two_basins()
    edges = ng.candidate_edges(points, proposal_rule="complete_pairwise_distances")
    minimal = ng.minimal_edge_set(
        edges, algorithm="kruskal_minimum_spanning_tree", node_ids=list(points)
    )
    confirmed = {k: True for k in points}
    verdicts = [
        ng.classify_edge(
            edge, endpoint_confirmed=confirmed,
            probe_evidence=(
                [nc.EVIDENCE_CONFIRMED] * 3 if edge.left[0] == edge.right[0]
                else [nc.EVIDENCE_CONFIRMED, nc.EVIDENCE_NO_SUPPORT, nc.EVIDENCE_CONFIRMED]
            ),
            n_declared=3,
            pass_rule=PASS_RULE,
        )
        for edge in minimal
    ]
    components = ng.components(list(points), verdicts)
    assert len(components) == 2
    members = {frozenset(c.members) for c in components}
    assert members == {frozenset({"A0", "A1", "A2"}), frozenset({"B0", "B1", "B2"})}


def test_a_failed_cross_basin_bridge_keeps_the_basins_separate_as_unresolved():
    points = {"A": [0.1, 0.1], "B": [0.9, 0.9]}
    edge = _edge("A", "B", distance=1.13)
    verdict = ng.classify_edge(
        edge, endpoint_confirmed={"A": True, "B": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED, nc.EVIDENCE_NO_SUPPORT, nc.EVIDENCE_CONFIRMED],
        n_declared=3,
        pass_rule=PASS_RULE,
    )
    components = ng.components(list(points), [verdict])
    assert len(components) == 2
    # The unresolved edge is recorded against both components, not hidden.
    assert all(c.unresolved_edges == ("A--B",) for c in components)
    assert ng.unresolved_changes_representation(components, [verdict]) == ["A--B"]


def test_a_curved_corridor_fails_its_straight_bridge_and_is_reported_unresolved():
    """A real curved region: the straight chord leaves the plausible corridor."""
    space = fx.m0_space()
    centre_a = np.full(space.n, 0.35)
    centre_b = np.full(space.n, 0.65)
    field = fx.SyntheticField("curved_corridor", [centre_a, centre_b], noise=0.0)
    # Endpoints are plausible...
    assert field.i_max(centre_a) <= 3.0
    assert field.i_max(centre_b) <= 3.0
    # ...but the straight midpoint is not, because the corridor bulges.
    midpoint = 0.5 * (centre_a + centre_b)
    assert field.i_max(midpoint) > 3.0

    evidence = []
    for fraction in (0.25, 0.5, 0.75):
        u = centre_a + fraction * (centre_b - centre_a)
        evidence.append(
            nc.EVIDENCE_CONFIRMED if field.i_max(u) <= 3.0 else nc.EVIDENCE_NO_SUPPORT
        )
    verdict = ng.classify_edge(
        _edge("A", "B"), endpoint_confirmed={"A": True, "B": True},
        probe_evidence=evidence, n_declared=3,
        pass_rule=PASS_RULE,
    )
    assert verdict.label == nc.EDGE_UNRESOLVED
    assert "not proof of disconnection" in verdict.reason


def test_a_singleton_is_a_legitimate_component():
    components = ng.components(["solo"], [])
    assert len(components) == 1
    assert components[0].members == ("solo",)


def test_components_refuse_an_edge_outside_the_declared_node_set():
    verdict = ng.classify_edge(
        _edge("X", "Y"), endpoint_confirmed={"X": True, "Y": True},
        probe_evidence=[nc.EVIDENCE_CONFIRMED] * 3, n_declared=3,
        pass_rule=PASS_RULE,
    )
    with pytest.raises(ContractError, match="outside the declared set"):
        ng.components(["A", "B"], [verdict])


# ==========================================================================
# Representative selection
# ==========================================================================

def test_representative_selection_takes_no_policy_outcome_argument():
    """Selection must be structurally unable to see policy outcomes."""
    import inspect
    signature = inspect.signature(ng.select_representatives)
    for forbidden in ("policy", "outcome", "qaly", "cost", "icer", "forecast"):
        assert not any(forbidden in name.lower() for name in signature.parameters), (
            f"select_representatives must not accept a {forbidden!r} argument"
        )


def test_only_points_with_demonstrated_local_support_are_selected():
    points = two_basins()
    components = ng.components(list(points), [])
    eligible = {k: k.startswith("A") for k in points}
    frame = ng.select_representatives(
        components, points, n_per_component=1, rule="maximin_geometry", eligible=eligible
    )
    chosen = frame["candidate_id"].dropna().tolist()
    assert chosen and all(c.startswith("A") for c in chosen)


def test_a_component_with_no_eligible_member_is_reported_not_dropped():
    points = {"B0": [0.9, 0.9]}
    components = ng.components(list(points), [])
    frame = ng.select_representatives(
        components, points, n_per_component=1, rule="maximin_geometry",
        eligible={"B0": False},
    )
    assert len(frame) == 1
    assert frame.loc[0, "candidate_id"] is None or pd.isna(frame.loc[0, "candidate_id"])
    assert int(frame.loc[0, "n_eligible_members"]) == 0
    assert "not silently dropped" in str(frame.loc[0, "note"])


def test_an_unsigned_selection_rule_is_refused():
    components = ng.components(["A"], [])
    with pytest.raises(ContractError, match="must be declared and signed"):
        ng.select_representatives(
            components, {"A": [0.0]}, n_per_component=1, rule="whatever_looks_best"
        )


def test_representative_selection_is_deterministic():
    points = two_basins()
    components = ng.components(list(points), [])
    eligible = {k: True for k in points}
    first = ng.select_representatives(
        components, points, n_per_component=2, rule="maximin_geometry", eligible=eligible
    )
    reordered = {k: points[k] for k in reversed(list(points))}
    second = ng.select_representatives(
        components, reordered, n_per_component=2, rule="maximin_geometry", eligible=eligible
    )
    pd.testing.assert_frame_equal(first, second)


# ==========================================================================
# Reporting
# ==========================================================================

def sample_tables() -> dict[str, pd.DataFrame]:
    support = pd.DataFrame([
        {"candidate_id": "A0", "local_support_label": nc.LABEL_BROADLY_SUPPORTED,
         "eligible_for_representation": True},
        {"candidate_id": "A1", "local_support_label": nc.LABEL_RIDGE_SUPPORTED,
         "eligible_for_representation": True},
        {"candidate_id": "A2", "local_support_label": nc.LABEL_NO_LOCAL_SUPPORT,
         "eligible_for_representation": False},
        {"candidate_id": "B0", "local_support_label": nc.LABEL_UNRESOLVED,
         "eligible_for_representation": False},
    ])
    components = pd.DataFrame([
        {"component_id": "R001", "n_members": 3, "members": "A0|A1|A2",
         "n_supporting_edges": 2, "supporting_edges": "A0--A1|A1--A2",
         "n_unresolved_boundary_edges": 1, "unresolved_boundary_edges": "A2--B0"},
        {"component_id": "R002", "n_members": 1, "members": "B0",
         "n_supporting_edges": 0, "supporting_edges": "",
         "n_unresolved_boundary_edges": 1, "unresolved_boundary_edges": "A2--B0"},
    ])
    edges = pd.DataFrame([
        {"edge_id": "A0--A1", "left_candidate_id": "A0", "right_candidate_id": "A1",
         "edge_label": nc.EDGE_SAMPLED_PATH_SUPPORTED,
         "evidence_state": nc.EVIDENCE_CONFIRMED, "n_bridge_probes_confirmed": 3},
        {"edge_id": "A1--A2", "left_candidate_id": "A1", "right_candidate_id": "A2",
         "edge_label": nc.EDGE_SAMPLED_PATH_SUPPORTED,
         "evidence_state": nc.EVIDENCE_CONFIRMED, "n_bridge_probes_confirmed": 3},
        {"edge_id": "A2--B0", "left_candidate_id": "A2", "right_candidate_id": "B0",
         "edge_label": nc.EDGE_UNRESOLVED,
         "evidence_state": nc.EVIDENCE_UNRESOLVED, "n_bridge_probes_confirmed": 1},
    ])
    representatives = pd.DataFrame([
        {"component_id": "R001", "candidate_id": "A0", "selection_rank": 1,
         "n_eligible_members": 2, "selection_rule": "maximin_geometry", "note": ""},
        {"component_id": "R002", "candidate_id": None, "selection_rank": None,
         "n_eligible_members": 0, "selection_rule": "maximin_geometry", "note": "none"},
    ])
    scores = pd.DataFrame([
        {"design_id": "A0", "i_max": 1.2, "nominal_pass": True, "promoted": True,
         "variance_unstable": False},
        {"design_id": "A1", "i_max": 2.9, "nominal_pass": True, "promoted": True,
         "variance_unstable": False},
        {"design_id": "A2", "i_max": 3.4, "nominal_pass": False, "promoted": True,
         "variance_unstable": True},
    ])
    attempts = pd.DataFrame([
        {"attempt_id": "a1", "design_id": "A0", "seed": 1, "status": ne.ATTEMPT_SUCCESS},
        {"attempt_id": "a2", "design_id": "A1", "seed": 1, "status": ne.ATTEMPT_SUCCESS},
        {"attempt_id": "a3", "design_id": "A2", "seed": 1, "status": ne.ATTEMPT_FAILED},
        {"attempt_id": "a4", "design_id": "A2", "seed": 1, "status": ne.ATTEMPT_SUCCESS},
        {"attempt_id": "a5", "design_id": "B0", "seed": 1, "status": ne.ATTEMPT_LOST},
    ])
    units = pd.DataFrame([
        {"design_id": d, "seed": 1} for d in ("A0", "A1", "A2", "B0")
    ])
    coverage = nrp.p_coverage_table(
        [0.0, 0.1, 0.2], [0.0, 0.05, 0.15], [True, True, False]
    )
    return {
        "local_support": support, "components": components, "edge_verdicts": edges,
        "representatives": representatives, "candidate_scores": scores,
        "ledger_attempts": attempts, "ledger_units": units, "p_coverage": coverage,
    }


def test_every_executive_summary_number_is_recomputed_from_the_tables():
    """The headline numbers must be derivable from the CSVs alone."""
    tables = sample_tables()
    facts = nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M1)
    f = facts.as_dict()

    support = tables["local_support"]
    labels = support["local_support_label"]
    assert f["n_anchors"] == len(support)
    assert f[f"n_{nc.LABEL_BROADLY_SUPPORTED}"] == int(
        (labels == nc.LABEL_BROADLY_SUPPORTED).sum()
    )
    assert f[f"n_{nc.LABEL_RIDGE_SUPPORTED}"] == int((labels == nc.LABEL_RIDGE_SUPPORTED).sum())
    assert f[f"n_{nc.LABEL_NO_LOCAL_SUPPORT}"] == int(
        (labels == nc.LABEL_NO_LOCAL_SUPPORT).sum()
    )
    assert f[f"n_{nc.LABEL_UNRESOLVED}"] == int((labels == nc.LABEL_UNRESOLVED).sum())
    assert f["n_eligible"] == int(support["eligible_for_representation"].sum())
    assert f["eligible_candidate_ids"] == ["A0", "A1"]
    assert f["no_support_candidate_ids"] == ["A2"]
    assert f["unresolved_candidate_ids"] == ["B0"]

    assert f["n_components"] == len(tables["components"])
    assert f["component_ids"] == ["R001", "R002"]
    assert f["n_edges_sampled_path_supported"] == 2
    assert f["n_edges_unresolved"] == 1
    assert f["n_representatives"] == 1
    assert f["n_components_without_representative"] == 1
    assert f["n_promoted"] == 3
    assert f["n_nominal_pass"] == 2
    assert f["n_variance_unstable"] == 1
    assert f["n_failed_attempts"] == 1
    assert f["n_lost_attempts"] == 1
    assert f["n_successful_attempts"] == 3
    assert f["n_units_complete"] == 3
    assert f["n_units_incomplete"] == 1
    assert f["n_p_bins"] == 2
    assert f["n_p_bins_covered"] == 1


def test_summary_facts_are_written_so_a_reader_can_recheck_them(tmp_path):
    tables = sample_tables()
    facts = nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M1)
    manifest = {"stage": nc.STAGE_REGIONS, "model": nc.MODEL_M1,
                "status": nc.STATUS_COMPLETE, "parentable": True, "contract": {}}
    nrp.write_package(tmp_path, tables, facts=facts, manifest=manifest, question="Q")
    written = json.loads((tmp_path / "summary_facts.json").read_text())
    assert written["n_eligible"] == facts["n_eligible"]
    assert written["component_ids"] == facts["component_ids"]


def test_the_package_distinguishes_no_support_from_no_simulation():
    """'nothing found' and 'nothing run' must never be the same number."""
    nothing_run = nrp.summary_facts({}, status=nc.STATUS_PREPARED, model=nc.MODEL_M0)
    assert nothing_run["evidence_state"] == nc.EVIDENCE_NOT_RUN
    assert nothing_run["assessment_ran"] is False
    assert nothing_run[f"n_{nc.LABEL_NO_LOCAL_SUPPORT}"] is None
    # None, never 0: "nothing ran" must not render as "we found none".
    for key in (
        "n_anchors", "n_components", "n_representatives", "n_edges_evaluated",
        "n_candidates_scored", "n_attempts",
    ):
        assert nothing_run[key] is None, f"{key} must be None when nothing ran"

    ran_and_found_nothing = nrp.summary_facts(
        {"local_support": pd.DataFrame([{
            "candidate_id": "A0", "local_support_label": nc.LABEL_NO_LOCAL_SUPPORT,
            "eligible_for_representation": False,
        }])},
        status=nc.STATUS_COMPLETE, model=nc.MODEL_M0,
    )
    # The assessment ran, but it confirmed nothing. Reporting
    # "confirmed_support" here would be an overclaim.
    assert ran_and_found_nothing["assessment_ran"] is True
    assert ran_and_found_nothing["evidence_state"] == nc.EVIDENCE_NO_SUPPORT
    assert ran_and_found_nothing[f"n_{nc.LABEL_NO_LOCAL_SUPPORT}"] == 1

    summary = nrp.render_executive_summary(nothing_run, question="Q")
    assert "not run" in summary


def test_evidence_state_reflects_findings_not_merely_that_the_run_happened():
    """Three runs that all 'ran' but found different things."""
    def facts_for(label):
        return nrp.summary_facts(
            {"local_support": pd.DataFrame([{
                "candidate_id": "A0", "local_support_label": label,
                "eligible_for_representation": label in nc.ELIGIBLE_LABELS,
            }])},
            status=nc.STATUS_COMPLETE, model=nc.MODEL_M0,
        )
    assert facts_for(nc.LABEL_BROADLY_SUPPORTED)["evidence_state"] == nc.EVIDENCE_CONFIRMED
    assert facts_for(nc.LABEL_RIDGE_SUPPORTED)["evidence_state"] == nc.EVIDENCE_CONFIRMED
    assert facts_for(nc.LABEL_UNRESOLVED)["evidence_state"] == nc.EVIDENCE_UNRESOLVED
    assert facts_for(nc.LABEL_NO_LOCAL_SUPPORT)["evidence_state"] == nc.EVIDENCE_NO_SUPPORT
    for label in nc.LOCAL_SUPPORT_LABELS:
        assert facts_for(label)["assessment_ran"] is True


def test_all_five_evidence_states_are_documented_in_the_package():
    tables = sample_tables()
    dictionary = nrp.data_dictionary(tables)
    assert set(dictionary["evidence_states"]) == set(nc.EVIDENCE_STATES)
    summary = nrp.render_executive_summary(
        nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M1),
        question="Q",
    )
    for state in nc.EVIDENCE_STATES:
        assert state in summary


def test_table_schemas_and_the_data_dictionary_agree():
    tables = sample_tables()
    dictionary = nrp.data_dictionary(tables)
    for name, frame in tables.items():
        described = [c["name"] for c in dictionary["tables"][name]["columns"]]
        assert described == [str(c) for c in frame.columns]
        assert dictionary["tables"][name]["n_rows"] == len(frame)


def test_every_table_is_registered_in_the_table_spec():
    tables = sample_tables()
    unregistered = set(tables) - set(nrp.TABLE_SPEC)
    assert not unregistered, f"undocumented tables: {sorted(unregistered)}"
    index = nrp.render_table_index(tables)
    for name in tables:
        assert f"`{name}.csv`" in index
        assert nrp.TABLE_SPEC[name]["row_unit"] in index


def test_ids_must_resolve_uniquely():
    tables = sample_tables()
    nrp.assert_ids_resolve(tables)

    duplicated = dict(tables)
    duplicated["local_support"] = pd.concat(
        [tables["local_support"], tables["local_support"].head(1)], ignore_index=True
    )
    with pytest.raises(ContractError, match="duplicate candidate_id"):
        nrp.assert_ids_resolve(duplicated)

    overlapping = dict(tables)
    overlapping["components"] = pd.DataFrame([
        {"component_id": "R001", "members": "A0|A1", "n_unresolved_boundary_edges": 0},
        {"component_id": "R002", "members": "A1|A2", "n_unresolved_boundary_edges": 0},
    ])
    with pytest.raises(ContractError, match="more than one component"):
        nrp.assert_ids_resolve(overlapping)

    unknown = dict(tables)
    unknown["representatives"] = pd.DataFrame([
        {"component_id": "R999", "candidate_id": "A0"}
    ])
    with pytest.raises(ContractError, match="unknown components"):
        nrp.assert_ids_resolve(unknown)


def test_the_executive_summary_carries_the_required_disclaimers():
    tables = sample_tables()
    summary = nrp.render_executive_summary(
        nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M1),
        question="Q",
    )
    for phrase in (
        "not topological components",
        "not a claim that the point is isolated",
        "descriptive coverage",
        "none found under the declared search and budget",
        "required identity control",
    ):
        assert phrase in summary, f"missing disclaimer: {phrase!r}"


def test_a_partial_stage_is_flagged_as_a_blocker_in_the_summary():
    tables = sample_tables()
    facts = nrp.summary_facts(tables, status=nc.STATUS_PARTIAL, model=nc.MODEL_M0)
    summary = nrp.render_executive_summary(facts, question="Q")
    assert "cannot become a parent" in summary
    assert facts["stage_parentable"] is False


def test_unsigned_values_appear_as_a_blocker():
    facts = nrp.summary_facts({}, status=nc.STATUS_PREPARED, model=nc.MODEL_M0)
    summary = nrp.render_executive_summary(
        facts, question="Q", unsigned=["budget.max_trajectories"]
    )
    assert "unsigned" in summary
    assert "budget.max_trajectories" in summary


def test_region_report_has_one_section_per_component():
    tables = sample_tables()
    report = nrp.render_region_report(tables["components"], tables["local_support"])
    assert "## Component R001" in report
    assert "## Component R002" in report
    assert "not a posterior" in report


def test_run_audit_reports_recomputation_status_and_hashes():
    tables = sample_tables()
    facts = nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M0)
    manifest = {
        "stage": nc.STAGE_REGIONS, "model": nc.MODEL_M0, "status": nc.STATUS_COMPLETE,
        "parentable": True, "n_units": 4, "n_completed": 3, "n_incomplete": 1,
        "consumed_attempts": 5, "ledger_frozen_sha256": "abc",
        "contract": {"seeds": {"discovery": [1, 2], "k_discovery": 2},
                     "limitations": ["finite design"]},
    }
    audit = nrp.render_run_audit(facts, manifest=manifest, recomputation_verified=True)
    assert "verified" in audit
    assert "abc" in audit
    assert "never trusts a shard summary" in audit
    assert "finite design" in audit
    not_verified = nrp.render_run_audit(facts, manifest=manifest, recomputation_verified=False)
    assert "NOT verified" in not_verified


def test_p_coverage_is_labelled_descriptive_only():
    table = nrp.p_coverage_table([0.0, 0.25, 0.5], [0.0, 0.3], [True, True])
    assert bool(table["descriptive_coverage_only"].all())
    assert int(table.loc[0, "n_confirmed"]) == 1
    assert int(table.loc[1, "n_confirmed"]) == 1


def test_p_coverage_closes_the_final_bin():
    table = nrp.p_coverage_table([0.0, 0.5], [0.5], [True])
    assert int(table.loc[0, "n_evaluated"]) == 1, "the top edge must fall inside a bin"


# ==========================================================================
# End-to-end through the real CLI
# ==========================================================================

def _write_anchors(path: Path, space, count=3, seed=77):
    rows = []
    for index, vector in enumerate(fx.valid_interior_vectors(space, count, seed=seed)):
        rows.append({"candidate_id": f"A{index}", **vector})
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_validate_subcommand_fails_closed_on_the_shipped_plan(capsys):
    code = runner.main(["validate", "--plan", str(fx.PRODUCTION_PLAN)])
    out = capsys.readouterr().out
    assert code == 2, "validate must exit non-zero while decisions are unsigned"
    assert "FAIL CLOSED" in out
    assert "No production simulation may start" in out
    assert "separate signature gate" in out
    assert "sign_off.signed_by" in out


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('{"schema": "first", "schema": "second"}', "duplicate JSON object key"),
        ('{"schema": NaN}', "non-finite JSON token"),
        ('{"schema": Infinity}', "non-finite JSON token"),
        ('{"schema": -Infinity}', "non-finite JSON token"),
        ('{"schema": 1e999}', "overflows to a non-finite value"),
    ],
)
def test_validate_subcommand_uses_strict_plan_json(tmp_path, capsys, text, problem):
    plan_path = tmp_path / "adversarial_plan.json"
    plan_path.write_text(text)
    assert runner.main(["validate", "--plan", str(plan_path)]) == 1
    captured = capsys.readouterr()
    assert "CONTRACT ERROR" in captured.err
    assert problem in captured.err


def _write_typed_external_review(
        tmp_path: Path, kind: str, *, status: str, reviewer: str,
        reviewed_utc: str) -> list[dict]:
    path = tmp_path / f"external_review_{kind.lower()}.json"
    path.write_text(json.dumps({
        "schema": seed_audit_tool.EXTERNAL_REVIEW_SCHEMA,
        "location_kind": kind,
        "review_status": status,
        "reviewer": reviewer,
        "reviewed_utc": reviewed_utc,
        "searched_locations": [f"isolated-{kind.lower()}-fixture"],
        "searched_accounts_or_owners": ["fixture-owner"],
        "search_method": "Inspected the isolated verification fixture.",
        "search_started_utc": reviewed_utc,
        "search_completed_utc": reviewed_utc,
        "indexed_sources": [],
        "no_additional_source_basis": "The isolated fixture has no additional source.",
    }))
    return [{
        "path": path.name,
        "sha256": ccs.sha256_file(path),
        "description": f"Typed {kind} review record.",
    }]


def _write_final_production_evidence(tmp_path: Path, plan: nc.FrozenPlan) -> dict[str, Path]:
    base = Path(str(plan.require("models.base_config")))
    if not base.is_absolute():
        base = ROOT / base
    m0_space = Path(str(plan.require("models.m0.calib_space")))
    if not m0_space.is_absolute():
        m0_space = ROOT / m0_space
    target_register = Path(plan.targets.register_path)
    if not target_register.is_absolute():
        target_register = ROOT / target_register
    runtime = nig.scientific_runtime_fingerprint()
    paths = {
        key: Path(value)
        for key, value in plan.get("production_prerequisites").items()
    }

    registry_rows = {
        "plan path": plan.plan_path,
        "plan SHA-256": plan.plan_sha256,
        "base-config path": str(base),
        "base-config SHA-256": ccs.sha256_file(base),
        "M0 calibration-space path": str(m0_space),
        "M0 calibration-space SHA-256": ccs.sha256_file(m0_space),
        "target-register path": str(target_register),
        "target-register SHA-256": plan.targets.register_sha256,
        "target order": json.dumps(list(plan.targets.frozen_order), separators=(",", ":")),
        "scientific-code SHA-256": runner._code_sha256(),
        "scientific runtime": json.dumps(runtime, sort_keys=True, separators=(",", ":")),
        "discovery seeds": json.dumps(list(plan.seeds.discovery)),
        "confirmation seeds": json.dumps(list(plan.seeds.confirmation)),
        "generated UTC": "2026-10-15T00:00:00Z",
        "producing command": "verification fixture",
        "reviewer": "verification reviewer",
        "review date": "2026-10-15",
    }
    paths["config_registry"].write_text(
        "# Robust-NROY production configuration registry\n\nStatus: FINAL\n\n"
        "| Binding | Value |\n|---|---|\n"
        + "".join(f"| {key} | `{value}` |\n" for key, value in registry_rows.items())
    )

    raw_measurement = tmp_path / "pilot_raw_measurement.json"
    machine = {"profile": "verification-machine", "cpu": "fixture-cpu"}
    scheduler = {
        "billable_resources": 64,
        "resource_unit": "worker",
        "wall_seconds": 60,
    }
    model_elapsed = {nc.MODEL_M0: 0.01, nc.MODEL_M1: 0.02}
    model_spaces = runner._model_spaces(plan)
    config_sha = {
        model: runner._scientific_config_sha256(plan, model_spaces[model])
        for model in nc.MODELS
    }
    pilot_vector = {
        model: {
            name: float((lower + upper) / 2.0)
            for name, lower, upper in zip(
                model_spaces[model].names,
                model_spaces[model].space.lower,
                model_spaces[model].space.upper,
            )
        }
        for model in nc.MODELS
    }
    vector_sha = {
        model: nc.vector_hash(pilot_vector[model], names=model_spaces[model].names)
        for model in nc.MODELS
    }
    raw_rows = [
        {
            "trajectory_id": f"{model}-pilot-{seed}",
            "model": model,
            "seed": int(seed),
            "parameter_vector": pilot_vector[model],
            "parameter_vector_sha256": vector_sha[model],
            "scientific_config_sha256": config_sha[model],
            "status": "SUCCESS",
            "elapsed_seconds": model_elapsed[model],
            "peak_memory_bytes": 1024 if model == nc.MODEL_M0 else 2048,
        }
        for model in nc.MODELS
        for seed in plan.seeds.discovery
    ]
    raw_measurement.write_text(json.dumps({
        "schema": runner.RAW_PILOT_SCHEMA,
        "status": "FINAL",
        "plan_sha256": plan.plan_sha256,
        "scientific_code_sha256": runner._code_sha256(),
        "models": list(nc.MODELS),
        "discovery_seeds": list(plan.seeds.discovery),
        "parameter_space_sha256_by_model": {
            model: runner._pilot_parameter_space_sha256(model_spaces[model])
            for model in nc.MODELS
        },
        "machine_profile": machine,
        "scheduler_request": scheduler,
        "rows": raw_rows,
    }))
    model_summaries = {
        model: {
            "n_trajectories": len(plan.seeds.discovery),
            "trajectory_ids": [
                f"{model}-pilot-{seed}" for seed in plan.seeds.discovery
            ],
            "seeds": list(plan.seeds.discovery),
            "timing_summary": {
                "statistic": runner.PILOT_TIMING_STATISTIC,
                "n_successful_trajectories": len(plan.seeds.discovery),
                "mean_elapsed_seconds": model_elapsed[model],
                "max_elapsed_seconds": model_elapsed[model],
                "total_elapsed_seconds": (
                    model_elapsed[model] * len(plan.seeds.discovery)
                ),
            },
            "peak_memory_bytes": 1024 if model == nc.MODEL_M0 else 2048,
        }
        for model in nc.MODELS
    }
    pilot = {
        "schema": runner.MEASURED_PILOT_SCHEMA,
        "status": "FINAL",
        "production_eligible": True,
        "generated_utc": "2026-10-15T00:00:00Z",
        "command": "verification pilot fixture",
        "passed": True,
        "plan_sha256": plan.plan_sha256,
        "base_config_path": str(base),
        "base_config_sha256": ccs.sha256_file(base),
        "m0_calibration_space_sha256": ccs.sha256_file(m0_space),
        "target_register_sha256": plan.targets.register_sha256,
        "target_order": list(plan.targets.frozen_order),
        "discovery_seeds": list(plan.seeds.discovery),
        "scientific_code_sha256": runner._code_sha256(),
        "scientific_runtime": runtime,
        "machine_profile": machine,
        "scheduler_request": scheduler,
        "model_summaries": model_summaries,
        "raw_measurement_path": str(raw_measurement),
        "raw_measurement_sha256": ccs.sha256_file(raw_measurement),
    }
    paths["measured_pilot"].write_text(json.dumps(pilot))

    pricing_source = tmp_path / "pricing_source.json"
    cost_inputs = {
        "currency_or_credit_unit": "test-credit",
        "resource_unit": "worker",
        "billing_unit": "resource_hour",
        "seconds_per_billing_unit": 3600,
        "unit_price": 0.25,
    }
    pricing_source.write_text(json.dumps({
        "schema": runner.PRICING_SOURCE_SCHEMA,
        "status": "FINAL",
        "machine_profile": machine,
        "source_reference": "verification fixture price card",
        "effective_utc": "2026-10-15T00:00:00Z",
        **cost_inputs,
    }))
    max_trajectories = int(plan.require("budget.max_trajectories"))
    max_attempts = int(plan.require("budget.max_attempts"))
    wall_cap = float(plan.require("budget.wall_time_cap_seconds"))
    expected_resource_seconds = {
        model: max_trajectories * model_elapsed[model] * 64
        for model in nc.MODELS
    }
    worst_resource_seconds = wall_cap * 64
    expected_cost = {
        model: expected_resource_seconds[model] / 3600.0 * 0.25
        for model in nc.MODELS
    }
    worst_cost = worst_resource_seconds / 3600.0 * 0.25
    stage_costs = [
        {
            "model": model,
            "stage": stage,
            "max_attempts": max_attempts,
            "max_trajectories": max_trajectories,
            "wall_time_cap_seconds": wall_cap,
            "expected_resource_seconds": expected_resource_seconds[model],
            "worst_case_resource_seconds": worst_resource_seconds,
            "expected_cost": expected_cost[model],
            "worst_case_cost": worst_cost,
        }
        for model, stage in runner.PRICED_STAGE_KEYS
    ]
    priced = {
        "schema": runner.PRICED_BUDGET_SCHEMA,
        "status": "FINAL",
        "production_eligible": True,
        "generated_utc": "2026-10-15T00:00:00Z",
        "command": "verification pricing fixture",
        "approved": True,
        "approved_by": "verification approver",
        "approved_utc": "2026-10-15T00:00:00Z",
        "plan_sha256": plan.plan_sha256,
        "measured_pilot_path": str(paths["measured_pilot"]),
        "measured_pilot_sha256": ccs.sha256_file(paths["measured_pilot"]),
        "machine_profile": machine,
        "pricing_source": str(pricing_source),
        "pricing_source_sha256": ccs.sha256_file(pricing_source),
        "cap_scope": "per_stage_ledger",
        "cost_scope": "entire_workflow",
        "cost_formula": runner.PRICING_FORMULA,
        "cost_inputs": cost_inputs,
        "timing_inputs": {
            model: {
                "statistic": runner.PILOT_TIMING_STATISTIC,
                "n_successful_trajectories": len(plan.seeds.discovery),
                "expected_seconds_per_trajectory": model_elapsed[model],
                "observed_max_seconds_per_trajectory": model_elapsed[model],
            }
            for model in nc.MODELS
        },
        "stage_costs": stage_costs,
        "workflow_totals": {
            "n_priced_stages": len(stage_costs),
            "expected_cost": sum(row["expected_cost"] for row in stage_costs),
            "worst_case_cost": len(stage_costs) * worst_cost,
        },
    }
    paths["priced_budget"].write_text(json.dumps(priced))

    seed_scan_source = tmp_path / "seed_scan_source.json"
    seed_scan_source.write_text(json.dumps({"fixture": "no prior RNG values"}))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", seed_scan_source.name], cwd=tmp_path, check=True)
    (tmp_path / "outputs").mkdir(exist_ok=True)
    scope = tmp_path / "seed_scope.json"
    scope.write_text(json.dumps({
        "schema": seed_audit_tool.SCOPE_SCHEMA,
        "owner_scope": {
            "status": "APPROVED",
            "approved_by": "verification owner",
            "approved_utc": "2026-10-15T00:00:00Z",
            "scope_statement": "Every verification source is covered.",
        },
        "source_coverage": {"status": "COMPLETE"},
        "tracked_repository": {
            "pathspecs": [seed_scan_source.name],
            "text_extensions": [".json", ".md", ".py", ".txt"],
            "explicit_exclusions": [],
        },
        "external_execution_locations": [
            {
                "kind": kind,
                "status": "REVIEWED_NONE",
                "coverage_decision": "No additional source exists in this fixture.",
                "evidence": _write_typed_external_review(
                    tmp_path,
                    kind,
                    status="REVIEWED_NONE",
                    reviewer="verification owner",
                    reviewed_utc="2026-10-15T00:00:00Z",
                ),
                "reviewer": "verification owner",
                "reviewed_utc": "2026-10-15T00:00:00Z",
            }
            for kind in ("REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD")
        ],
        "outputs": {
            "root": "outputs",
            "json_basename_globs": ["*manifest*.json"],
            "json_lines_basename_globs": ["*results*.jsonl"],
            "text_extensions": [".md"],
            "max_scanned_file_bytes": 1024 * 1024,
        },
        "limitations": [],
    }))
    resolutions = tmp_path / "seed_finding_resolutions.json"
    resolutions_payload = {
        "schema": seed_audit_tool.RESOLUTION_SCHEMA,
        "status": "REVIEWED",
        "scope_manifest_sha256": ccs.sha256_file(scope),
        "exact_resolutions": [],
        "reviewed_rules": [],
        "excluded_output_dispositions": [],
        "excluded_output_index_review": None,
        "limitations": [],
    }
    resolutions.write_text(json.dumps(resolutions_payload))
    candidate_source = tmp_path / "candidate_seed_blocks.json"
    candidate_payload = {
        "schema": seed_audit_tool.CANDIDATE_SCHEMA,
        "discovery": list(plan.seeds.discovery),
        "confirmation": list(plan.seeds.confirmation),
        "proposal_rng": plan.seeds.proposal_rng,
    }
    candidate_source.write_text(json.dumps(candidate_payload))
    seed_audit = seed_audit_tool.build_audit(
        tmp_path,
        scope,
        candidate_source,
        plan_path=Path(plan.plan_path),
        resolution_path=resolutions,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert seed_audit["passed"] is True
    paths["seed_disjointness_audit"].write_text(json.dumps(seed_audit))
    return paths


def test_production_readiness_reconciles_every_evidence_template(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    _write_final_production_evidence(tmp_path, plan)
    evidence_problems = [
        problem for problem in runner._production_readiness_problems(plan)
        if not problem.startswith("the production M1 config route failed:")
    ]
    assert evidence_problems == []


def _reseal_seed_audit(path: Path, payload: dict) -> None:
    payload.pop("artifact_sha256", None)
    payload["artifact_sha256"] = seed_audit_tool.canonical_sha256(payload)
    path.write_text(json.dumps(payload))


def test_production_readiness_strictly_rejects_duplicate_seed_audit_keys(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    audit_path = paths["seed_disjointness_audit"]
    original = audit_path.read_text()
    audit_path.write_text('{"schema":"forged-duplicate",' + original.lstrip()[1:])

    assert any(
        "duplicate JSON object key" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_production_readiness_strictly_rejects_nonfinite_seed_audit_json(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    audit_path = paths["seed_disjointness_audit"]
    original = audit_path.read_text()
    audit_path.write_text('{"nonfinite":NaN,' + original.lstrip()[1:])

    assert any(
        "non-finite JSON numeric literal is forbidden" in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize(
    ("source_record", "duplicate_field", "expected_problem"),
    (
        ("scope_manifest", '"schema":"forged-duplicate",',
         "scope manifest is invalid"),
        ("finding_resolutions", '"status":"forged-duplicate",',
         "resolution document is invalid"),
        ("candidate_blocks", '"proposal_rng":999999,',
         "candidate-block file is invalid"),
    ),
)
def test_production_readiness_strictly_reloads_raw_seed_evidence(
        tmp_path, source_record, duplicate_field, expected_problem):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    audit_path = paths["seed_disjointness_audit"]
    audit = json.loads(audit_path.read_text())
    record = audit[source_record]
    source_path = Path(record["path"])
    original = source_path.read_text()
    source_path.write_text("{" + duplicate_field + original.lstrip()[1:])

    if source_record == "scope_manifest":
        record["sha256"] = ccs.sha256_file(source_path)
    else:
        record["file_sha256"] = ccs.sha256_file(source_path)
    _reseal_seed_audit(audit_path, audit)

    assert any(
        expected_problem in problem and "duplicate JSON object key" in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize(
    ("artifact", "mutation", "expected_problem"),
    (
        ("measured_pilot", lambda value: value.update(production_eligible=False),
         "not production-eligible"),
        ("measured_pilot", lambda value: value.pop("scientific_runtime"), "scientific runtime"),
        ("measured_pilot", lambda value: value.update(target_order=[]), "target order"),
        ("measured_pilot", lambda value: value.update(raw_measurement_sha256="0" * 64),
         "raw-measurement hash"),
        ("priced_budget", lambda value: value.update(approved_by=""), "approved_by"),
        ("priced_budget", lambda value: value.update(status="INCOMPLETE"), "status is not FINAL"),
        ("priced_budget", lambda value: value.update(pricing_source_sha256="0" * 64),
         "pricing-source hash"),
        ("priced_budget", lambda value: value.update(cap_scope="cumulative"),
         "per-stage ledger caps"),
        ("priced_budget", lambda value: value.update(cost_scope="single_stage"),
         "entire workflow"),
        ("seed_disjointness_audit",
         lambda value: value.update(production_eligible=False), "not production-eligible"),
        ("seed_disjointness_audit",
         lambda value: value["candidate_blocks"].update(confirmation=[]), "signed seed blocks"),
        ("seed_disjointness_audit",
         lambda value: value["scope_manifest"].update(source_coverage_status="UNRESOLVED"),
         "source coverage"),
    ),
)
def test_production_readiness_fails_on_load_bearing_evidence_drift(
        tmp_path, artifact, mutation, expected_problem):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    payload = json.loads(paths[artifact].read_text())
    mutation(payload)
    paths[artifact].write_text(json.dumps(payload))
    assert any(
        expected_problem in problem for problem in runner._production_readiness_problems(plan)
    )


def test_production_readiness_rejects_self_consistent_forged_seed_audit(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    audit_path = paths["seed_disjointness_audit"]
    forged = json.loads(audit_path.read_text())
    forged["source_inventory"]["inventory_sha256"] = "f" * 64
    _reseal_seed_audit(audit_path, forged)

    assert any(
        "differs from a deterministic rebuild" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def _mutate_raw_pilot(paths, mutation) -> None:
    """Change raw rows while keeping both enclosing hashes internally current."""
    pilot = json.loads(paths["measured_pilot"].read_text())
    raw_path = Path(pilot["raw_measurement_path"])
    raw = json.loads(raw_path.read_text())
    mutation(raw)
    raw_path.write_text(json.dumps(raw))
    pilot["raw_measurement_sha256"] = ccs.sha256_file(raw_path)
    paths["measured_pilot"].write_text(json.dumps(pilot))
    priced = json.loads(paths["priced_budget"].read_text())
    priced["measured_pilot_sha256"] = ccs.sha256_file(paths["measured_pilot"])
    paths["priced_budget"].write_text(json.dumps(priced))


def test_measured_pilot_rejects_hash_consistent_tampered_raw_timing(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths, lambda raw: raw["rows"][0].update(elapsed_seconds=9.0)
    )
    assert any(
        "M0 mean_elapsed_seconds does not match its raw rows" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_rejects_authored_summary_mismatch(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    pilot = json.loads(paths["measured_pilot"].read_text())
    pilot["model_summaries"]["M0"]["timing_summary"][
        "mean_elapsed_seconds"
    ] = 99.0
    paths["measured_pilot"].write_text(json.dumps(pilot))
    assert any(
        "M0 mean_elapsed_seconds does not match its raw rows" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_rejects_duplicate_raw_trajectory_rows(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths,
        lambda raw: raw["rows"][1].update(
            trajectory_id=raw["rows"][0]["trajectory_id"]
        ),
    )
    assert any(
        "duplicate trajectory ids" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_rejects_failed_raw_trajectory(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths, lambda raw: raw["rows"][1].update(status="FAILED")
    )
    assert any(
        "failed trajectory" in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        (
            lambda raw: raw["rows"][0].update(seed=-1),
            "exact uint32 signed seed",
        ),
        (
            lambda raw: raw["rows"][0].update(
                seed=str(raw["rows"][0]["seed"]),
            ),
            "exact uint32 signed seed",
        ),
        (
            lambda raw: raw["rows"][0].update(scientific_config_sha256="0" * 64),
            "matching scientific-config SHA256",
        ),
        (
            lambda raw: raw["rows"][0]["parameter_vector"].update(
                {
                    next(iter(raw["rows"][0]["parameter_vector"])):
                    next(iter(raw["rows"][0]["parameter_vector"].values())) + 0.01,
                }
            ),
            "complete finite parameter vector with matching SHA256",
        ),
        (
            lambda raw: raw["rows"][1].update(
                seed=raw["rows"][0]["seed"],
                parameter_vector_sha256=raw["rows"][0]["parameter_vector_sha256"],
            ),
            "repeats a vector/seed trajectory",
        ),
    ),
)
def test_measured_pilot_rejects_anonymous_or_repeated_timing_rows(
        tmp_path, mutation, expected_problem):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(paths, mutation)
    assert any(
        expected_problem in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize("model_name", nc.MODELS)
def test_measured_pilot_rejects_hash_consistent_out_of_bounds_vector(
        tmp_path, model_name):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    spaces = runner._model_spaces(plan)

    def move_vector_outside_signed_space(raw):
        row = next(row for row in raw["rows"] if row["model"] == model_name)
        model = spaces[row["model"]]
        name = model.names[0]
        upper = float(model.space.upper[0])
        row["parameter_vector"][name] = upper + max(abs(upper), 1.0)
        row["parameter_vector_sha256"] = nc.vector_hash(
            row["parameter_vector"], names=model.names
        )

    # This reseals the raw file, measured-pilot hash and priced-budget pilot
    # hash, so rejection cannot be attributed to stale integrity metadata.
    _mutate_raw_pilot(paths, move_vector_outside_signed_space)
    assert any(
        "outside the exact signed model-specific space bounds" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_rejects_hash_consistent_joint_constraint_violation(
    tmp_path,
):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    model = runner._model_spaces(plan)[nc.MODEL_M0]

    def violate_declared_joint_constraint(raw):
        row = next(row for row in raw["rows"] if row["model"] == nc.MODEL_M0)
        row["parameter_vector"]["seed_msm_aids_share"] = 0.08
        row["parameter_vector"]["seed_non_msm_aids_share"] = 0.0
        values = model.space.as_vector(row["parameter_vector"])
        assert any(
            "violates declared draw constraints" in reason
            for reason in ccs.invalid_reasons(model.space, values)
        )
        row["parameter_vector_sha256"] = nc.vector_hash(
            row["parameter_vector"], names=model.names
        )

    # All enclosing raw/pilot/pricing hashes are recomputed by this helper.
    _mutate_raw_pilot(paths, violate_declared_joint_constraint)
    assert any(
        "violate its declared joint constraints" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_rejects_parameter_space_identity_drift(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths,
        lambda raw: raw["parameter_space_sha256_by_model"].update(M0="0" * 64),
    )
    assert any(
        "not bound to the exact signed M0/M1 parameter spaces" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_measured_pilot_invalid_scheduler_fails_closed_without_crashing(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    pilot = json.loads(paths["measured_pilot"].read_text())
    pilot["scheduler_request"] = {
        "billable_resources": "64",
        "resource_unit": "worker",
    }
    raw_path = Path(pilot["raw_measurement_path"])
    raw = json.loads(raw_path.read_text())
    raw["scheduler_request"] = pilot["scheduler_request"]
    raw_path.write_text(json.dumps(raw))
    pilot["raw_measurement_sha256"] = ccs.sha256_file(raw_path)
    paths["measured_pilot"].write_text(json.dumps(pilot))
    problems = runner._production_readiness_problems(plan)
    assert any("positive uint32 billable_resources" in problem for problem in problems)


def _inject_duplicate_schema_key(path: Path) -> None:
    text = path.read_text()
    assert text.startswith("{")
    path.write_text('{"schema":"duplicate",' + text[1:])


@pytest.mark.parametrize(
    ("artifact", "expected_problem"),
    (
        ("measured_pilot", "production prerequisite 'measured_pilot' is not strict JSON"),
        ("priced_budget", "production prerequisite 'priced_budget' is not strict JSON"),
        ("raw_measurement", "raw measurement is not strict JSON"),
        ("pricing_source", "pricing source is not strict JSON"),
    ),
)
def test_new_pilot_and_pricing_artifacts_reject_duplicate_json_keys(
        tmp_path, artifact, expected_problem):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    pilot = json.loads(paths["measured_pilot"].read_text())
    priced = json.loads(paths["priced_budget"].read_text())
    targets = {
        "measured_pilot": paths["measured_pilot"],
        "priced_budget": paths["priced_budget"],
        "raw_measurement": Path(pilot["raw_measurement_path"]),
        "pricing_source": Path(priced["pricing_source"]),
    }
    _inject_duplicate_schema_key(targets[artifact])
    if artifact == "raw_measurement":
        pilot["raw_measurement_sha256"] = ccs.sha256_file(targets[artifact])
        paths["measured_pilot"].write_text(json.dumps(pilot))
        priced["measured_pilot_sha256"] = ccs.sha256_file(paths["measured_pilot"])
        paths["priced_budget"].write_text(json.dumps(priced))
    elif artifact == "pricing_source":
        priced["pricing_source_sha256"] = ccs.sha256_file(targets[artifact])
        paths["priced_budget"].write_text(json.dumps(priced))
    assert any(
        expected_problem in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_raw_pilot_rejects_nonstandard_nan_even_when_hash_is_current(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths, lambda raw: raw["rows"][0].update(elapsed_seconds=float("nan"))
    )
    assert any(
        "non-finite JSON token 'NaN'" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_raw_pilot_rejects_integer_too_large_for_finite_timing(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths, lambda raw: raw["rows"][0].update(elapsed_seconds=10 ** 400)
    )
    assert any(
        "positive finite elapsed_seconds" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_strict_json_rejects_finite_syntax_that_overflows_binary_float(tmp_path):
    artifact = tmp_path / "overflow.json"
    artifact.write_text('{"elapsed_seconds":1e999}')
    with pytest.raises(ContractError, match="non-finite JSON number"):
        runner._load_strict_json(artifact)


def test_pricing_fails_if_expected_runtime_exceeds_signed_wall_cap(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    _mutate_raw_pilot(
        paths,
        lambda raw: raw["rows"][0].update(
            elapsed_seconds=float(plan.require("budget.wall_time_cap_seconds")) * 10.0,
        ),
    )
    assert any(
        "wall-time cap cannot fund the expected full-capacity workload" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_pricing_uses_model_specific_means_and_wall_cap_as_hard_ceiling(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    priced = json.loads(paths["priced_budget"].read_text())
    rows = {(row["model"], row["stage"]): row for row in priced["stage_costs"]}
    max_trajectories = int(plan.require("budget.max_trajectories"))
    wall_cap = float(plan.require("budget.wall_time_cap_seconds"))
    resources = 64
    assert rows[(nc.MODEL_M0, nc.STAGE_DISCOVERY)][
        "expected_resource_seconds"
    ] == pytest.approx(max_trajectories * 0.01 * resources)
    assert rows[(nc.MODEL_M1, nc.STAGE_DISCOVERY)][
        "expected_resource_seconds"
    ] == pytest.approx(max_trajectories * 0.02 * resources)
    assert all(
        row["worst_case_resource_seconds"] == pytest.approx(wall_cap * resources)
        for row in rows.values()
    )


def test_pricing_rejects_using_m0_timing_for_an_m1_stage(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    priced = json.loads(paths["priced_budget"].read_text())
    m0_expected = next(
        row["expected_resource_seconds"] for row in priced["stage_costs"]
        if row["model"] == nc.MODEL_M0
    )
    m1_row = next(
        row for row in priced["stage_costs"] if row["model"] == nc.MODEL_M1
    )
    m1_row["expected_resource_seconds"] = m0_expected
    paths["priced_budget"].write_text(json.dumps(priced))
    assert any(
        "M1/discovery expected_resource_seconds is not recomputable" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_pricing_requires_exact_six_simulator_stage_rows(tmp_path):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    priced = json.loads(paths["priced_budget"].read_text())
    priced["stage_costs"].pop()
    paths["priced_budget"].write_text(json.dumps(priced))
    assert any(
        "stage rows must cover each M0/M1 discovery, confirmation and bridges "
        "stage exactly once" in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize(
    ("mutation", "expected_problem"),
    (
        (
            lambda priced: priced["workflow_totals"].update(expected_cost=12345.0),
            "entire-workflow expected cost is not recomputable",
        ),
        (
            lambda priced: priced.update(cost_formula="multiply whatever the reviewer wrote"),
            "unknown or arbitrary cost formula",
        ),
        (
            lambda priced: priced["stage_costs"][0].update(max_attempts=1),
            "does not match the signed per-stage caps",
        ),
        (
            lambda priced: priced["stage_costs"][0].update(expected_cost=12345.0),
            "expected_cost is not recomputable",
        ),
    ),
)
def test_priced_budget_rejects_authored_totals_formulas_and_cap_drift(
        tmp_path, mutation, expected_problem):
    plan = nc.FrozenPlan.load(fx.write_signed_plan(tmp_path), root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    priced = json.loads(paths["priced_budget"].read_text())
    mutation(priced)
    paths["priced_budget"].write_text(json.dumps(priced))
    assert any(
        expected_problem in problem
        for problem in runner._production_readiness_problems(plan)
    )


@pytest.mark.parametrize(
    ("artifact", "mutation", "expected_problem"),
    (
        (
            "identity_gate",
            lambda value: value["route_evidence"].update(plan_sha256="0" * 64),
            "not bound to this signed run plan",
        ),
        (
            "identity_gate",
            lambda value: value.update(environment={}),
            "different scientific runtime",
        ),
        (
            "m1_anchor_confirmation",
            lambda value: value.update(schema="robust_nroy_m1_anchor_confirmation/1.0.0"),
            "schema does not match",
        ),
        (
            "m1_anchor_confirmation",
            lambda value: value.update(scientific_code_sha256="0" * 64),
            "scientific_code_sha256 does not match",
        ),
    ),
)
def test_production_readiness_validates_signed_m1_prerequisite_bindings(
        tmp_path, artifact, mutation, expected_problem):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    payload = json.loads(paths[artifact].read_text())
    mutation(payload)
    paths[artifact].write_text(json.dumps(payload))
    assert any(
        expected_problem in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_production_readiness_requires_both_signed_m1_prerequisites(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    paths["identity_gate"].unlink()
    paths["m1_anchor_confirmation"].unlink()
    problems = runner._production_readiness_problems(plan)
    assert "production prerequisite 'identity_gate' is missing" in problems
    assert "production prerequisite 'm1_anchor_confirmation' is missing" in problems


def test_production_readiness_rejects_registry_field_omission(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)
    text = paths["config_registry"].read_text()
    paths["config_registry"].write_text(
        "\n".join(line for line in text.splitlines() if "scientific runtime" not in line)
    )
    assert any(
        "scientific runtime" in problem
        for problem in runner._production_readiness_problems(plan)
    )


def test_scanner_generated_final_seed_audit_is_accepted_without_hand_editing(
        tmp_path, monkeypatch):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    paths = _write_final_production_evidence(tmp_path, plan)

    prior = tmp_path / "prior_seed_source.json"
    prior.write_text(json.dumps({"seeds": {"simulation_seeds": [1, 2, 3]}}))
    (tmp_path / "outputs").mkdir(exist_ok=True)
    scope = tmp_path / "approved_seed_scope.json"
    scope.write_text(json.dumps({
        "schema": seed_audit_tool.SCOPE_SCHEMA,
        "owner_scope": {
            "status": "APPROVED",
            "approved_by": "verification owner",
            "approved_utc": "2026-10-15T00:00:00Z",
            "scope_statement": "Every test seed source is covered.",
        },
        "source_coverage": {"status": "COMPLETE"},
        "tracked_repository": {
            "pathspecs": ["prior_seed_source.json"],
            "text_extensions": [".md", ".py", ".txt"],
            "explicit_exclusions": [],
        },
        "external_execution_locations": [
            {
                "kind": kind,
                "status": "REVIEWED_NONE",
                "coverage_decision": "Covered by the isolated verification fixture.",
                "evidence": _write_typed_external_review(
                    tmp_path,
                    kind,
                    status="REVIEWED_NONE",
                    reviewer="verification owner",
                    reviewed_utc="2026-10-15T00:00:00Z",
                ),
                "reviewer": "verification owner",
                "reviewed_utc": "2026-10-15T00:00:00Z",
            }
            for kind in ("REMOTE_HPC", "CLOUD", "DELETED_LOCAL", "COLLABORATOR_HELD")
        ],
        "outputs": {
            "root": "outputs",
            "json_basename_globs": ["*manifest*.json"],
            "json_lines_basename_globs": ["*results*.jsonl"],
            "text_extensions": [".md"],
            "max_scanned_file_bytes": 1024 * 1024,
        },
        "evidence_example_limit": 20,
        "parsed_seed_source_limit": 10,
        "limitations": [],
    }))
    candidate_blocks = tmp_path / "candidate_seed_blocks.json"
    candidate_blocks.write_text(json.dumps({
        "schema": seed_audit_tool.CANDIDATE_SCHEMA,
        "discovery": list(plan.seeds.discovery),
        "confirmation": list(plan.seeds.confirmation),
        "proposal_rng": plan.seeds.proposal_rng,
    }))
    resolutions = tmp_path / "reviewed_seed_resolutions.json"
    resolutions.write_text(json.dumps({
        "schema": seed_audit_tool.RESOLUTION_SCHEMA,
        "status": "REVIEWED",
        "scope_manifest_sha256": ccs.sha256_file(scope),
        "exact_resolutions": [],
        "reviewed_rules": [],
        "excluded_output_dispositions": [],
        "excluded_output_index_review": None,
        "limitations": [],
    }))
    monkeypatch.setattr(
        seed_audit_tool, "git_tracked_paths", lambda _root, _pathspecs: [prior]
    )
    generated = seed_audit_tool.build_audit(
        tmp_path,
        scope,
        candidate_blocks,
        plan_path=plan_path,
        resolution_path=resolutions,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert generated["status"] == "FINAL"
    assert generated["production_eligible"] is True
    assert generated["passed"] is True

    mismatched_blocks = json.loads(candidate_blocks.read_text())
    mismatched_blocks["confirmation"][0] += 1_000_000
    mismatched_path = tmp_path / "mismatched_candidate_seed_blocks.json"
    mismatched_path.write_text(json.dumps(mismatched_blocks))
    mismatched = seed_audit_tool.build_audit(
        tmp_path,
        scope,
        mismatched_path,
        plan_path=plan_path,
        resolution_path=resolutions,
        generated_utc="2026-10-15T00:00:00Z",
    )
    assert mismatched["status"] == "INCOMPLETE"
    assert mismatched["production_eligible"] is False
    assert mismatched["passed"] is False
    assert any("do not exactly match" in item for item in mismatched["unresolved"])

    paths["seed_disjointness_audit"].write_text(json.dumps(generated))

    evidence_problems = [
        problem for problem in runner._production_readiness_problems(plan)
        if not problem.startswith("the production M1 config route failed:")
    ]
    assert evidence_problems == []


@pytest.mark.skipif(
    not EXTERNAL_ACCEPTANCE_INPUTS_PRESENT,
    reason=EXTERNAL_ACCEPTANCE_INPUTS_REASON,
)
def test_validate_subcommand_rejects_signed_plan_with_placeholder_readiness_evidence(
    tmp_path, capsys,
):
    plan_path = fx.write_signed_plan(tmp_path)
    code = runner.main(["validate", "--plan", str(plan_path)])
    out = capsys.readouterr().out
    assert code == 3
    assert "All required decisions are signed" in out
    assert "not production-ready" in out
    assert "placeholder/fixture evidence" in out
    assert "no current evidence schema" in out
    # The shared-config fixture now exercises a valid explicit M0/M1 route.
    # Readiness still fails on the deliberately placeholder external evidence,
    # not on a config defect that production no longer carries.
    assert "production M1 config route failed" not in out


def test_acceptance_validation_rejects_a_self_consistent_wrong_workbook(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan_raw = nc.strict_json_load(plan_path, what="signed test plan")
    register_path = Path(plan_raw["target_contract"]["register_path"])
    register = nc.strict_json_load(register_path, what="test target register")
    wrong_workbook = tmp_path / "acceptance_inputs" / "fixture_source_workbook.xlsx"
    register["source_workbook"] = str(wrong_workbook)
    register["source_workbook_sha256"] = "0" * 64
    register_path.write_text(json.dumps(register))
    plan_raw["target_contract"]["register_sha256"] = ccs.sha256_file(register_path)
    plan_path.write_text(json.dumps(plan_raw, indent=1))
    fx._write_test_acceptance_package(
        tmp_path / "acceptance_inputs",
        plan_path=plan_path,
        observed=None,
        variance=None,
    )

    with pd.ExcelWriter(wrong_workbook) as writer:
        pd.DataFrame({"Year": [2024], "Value": [1]}).to_excel(
            writer, sheet_name="S", index=False
        )
    provenance_path = tmp_path / "acceptance_inputs" / nai.PROVENANCE_FILENAME
    provenance = nc.strict_json_load(provenance_path, what="acceptance provenance")
    provenance["source_bindings"]["source_workbook"] = {
        "path": str(wrong_workbook),
        "sha256": ccs.sha256_file(wrong_workbook),
        "expected_sha256": "0" * 64,
    }
    provenance_path.write_text(json.dumps(provenance, indent=1))

    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    with pytest.raises(ContractError, match="plan-bound target-register SHA-256"):
        nai.validate_package(
            plan,
            provenance_path,
            root=ROOT,
            scientific_code_sha256=runner._code_sha256(),
            scientific_runtime=nig.scientific_runtime_fingerprint(),
        )


def test_prepare_refuses_an_unsigned_plan(tmp_path):
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    code = runner.main([
        "prepare", "--plan", str(fx.PRODUCTION_PLAN), "--out", str(tmp_path / "stage"),
        "--anchors", str(anchors),
    ])
    assert code == 2
    assert not (tmp_path / "stage" / "manifest.json").exists()


def _unit(**overrides) -> ne.WorkUnit:
    fields = dict(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id="D", seed=1,
        vector_hash="h", kernel=nc.KERNEL_AMBIENT, parent_ids=("A",), params={},
        parent_manifest_sha256="p", code_sha256="c", config_sha256="g",
        target_schema=TARGETS,
    )
    fields.update(overrides)
    return ne.WorkUnit(**fields)


def test_the_production_evaluator_stub_cannot_be_called_directly():
    """The module-level callable is a refusal; the adapter is behind the gate."""
    with pytest.raises(ContractError, match="gate was bypassed"):
        runner.production_evaluator(_unit(), {}, TARGETS)


def test_production_is_refused_without_the_priced_budget_acknowledgement(tmp_path):
    plan = fx.signed_plan(tmp_path)
    ledger = ne.Ledger(stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, units=(_unit(),))
    with pytest.raises(ContractError, match="never refunded"):
        runner.resolve_evaluator("production", plan, priced=False, ledger=ledger)
    # The surrogate is unaffected and runs no engine.
    assert runner.resolve_evaluator(
        "surrogate", plan, priced=False, ledger=ledger
    ) is runner.surrogate_evaluator


def test_production_is_refused_when_the_frozen_work_has_no_validated_parent(tmp_path):
    """A stage after discovery must carry its parent-manifest hash."""
    plan = fx.signed_plan(tmp_path)
    orphan = _unit(stage=nc.STAGE_CONFIRMATION, parent_manifest_sha256="")
    ledger = ne.Ledger(stage=nc.STAGE_CONFIRMATION, model=nc.MODEL_M0, units=(orphan,))
    with pytest.raises(ContractError, match="parent artifacts were never validated"):
        runner.resolve_evaluator("production", plan, priced=True, ledger=ledger)


def test_there_is_no_default_route_to_the_production_evaluator():
    """``--evaluator`` is required, so omission cannot reach the simulator."""
    parser = runner.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["evaluate", "--stage-dir", "d", "--shard", "0", "--of", "1"])


def test_anchor_table_must_hold_complete_joint_vectors(tmp_path):
    space = fx.m0_space()
    path = _write_anchors(tmp_path / "anchors.csv", space)
    frame = pd.read_csv(path).drop(columns=[space.names[0]])
    frame.to_csv(path, index=False)
    with pytest.raises(ContractError, match="missing parameter column"):
        runner._load_anchors(path, space)


def _run_pipeline(tmp_path, n_shards, *, model=nc.MODEL_M0, tag="", overrides=None):
    """prepare -> evaluate (n shards) -> merge -> report, via the CLI.

    Each call gets its own stage directory: re-preparing into a finished one is
    correctly refused, because completed artifacts are immutable.
    """
    plan_path = fx.write_signed_plan(tmp_path, **(overrides or {}))
    space = fx.m1_space() if model == nc.MODEL_M1 else fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    stage_dir = tmp_path / f"stage_{model}_{n_shards}{tag}"
    argv = [
        "prepare", "--plan", str(plan_path), "--out", str(stage_dir),
        "--anchors", str(anchors), "--model", model,
    ]
    if model == nc.MODEL_M1:
        argv += ["--identity-gate", str(fx.write_identity_gate(tmp_path))]
    assert runner.main(argv) == 0
    for shard in range(n_shards):
        assert runner.main([
            "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
            "--shard", str(shard), "--of", str(n_shards), "--evaluator", "surrogate",
        ]) == 0

    observed, base_variance = fx.observed_and_variance(TARGETS)
    (tmp_path / "observed.json").write_text(json.dumps(observed))
    (tmp_path / "variance.json").write_text(json.dumps(base_variance))
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--observed", str(tmp_path / "observed.json"),
        "--base-variance", str(tmp_path / "variance.json"),
    ]) == 0
    assert runner.main([
        "report", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
    ]) == 0
    return stage_dir


def test_end_to_end_pipeline_runs_without_any_engine(tmp_path):
    stage_dir = _run_pipeline(tmp_path, 3)
    for name in (
        "manifest.json", "artifacts.json", "ledger_units.csv", "ledger_attempts.csv",
        "design_proposals.csv", "design_vectors.csv", "seed_results.csv",
        "candidate_scores.csv",
    ):
        assert (stage_dir / name).exists(), f"missing {name}"
    package = stage_dir / "package"
    for name in (
        "EXECUTIVE_SUMMARY.md", "REGION_REPORT.md", "RUN_AUDIT.md", "TABLE_INDEX.md",
        "data_dictionary.json", "summary_facts.json",
    ):
        assert (package / name).exists(), f"missing {name}"
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    assert manifest["status"] == nc.STATUS_COMPLETE
    assert manifest["parentable"] is True


@pytest.mark.parametrize("n_shards", [1, 2, 5])
def test_end_to_end_merged_table_is_identical_across_worker_counts(tmp_path, n_shards):
    reference_dir = _run_pipeline(tmp_path, 1, tag="_ref")
    candidate_dir = _run_pipeline(tmp_path, n_shards, tag="_cand")
    reference = ne.read_frame_exact(reference_dir / "seed_results.csv")
    merged = ne.read_frame_exact(candidate_dir / "seed_results.csv")
    pd.testing.assert_frame_equal(
        merged.drop(columns=["attempt_id"]), reference.drop(columns=["attempt_id"])
    )
    pd.testing.assert_frame_equal(
        ne.read_frame_exact(candidate_dir / "candidate_scores.csv"),
        ne.read_frame_exact(reference_dir / "candidate_scores.csv"),
    )


def test_durable_results_survive_loss_of_the_end_of_worker_shard_snapshot(tmp_path):
    stage_dir = _run_pipeline(tmp_path, 1)
    expected = ne.read_frame_exact(stage_dir / "seed_results.csv")
    for path in (stage_dir / "shards").glob("shard_*.csv"):
        path.unlink()
    recovered = runner._read_shards(stage_dir, fx.signed_plan(tmp_path).targets)
    actual = pd.DataFrame([row.as_row() for row in recovered]).sort_values(
        ["stage", "model", "design_id", "seed"]
    ).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        actual[expected.columns], expected.reset_index(drop=True)
    )


def test_m1_discovery_refuses_caller_selected_anchors(tmp_path):
    """M1 must consume the automatic exact-p=0 M0 embedding handoff."""
    plan_path = fx.write_signed_plan(
        tmp_path, **{"p_parameter.extra_probe_values": [1e-18]}
    )
    anchors = _write_anchors(tmp_path / "anchors.csv", fx.m1_space())
    code = runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "m1"),
        "--anchors", str(anchors), "--model", nc.MODEL_M1,
        "--identity-gate", str(fx.write_identity_gate(tmp_path)),
    ])
    assert code == 1


def test_evaluate_refuses_a_mutated_design_vector(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    stage_dir = tmp_path / "stage"
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(stage_dir),
        "--anchors", str(anchors),
    ]) == 0
    vectors = pd.read_csv(stage_dir / "design_vectors.csv")
    vectors.loc[0, space.names[0]] = float(vectors.loc[0, space.names[0]]) * 1.5
    vectors.to_csv(stage_dir / "design_vectors.csv", index=False)
    code = runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
    ])
    assert code == 1, "a mutated design must be refused, not evaluated"


def test_artifact_index_hashes_every_written_file(tmp_path):
    stage_dir = _run_pipeline(tmp_path, 2)
    index = json.loads((stage_dir / "artifacts.json").read_text())
    assert index["files"]
    for entry in index["files"]:
        path = stage_dir / entry["path"]
        assert path.exists()
        assert ccs.sha256_file(path) == entry["sha256"]


# ==========================================================================
# The fixed figure set (spec sec. 8)
# ==========================================================================

def test_the_fixed_figure_set_is_produced_or_explains_each_skip(tmp_path):
    tables = sample_tables()
    scores = tables["candidate_scores"].copy()
    for name in TARGETS:
        scores[f"mean:{name}"] = np.linspace(100.0, 110.0, len(scores))
    tables["candidate_scores"] = scores
    parameters = pd.DataFrame([
        {"candidate_id": c, "x": i * 0.1, "y": 0.5 - i * 0.05, "z": 0.2}
        for i, c in enumerate(["A0", "A1", "A2", "B0"])
    ])
    results = nrp.write_figures(
        tmp_path, tables, parameter_frame=parameters,
        parameter_names=["x", "y", "z"], target_order=TARGETS,
    )
    assert set(results) == set(nrp.FIGURE_SET), "the figure set must be fixed"
    for name, value in results.items():
        if value.startswith("skipped:"):
            assert len(value) > len("skipped:"), f"{name} skipped without a reason"
        else:
            assert Path(value).exists() and Path(value).stat().st_size > 0


def test_figures_are_skipped_with_a_reason_when_nothing_ran(tmp_path):
    """An empty panel must never read as a finding of 'nothing there'."""
    results = nrp.write_figures(tmp_path, {})
    assert set(results) == set(nrp.FIGURE_SET)
    assert all(v.startswith("skipped:") for v in results.values())
    assert any(nc.EVIDENCE_NOT_RUN in v for v in results.values())


def test_the_package_records_the_figure_outcome(tmp_path):
    tables = sample_tables()
    facts = nrp.summary_facts(tables, status=nc.STATUS_COMPLETE, model=nc.MODEL_M1)
    manifest = {"stage": nc.STAGE_REGIONS, "model": nc.MODEL_M1,
                "status": nc.STATUS_COMPLETE, "parentable": True, "contract": {}}
    nrp.write_package(tmp_path, tables, facts=facts, manifest=manifest, question="Q")
    payload = json.loads((tmp_path / "figures.json").read_text())
    assert payload["figure_set"] == list(nrp.FIGURE_SET)
    assert set(payload["results"]) == set(nrp.FIGURE_SET)


# ==========================================================================
# Fail-closed hardening (regressions found by adversarial probing)
# ==========================================================================

def test_no_module_reads_a_proposal_field_as_a_value():
    """A ``_proposal`` is a record of what the spec suggested, never an input.

    The one legitimate read is in ``nroy_contract.unsigned_values``, which
    quotes the proposal back in the fail-closed error message so the sign-off
    sheet can be filled in. Anything else would be a silent default for an
    unsigned scientific value.
    """
    import re
    allowed = {"nroy_contract.py"}
    offenders = []
    for name in (
        "nroy_contract.py", "nroy_design.py", "nroy_execution.py",
        "nroy_graph.py", "nroy_report.py", "run_robust_nroy_freeze.py",
    ):
        text = (ROOT / name).read_text()
        for match in re.finditer(r'(?:require|get)\(\s*f?"[^"]*_proposal"', text):
            if name in allowed and "unsigned_values" in text[: match.start()][-800:]:
                continue
            offenders.append(f"{name}: {match.group(0)}")
    assert not offenders, f"these read a _proposal as a value: {offenders}"


def _consumption_blob() -> str:
    """Module source with the REQUIRED_DECISIONS literal removed.

    Searching the dict that *declares* the decisions would make any
    consumption test vacuous -- every path trivially appears in its own
    declaration. This strips that literal so the test measures real reads.
    """
    sources = {}
    for name in (
        "nroy_contract", "nroy_design", "nroy_execution",
        "nroy_graph", "nroy_report", "nroy_assess", "run_robust_nroy_freeze",
    ):
        sources[name] = (ROOT / f"{name}.py").read_text()
    contract = sources["nroy_contract"]
    # Strip every literal that merely NAMES decisions, at any level: the
    # declaration dicts and the bookkeeping sets. Leaving any of them in makes
    # the test vacuous, because each path trivially appears in its own listing.
    for opening, closing in (
        ("REQUIRED_DECISIONS: dict[str, str] = {", "\n}\n"),
        ("DECISIONS_READ_BY_SUBSCRIPT = frozenset({", "\n})\n"),
        ("CONDITIONAL_DECISIONS: dict[str, tuple[str, str]] = {", "\n}\n"),
    ):
        start = contract.index(opening)
        end = contract.index(closing, start) + len(closing)
        contract = contract[:start] + contract[end:]
    sources["nroy_contract"] = contract
    return "".join(sources.values())


def test_every_signed_decision_is_either_consumed_or_declared_unwired():
    """No required decision may be silently decorative.

    A signed value that no code reads is worse than an honest default: the
    sign-off sheet implies a decision is being honoured when it is not. Each
    decision must therefore be genuinely read, read by subscript in a
    ``from_plan`` constructor, or explicitly listed as pending wiring.
    """
    blob = _consumption_blob()
    declared = set(nc.REQUIRED_DECISIONS) | set(nc.CONDITIONAL_DECISIONS)
    unread = {
        path for path in declared if f'"{path}"' not in blob
    } - nc.DECISIONS_READ_BY_SUBSCRIPT
    assert unread <= nc.DECISIONS_PENDING_WIRING, (
        "these required decisions are silently decorative -- no code reads them and "
        f"they are not declared as pending wiring: {sorted(unread - nc.DECISIONS_PENDING_WIRING)}"
    )


def test_the_pending_wiring_set_is_empty_and_cannot_rot():
    """Once a stage is wired, its decision must leave DECISIONS_PENDING_WIRING.

    Without this, the set becomes a permanent excuse: a decision could be
    wired up and still be advertised as unwired, or vice versa.

    It is now empty, which is the stronger statement: every required decision
    of the base stage has a real consumer.  Re-emptying it is not automatic --
    an entry added later must still be genuinely unconsumed.
    """
    blob = _consumption_blob()
    now_consumed = sorted(
        path for path in nc.DECISIONS_PENDING_WIRING if f'"{path}"' in blob
    )
    assert not now_consumed, (
        "these are now read by code and must be removed from "
        f"DECISIONS_PENDING_WIRING: {now_consumed}"
    )
    unknown = sorted(
        nc.DECISIONS_PENDING_WIRING
        - set(nc.REQUIRED_DECISIONS) - set(nc.CONDITIONAL_DECISIONS)
    )
    assert not unknown, f"pending-wiring entries that are not required decisions: {unknown}"
    assert nc.DECISIONS_PENDING_WIRING == frozenset(), (
        "the base stage is fully wired; if a decision genuinely has no consumer again, "
        "add it here deliberately and say so in the inventory"
    )


def test_conditional_decisions_are_required_exactly_when_switched_on(tmp_path):
    """Off: not required.  On: required, and the stage will not run on defaults."""
    for path, (switch, _) in nc.CONDITIONAL_DECISIONS.items():
        assert path not in nc.REQUIRED_DECISIONS, (
            f"{path} is both unconditionally and conditionally required"
        )
        # The shipped plan has the switch off, so the value is not demanded...
        off = {item.path for item in nc.unsigned_values(fx.signed_plan_dict())}
        assert path not in off, f"{path} is demanded while {switch} is false"
        # ...and turning it on demands it.
        on = {
            item.path
            for item in nc.unsigned_values(fx.signed_plan_dict(**{switch: True}))
        }
        assert path in on, f"{path} is not demanded when {switch} is true"
    # And the loader refuses the enabled-but-unsigned plan outright.
    if nc.CONDITIONAL_DECISIONS:
        switch = next(iter(nc.CONDITIONAL_DECISIONS.values()))[0]
        with pytest.raises(nc.UnsignedConfigError):
            fx.signed_plan(tmp_path, **{switch: True})


def test_subscript_read_decisions_really_are_read():
    """Guard the other escape hatch: these must appear as a leaf-key read."""
    blob = _consumption_blob()
    for path in nc.DECISIONS_READ_BY_SUBSCRIPT:
        leaf = path.split(".")[-1]
        assert f'"{leaf}"' in blob, (
            f"{path} is listed as read by subscript but {leaf!r} never appears"
        )


@pytest.mark.parametrize("command", ["merge", "report", "resume", "evaluate"])
def test_subcommands_refuse_an_unprepared_stage_directory(tmp_path, command, capsys):
    """A missing stage directory is a clean refusal, not a traceback."""
    plan_path = fx.write_signed_plan(tmp_path)
    argv = [command, "--plan", str(plan_path), "--stage-dir", str(tmp_path / "never_made")]
    if command == "evaluate":
        argv += ["--shard", "0", "--of", "1", "--evaluator", "surrogate"]
    code = runner.main(argv)
    assert code == 1
    assert "CONTRACT ERROR" in capsys.readouterr().err


def test_a_directory_without_a_manifest_is_not_a_prepared_stage(tmp_path, capsys):
    plan_path = fx.write_signed_plan(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    code = runner.main(["resume", "--plan", str(plan_path), "--stage-dir", str(empty)])
    assert code == 1
    assert "not a prepared stage" in capsys.readouterr().err


def test_the_redraw_budget_must_be_signed(tmp_path):
    """It decides when a kernel fails preparation, so it cannot have a default."""
    assert "ambient.max_attempts_per_anchor" in nc.REQUIRED_DECISIONS
    with pytest.raises(nc.UnsignedConfigError):
        fx.signed_plan(tmp_path, **{"ambient.max_attempts_per_anchor": "UNSIGNED"})


def test_the_p_config_path_must_be_signed(tmp_path):
    """An unlisted disease-level block is silently discarded by config_reader."""
    assert "p_parameter.config_path" in nc.REQUIRED_DECISIONS
    with pytest.raises(nc.UnsignedConfigError):
        fx.signed_plan(tmp_path, **{"p_parameter.config_path": "UNSIGNED"})


def test_merge_uses_the_acceptance_inputs_sealed_at_prepare(tmp_path):
    """Omitting legacy flags cannot omit the already-sealed arithmetic."""
    plan_path = fx.write_signed_plan(tmp_path)
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    stage_dir = tmp_path / "stage"
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(stage_dir),
        "--anchors", str(anchors),
    ]) == 0
    assert runner.main([
        "evaluate", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
    ]) == 0
    code = runner.main(["merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir)])
    assert code == 0
    assert (stage_dir / "candidate_scores.csv").exists()


def test_merge_refuses_cli_inputs_that_do_not_hash_match_the_sealed_package(
    tmp_path, capsys,
):
    plan_path = fx.write_signed_plan(tmp_path)
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    stage_dir = tmp_path / "stage"
    runner.main(["prepare", "--plan", str(plan_path), "--out", str(stage_dir),
                 "--anchors", str(anchors)])
    runner.main(["evaluate", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
                 "--shard", "0", "--of", "1", "--evaluator", "surrogate"])
    observed, base_variance = fx.observed_and_variance(TARGETS)

    short = {k: v for k, v in observed.items() if k != TARGETS[0]}
    (tmp_path / "short.json").write_text(json.dumps(short))
    (tmp_path / "v.json").write_text(json.dumps(base_variance))
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--observed", str(tmp_path / "short.json"),
        "--base-variance", str(tmp_path / "v.json"),
    ]) == 1
    assert "observed_sha256 compatibility assertion" in capsys.readouterr().err

    (tmp_path / "o.json").write_text(json.dumps(observed))
    zeroed = dict(base_variance)
    zeroed[TARGETS[0]] = 0.0
    (tmp_path / "zero.json").write_text(json.dumps(zeroed))
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--observed", str(tmp_path / "o.json"),
        "--base-variance", str(tmp_path / "zero.json"),
    ]) == 1
    assert "base_variance_sha256 compatibility assertion" in capsys.readouterr().err

    nonfinite = dict(observed)
    nonfinite[TARGETS[0]] = float("nan")
    (tmp_path / "nan.json").write_text(json.dumps(nonfinite))
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--observed", str(tmp_path / "nan.json"),
        "--base-variance", str(tmp_path / "v.json"),
    ]) == 1
    assert "observed_sha256 compatibility assertion" in capsys.readouterr().err


def test_merge_records_the_hashes_of_the_acceptance_inputs(tmp_path):
    """A stage must record what z_i and B_i actually were."""
    stage_dir = _run_pipeline(tmp_path, 1, tag="_hash")
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    hashes = manifest["extra"]["target_inputs_sha256"]
    assert set(hashes) == {"observed", "base_variance"}
    assert all(len(v) == 64 for v in hashes.values())


def test_a_later_stage_requires_a_validated_complete_parent(tmp_path, capsys):
    """A PARTIAL stage must not be able to parent the next one.

    `validate_parent_stage` was written and tested but not connected: prepare
    took the parent hash as free text, so `--stage confirmation` could be run
    against nothing at all.
    """
    plan_path = fx.write_signed_plan(tmp_path)
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)

    # No --parent-dir at all.
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "promo"),
        "--anchors", str(anchors), "--stage", nc.STAGE_PROMOTION,
    ]) == 1
    assert "--parent-dir is required" in capsys.readouterr().err

    # A PREPARED (not COMPLETE) parent must be refused.
    discovery = tmp_path / "discovery"
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(discovery),
        "--anchors", str(anchors),
    ]) == 0
    assert json.loads((discovery / "manifest.json").read_text())["status"] == nc.STATUS_PREPARED
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "promo2"),
        "--anchors", str(anchors), "--stage", nc.STAGE_PROMOTION,
        "--parent-dir", str(discovery),
    ]) == 1
    assert "may be a parent" in capsys.readouterr().err


def test_child_refuses_a_complete_parent_from_a_different_signed_plan(
    tmp_path, capsys,
):
    first = tmp_path / "first"
    first.mkdir()
    parent = _run_pipeline(first, 1)
    other_plan = fx.write_signed_plan(
        tmp_path / "other", **{"budget.wall_time_cap_seconds": 3599}
    )
    code = runner.main([
        "prepare", "--plan", str(other_plan),
        "--out", str(tmp_path / "wrong_plan_child"),
        "--model", nc.MODEL_M0, "--stage", nc.STAGE_PROMOTION,
        "--parent-dir", str(parent),
    ])
    assert code == 1
    assert "differs from the stage's frozen plan hash" in capsys.readouterr().err

def test_the_first_stage_needs_no_parent_and_refuses_a_spurious_one(tmp_path, capsys):
    plan_path = fx.write_signed_plan(tmp_path)
    space = fx.m0_space()
    anchors = _write_anchors(tmp_path / "anchors.csv", space)
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "d1"),
        "--anchors", str(anchors),
    ]) == 0
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "d2"),
        "--anchors", str(anchors), "--parent-dir", str(tmp_path / "d1"),
    ]) == 1
    assert "has no parent" in capsys.readouterr().err

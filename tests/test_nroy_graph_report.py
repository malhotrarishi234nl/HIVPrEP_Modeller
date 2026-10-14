"""Connectivity, reporting and end-to-end runner tests.

Covers section 4 (connectivity without Euclidean overclaiming) and the
"Reporting" block of section 9 of ``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md``,
plus a full ``prepare -> evaluate -> merge -> report`` pass driven through the
real CLI with the surrogate evaluator.

No simulation engine is constructed and no trajectory is run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402
from nroy_fixtures import ROOT  # noqa: E402

import coupled_calibration_search as ccs  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
import nroy_graph as ng  # noqa: E402
import nroy_report as nrp  # noqa: E402
import run_robust_nroy_freeze as runner  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402

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
    first = ng.candidate_edges(points, neighbour_rule="k_nearest", neighbour_count=1)
    shuffled = {k: points[k] for k in ("c", "b", "a")}
    second = ng.candidate_edges(shuffled, neighbour_rule="k_nearest", neighbour_count=1)
    assert [e.edge_id for e in first] == [e.edge_id for e in second]


def test_unknown_graph_rules_are_refused():
    points = two_basins()
    with pytest.raises(ContractError, match="unknown neighbour rule"):
        ng.candidate_edges(points, neighbour_rule="whatever", neighbour_count=2)
    with pytest.raises(ContractError, match="unknown tie-breaking"):
        ng.candidate_edges(
            points, neighbour_rule="k_nearest", neighbour_count=2, tie_breaking="random"
        )
    with pytest.raises(ContractError, match="unknown minimal-edge algorithm"):
        ng.minimal_edge_set([], algorithm="magic", node_ids=list(points))


def test_minimal_edge_set_is_smaller_than_all_candidate_edges():
    points = two_basins()
    edges = ng.candidate_edges(points, neighbour_rule="k_nearest", neighbour_count=3)
    minimal = ng.minimal_edge_set(
        edges, algorithm="kruskal_min_spanning_forest", node_ids=list(points)
    )
    assert 0 < len(minimal) < len(edges)
    everything = ng.minimal_edge_set(
        edges, algorithm="all_candidate_edges", node_ids=list(points)
    )
    assert len(everything) == len(edges)


def test_mutual_k_nearest_proposes_no_more_edges_than_k_nearest():
    points = two_basins()
    plain = ng.candidate_edges(points, neighbour_rule="k_nearest", neighbour_count=2)
    mutual = ng.candidate_edges(points, neighbour_rule="mutual_k_nearest", neighbour_count=2)
    assert len(mutual) <= len(plain)


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
    edges = ng.candidate_edges(points, neighbour_rule="k_nearest", neighbour_count=2)
    minimal = ng.minimal_edge_set(
        edges, algorithm="kruskal_min_spanning_forest", node_ids=list(points)
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


def test_validate_subcommand_accepts_a_signed_plan(tmp_path, capsys):
    plan_path = fx.write_signed_plan(tmp_path)
    code = runner.main(["validate", "--plan", str(plan_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "All required decisions are signed" in out
    assert "Signed is not the same as launch-ready" in out


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


def test_end_to_end_m1_includes_explicit_p_zero_and_a_tiny_positive_probe(tmp_path):
    """Both halves of the M1 boundary design, through the real CLI.

    Exact ``p = 0`` is the identity control, and a declared tiny positive value
    is probed rather than excluded: the specifications prohibit an exclusion
    band above zero.
    """
    stage_dir = _run_pipeline(
        tmp_path, 2, model=nc.MODEL_M1,
        overrides={"p_parameter.extra_probe_values": [1e-18]},
    )
    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv")
    assert (vectors[fx.P_NAME] == 0.0).any(), "M1 must include exact p=0 designs"
    assert (vectors[fx.P_NAME] == 1e-18).any(), (
        "a declared tiny positive p must be probed, not excluded as discontinuous"
    )
    # And it was really evaluated, not merely proposed.
    tiny = vectors[vectors[fx.P_NAME] == 1e-18]["design_id"].astype(str).tolist()
    scored = set(
        ne.read_frame_exact(stage_dir / "candidate_scores.csv")["design_id"].astype(str)
    )
    assert set(tiny) <= scored, "the tiny positive probe was proposed but never scored"


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


def test_merge_refuses_without_the_acceptance_inputs(tmp_path, capsys):
    """z_i and B_i are the numerator and denominator of every implausibility.

    Previously `merge` skipped scoring silently when the flags were omitted and
    still published a COMPLETE, parentable stage containing no acceptance
    arithmetic, while RUN_AUDIT.md reported recomputation as "NOT verified".
    """
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
    assert code == 1
    assert "no acceptance arithmetic" in capsys.readouterr().err
    assert not (stage_dir / "candidate_scores.csv").exists()


def test_merge_refuses_acceptance_inputs_that_do_not_match_the_contract(tmp_path, capsys):
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
    assert "--observed" in capsys.readouterr().err

    (tmp_path / "o.json").write_text(json.dumps(observed))
    zeroed = dict(base_variance)
    zeroed[TARGETS[0]] = 0.0
    (tmp_path / "zero.json").write_text(json.dumps(zeroed))
    assert runner.main([
        "merge", "--plan", str(plan_path), "--stage-dir", str(stage_dir),
        "--observed", str(tmp_path / "o.json"),
        "--base-variance", str(tmp_path / "zero.json"),
    ]) == 1
    assert "strictly positive" in capsys.readouterr().err


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

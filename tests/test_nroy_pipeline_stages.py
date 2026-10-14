"""The wired pipeline stages: classify, bridges, regions, cap and diagnostic.

Covers the parts of ``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md`` that were
previously implemented only as library functions:

* section 3 local-support classification reached from a runner stage, with the
  five evidence states kept distinct;
* section 3.3 ``p`` coverage produced as a table;
* section 4 candidate-edge graph, frozen minimal edge set, bridge verdicts,
  components and geometry-only representative selection;
* section 5 wall-clock cap with bounded in-flight completion and a PARTIAL,
  non-parentable outcome;
* section 7 correlated-output diagnostic, stability-gated and unable to reach
  acceptance;
* the production-evaluator adapter behind every fail-closed protection,
  exercised with bounded fixtures and never launched.

No simulation engine is constructed and no trajectory is run.  The production
adapter is tested up to, but deliberately not including, the engine call.
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
import nroy_assess as nas  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
import nroy_graph as ng  # noqa: E402
import run_robust_nroy_freeze as runner  # noqa: E402
from coupled_calibration_search import ContractError  # noqa: E402

TARGETS = ccs.FROZEN_TARGET_ORDER
PASS_RULE = ng.BRIDGE_PASS_RULES[0]


# ==========================================================================
# Harness
# ==========================================================================

def _write_anchors(path: Path, space, count=3, seed=77) -> Path:
    rows = [
        {"candidate_id": f"A{index}", **vector}
        for index, vector in enumerate(fx.valid_interior_vectors(space, count, seed=seed))
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _acceptance_inputs(tmp_path: Path) -> tuple[Path, Path]:
    observed, base_variance = fx.observed_and_variance(TARGETS)
    observed_path = tmp_path / "observed.json"
    variance_path = tmp_path / "variance.json"
    observed_path.write_text(json.dumps(observed))
    variance_path.write_text(json.dumps(base_variance))
    return observed_path, variance_path


class Harness:
    """Drives the real CLI through a whole stage chain with the surrogate.

    Deliberately uses the CLI rather than calling the library directly: the
    point of these tests is that the stages are *reachable*, which a direct
    library call would not demonstrate.
    """

    def __init__(self, tmp_path: Path, *, model: str = nc.MODEL_M0, **overrides):
        self.tmp_path = tmp_path
        self.model = model
        self.plan_path = fx.write_signed_plan(tmp_path, **overrides)
        self.plan = nc.FrozenPlan.load(self.plan_path, root=ROOT)
        self.space = fx.m1_space() if model == nc.MODEL_M1 else fx.m0_space()
        self.anchors = _write_anchors(tmp_path / "anchors.csv", self.space)
        self.observed, self.variance = _acceptance_inputs(tmp_path)
        self.dirs: dict[str, Path] = {}

    def prepare(self, stage: str, *, parent: str | None = None, extra=()) -> Path:
        out = self.tmp_path / f"stage_{stage}"
        argv = [
            "prepare", "--plan", str(self.plan_path), "--out", str(out),
            "--model", self.model, "--stage", stage,
        ]
        if stage != nc.STAGE_BRIDGES:
            argv += ["--anchors", str(self.anchors)]
        if parent is not None:
            argv += ["--parent-dir", str(self.dirs[parent])]
        argv += list(extra)
        assert runner.main(argv) == 0, f"prepare {stage} failed"
        self.dirs[stage] = out
        return out

    def evaluate(self, stage: str, n_shards: int = 2, extra=()) -> None:
        for shard in range(n_shards):
            assert runner.main([
                "evaluate", "--plan", str(self.plan_path),
                "--stage-dir", str(self.dirs[stage]), "--shard", str(shard),
                "--of", str(n_shards), "--evaluator", "surrogate", *extra,
            ]) == 0

    def merge(self, stage: str) -> None:
        assert runner.main([
            "merge", "--plan", str(self.plan_path), "--stage-dir", str(self.dirs[stage]),
            "--observed", str(self.observed), "--base-variance", str(self.variance),
        ]) == 0

    def classify(self, stage: str) -> pd.DataFrame:
        assert runner.main([
            "classify", "--plan", str(self.plan_path),
            "--stage-dir", str(self.dirs[stage]),
        ]) == 0
        return ne.read_frame_exact(self.dirs[stage] / "local_support.csv")

    def full_local_stage(self, stage: str, *, parent: str | None = None) -> Path:
        self.prepare(stage, parent=parent)
        self.evaluate(stage)
        self.merge(stage)
        self.classify(stage)
        return self.dirs[stage]

    def chain_to_confirmation(self) -> Path:
        self.full_local_stage(nc.STAGE_DISCOVERY)
        self.full_local_stage(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
        return self.full_local_stage(nc.STAGE_CONFIRMATION, parent=nc.STAGE_PROMOTION)

    def chain_to_bridges(self) -> Path:
        self.chain_to_confirmation()
        self.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)
        self.evaluate(nc.STAGE_BRIDGES)
        self.merge(nc.STAGE_BRIDGES)
        return self.dirs[nc.STAGE_BRIDGES]

    def regions(self) -> Path:
        out = self.tmp_path / "stage_regions"
        assert runner.main([
            "regions", "--plan", str(self.plan_path),
            "--parent-dir", str(self.dirs[nc.STAGE_BRIDGES]), "--out", str(out),
        ]) == 0
        self.dirs[nc.STAGE_REGIONS] = out
        return out


# ==========================================================================
# Evidence states: the five-way distinction, reached from the ledger
# ==========================================================================

def _ledger_with(units, attempts=()) -> ne.Ledger:
    ledger = ne.Ledger(stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, units=tuple(units))
    for attempt in attempts:
        ledger.attempts.append(attempt)
    return ledger


def _unit(design_id: str, seed: int) -> ne.WorkUnit:
    return ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id=design_id, seed=seed,
        vector_hash=f"h-{design_id}", kernel=nc.KERNEL_AMBIENT, parent_ids=("A0",),
        params={}, parent_manifest_sha256="p", code_sha256="c", config_sha256="g",
        target_schema=TARGETS,
    )


def _attempt(design_id: str, seed: int, status: str) -> ne.AttemptRecord:
    return ne.AttemptRecord(
        attempt_id=f"{design_id}#s{seed}#a001", design_id=design_id, seed=seed,
        status=status, vector_hash=f"h-{design_id}",
    )


def test_the_five_evidence_states_are_distinguished_from_the_ledger():
    """"Nothing ran", "it failed" and "nothing was found" are three statements."""
    units = [_unit(d, s) for d in ("CONF", "NEG", "FAIL", "NOTRUN", "UNSC") for s in (1, 2)]
    attempts = [
        _attempt("CONF", 1, ne.ATTEMPT_SUCCESS), _attempt("CONF", 2, ne.ATTEMPT_SUCCESS),
        _attempt("NEG", 1, ne.ATTEMPT_SUCCESS), _attempt("NEG", 2, ne.ATTEMPT_SUCCESS),
        _attempt("FAIL", 1, ne.ATTEMPT_SUCCESS), _attempt("FAIL", 2, ne.ATTEMPT_FAILED),
        _attempt("UNSC", 1, ne.ATTEMPT_SUCCESS), _attempt("UNSC", 2, ne.ATTEMPT_SUCCESS),
    ]
    scores = pd.DataFrame([
        {"design_id": "CONF", "i_max": 1.2, "nominal_pass": True},
        {"design_id": "NEG", "i_max": 7.5, "nominal_pass": False},
        {"design_id": "FAIL", "i_max": 1.0, "nominal_pass": True},
    ])
    evidence = nas.evidence_by_design(_ledger_with(units, attempts), scores)

    assert evidence["CONF"].evidence == nc.EVIDENCE_CONFIRMED
    assert evidence["NEG"].evidence == nc.EVIDENCE_NO_SUPPORT
    assert evidence["NOTRUN"].evidence == nc.EVIDENCE_NOT_RUN
    assert evidence["UNSC"].evidence == nc.EVIDENCE_UNRESOLVED
    # A score row cannot rescue a unit whose seed block did not complete: the
    # execution failure wins, and it is never read as confirmation.
    assert evidence["FAIL"].evidence == nc.EVIDENCE_EXECUTION_FAILURE
    assert not evidence["FAIL"].confirmed
    assert evidence["FAIL"].n_failed_attempts == 1

    table = nas.evidence_table(evidence)
    assert set(table["evidence_state"]) == {
        nc.EVIDENCE_CONFIRMED, nc.EVIDENCE_NO_SUPPORT, nc.EVIDENCE_UNRESOLVED,
        nc.EVIDENCE_EXECUTION_FAILURE, nc.EVIDENCE_NOT_RUN,
    }


def test_a_merge_without_acceptance_arithmetic_cannot_be_assessed():
    units = [_unit("D", s) for s in (1, 2)]
    ledger = _ledger_with(units, [_attempt("D", s, ne.ATTEMPT_SUCCESS) for s in (1, 2)])
    with pytest.raises(ContractError, match="no acceptance decision"):
        nas.evidence_by_design(ledger, pd.DataFrame([{"design_id": "D", "i_max": 1.0}]))


# ==========================================================================
# classify: local support and p coverage, reached from the CLI
# ==========================================================================

def test_classify_produces_local_support_from_a_merged_stage(tmp_path):
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 3)
    harness.merge(nc.STAGE_DISCOVERY)
    support = harness.classify(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]

    assert (stage_dir / "local_support.csv").exists()
    assert (stage_dir / "design_evidence.csv").exists()
    assert len(support) == 3, "one row per anchor"
    assert set(support["candidate_id"]) == {"A0", "A1", "A2"}
    assert set(support["local_support_label"]) <= set(nc.LOCAL_SUPPORT_LABELS)
    # Every published count is present, so a reader can check the label.
    for column in (
        "n_confirmed_ambient", "n_confirmed_ambient_small_scale",
        "n_confirmed_correlation_following", "n_independent_direction_classes",
        "n_neighbours_evaluated", "n_neighbours_failed", "n_neighbours_not_run",
        "eligible_for_representation", "local_support_reason",
    ):
        assert column in support.columns, column
    # Eligibility follows the label, never the other way round.
    for _, row in support.iterrows():
        assert bool(row["eligible_for_representation"]) == (
            row["local_support_label"] in nc.ELIGIBLE_LABELS
        )
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    assert "local_support_counts" in manifest["extra"]


def test_classify_refuses_a_stage_that_was_never_scored(tmp_path):
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 1)
    code = runner.main([
        "classify", "--plan", str(harness.plan_path),
        "--stage-dir", str(harness.dirs[nc.STAGE_DISCOVERY]),
    ])
    assert code == 1, "classification without acceptance arithmetic must be refused"


def test_classify_writes_the_p_coverage_profile_for_m1(tmp_path):
    harness = Harness(
        tmp_path, model=nc.MODEL_M1,
        **{"p_parameter.extra_probe_values": [1e-18]},
    )
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 2)
    harness.merge(nc.STAGE_DISCOVERY)
    harness.classify(nc.STAGE_DISCOVERY)
    coverage = ne.read_frame_exact(
        harness.dirs[nc.STAGE_DISCOVERY] / "p_coverage.csv"
    )
    bins = fx.TEST_SIGNATURES["p_parameter.bins"]
    assert len(coverage) == len(bins) - 1
    assert list(coverage["bin_lower"]) == list(bins[:-1])
    assert coverage["descriptive_coverage_only"].all(), (
        "a covered bin is where the design confirmed points, not a claim that every "
        "value in it is plausible"
    )
    # The tiny positive probe lands in the first bin alongside p=0, and both
    # were evaluated: there is no excluded band.
    assert int(coverage.loc[0, "n_evaluated"]) >= 2


def test_p_coverage_never_counts_work_that_was_not_run():
    """A probe with no attempt cannot make a bin "evaluated"."""
    m1 = fx.m1_space()
    vectors = pd.DataFrame([
        {"design_id": "RAN", fx.P_NAME: 0.15},
        {"design_id": "NOTRUN", fx.P_NAME: 0.35},
    ])
    evidence = {
        "RAN": nas.DesignEvidence(
            design_id="RAN", evidence=nc.EVIDENCE_CONFIRMED, n_seeds_declared=2,
            n_seeds_succeeded=2, n_failed_attempts=0, scored=True, nominal_pass=True,
            i_max=1.0, reason="",
        ),
        "NOTRUN": nas.DesignEvidence(
            design_id="NOTRUN", evidence=nc.EVIDENCE_NOT_RUN, n_seeds_declared=2,
            n_seeds_succeeded=0, n_failed_attempts=0, scored=False, nominal_pass=None,
            i_max=None, reason="",
        ),
    }
    coverage = nas.p_coverage_from_tables(
        bins=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5], design_vectors=vectors,
        evidence=evidence, model=m1,
    )
    inside = coverage.set_index("bin_lower")["n_evaluated"]
    assert int(inside[0.1]) == 1, "the evaluated probe is counted"
    assert int(inside[0.3]) == 0, "work that was not run must not read as coverage"


# ==========================================================================
# Bridges: the graph is frozen before any bridge is simulated
# ==========================================================================

def test_bridges_prepare_freezes_the_graph_and_its_probes(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_confirmation()
    bridges = harness.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)

    nodes = ne.read_frame_exact(bridges / "graph_nodes.csv")
    edges = ne.read_frame_exact(bridges / "candidate_edges.csv")
    proposals = ne.read_frame_exact(bridges / "design_proposals.csv")

    assert len(nodes) == 3
    assert [c for c in nodes.columns if c.startswith("u:")], (
        "the node table must carry the normalised geometry selection will see"
    )
    assert not edges.empty
    assert edges["in_minimal_edge_set"].any()
    # Kruskal on three nodes keeps exactly two edges.
    minimal = edges[edges["in_minimal_edge_set"].astype(bool)]
    assert len(minimal) == 2

    fractions = list(fx.TEST_SIGNATURES["graph.bridge_fractions"])
    assert len(proposals) == len(minimal) * len(fractions)
    assert set(proposals["kernel_id"]) == {nc.KERNEL_BRIDGE}
    assert sorted(set(proposals["scale"].astype(float))) == sorted(fractions)
    # A bridge keeps BOTH endpoints as parents rather than inventing an anchor.
    assert (proposals["n_parents"] == 2).all()


def test_bridges_refuse_a_parent_with_no_completed_local_assessment(tmp_path):
    harness = Harness(tmp_path)
    harness.full_local_stage(nc.STAGE_DISCOVERY)
    harness.full_local_stage(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
    # Merge the confirmation stage but deliberately skip `classify`.
    harness.prepare(nc.STAGE_CONFIRMATION, parent=nc.STAGE_PROMOTION)
    harness.evaluate(nc.STAGE_CONFIRMATION)
    harness.merge(nc.STAGE_CONFIRMATION)
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "bridges_no_support"),
        "--model", nc.MODEL_M0, "--stage", nc.STAGE_BRIDGES,
        "--parent-dir", str(harness.dirs[nc.STAGE_CONFIRMATION]),
    ])
    assert code == 1, (
        "a confirmed point may not enter the graph without a completed local assessment"
    )


def test_bridges_refuse_a_hand_supplied_node_set(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_confirmation()
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "bridges_handset"),
        "--model", nc.MODEL_M0, "--stage", nc.STAGE_BRIDGES,
        "--parent-dir", str(harness.dirs[nc.STAGE_CONFIRMATION]),
        "--anchors", str(harness.anchors),
    ])
    assert code == 1, "the graph must be built over exactly the assessed points"


def test_bridges_use_the_reserved_confirmation_seed_block(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_confirmation()
    bridges = harness.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)
    units = ne.read_frame_exact(bridges / "ledger_units.csv")
    assert set(units["seed"].astype(int)) == set(harness.plan.seeds.confirmation)
    assert not set(units["seed"].astype(int)) & set(harness.plan.seeds.discovery)


# ==========================================================================
# Regions: verdicts, components, representatives
# ==========================================================================

def test_regions_produces_verdicts_components_and_representatives(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    out = harness.regions()

    for name in (
        "edge_verdicts.csv", "components.csv", "representatives.csv",
        "candidate_edges.csv", "graph_nodes.csv", "manifest.json",
        "unresolved_blocking_edges.json",
    ):
        assert (out / name).exists(), f"missing {name}"

    verdicts = ne.read_frame_exact(out / "edge_verdicts.csv")
    components = ne.read_frame_exact(out / "components.csv")
    representatives = ne.read_frame_exact(out / "representatives.csv")

    assert len(verdicts) == 2
    assert set(verdicts["edge_label"]) <= set(nc.EDGE_LABELS)
    assert set(verdicts["evidence_state"]) <= set(nc.EVIDENCE_STATES)
    assert (verdicts["n_bridge_probes_declared"] == 3).all()
    assert not components.empty
    assert not representatives.empty
    assert set(representatives["selection_rule"].dropna()) <= {"maximin_geometry"}

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["stage"] == nc.STAGE_REGIONS
    assert manifest["status"] == nc.STATUS_COMPLETE
    assert manifest["parent_stage"] == nc.STAGE_BRIDGES
    assert manifest["parent_manifest_sha256"], "lineage must reach the regions stage"
    assert "sampled-path" in manifest["extra"]["limitation"]


def test_regions_refuses_a_bridges_stage_that_was_not_scored(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_confirmation()
    harness.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)
    harness.evaluate(nc.STAGE_BRIDGES)
    # No merge: nothing has been scored, so no edge can be labelled.
    code = runner.main([
        "regions", "--plan", str(harness.plan_path),
        "--parent-dir", str(harness.dirs[nc.STAGE_BRIDGES]),
        "--out", str(tmp_path / "regions_unscored"),
    ])
    assert code == 1


def test_an_ineligible_member_receives_no_representative(tmp_path):
    """Decision 7: no local support means no entry to the forecast ensemble."""
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    bridges = harness.dirs[nc.STAGE_BRIDGES]
    nodes = ne.read_frame_exact(bridges / "graph_nodes.csv")
    nodes["eligible_for_representation"] = False
    nodes["local_support_label"] = nc.LABEL_NO_LOCAL_SUPPORT
    nodes.to_csv(bridges / "graph_nodes.csv", index=False)
    out = harness.regions()
    representatives = ne.read_frame_exact(out / "representatives.csv")
    assert representatives["candidate_id"].replace("", np.nan).isna().all(), (
        "a component with no supported member must yield no representative"
    )
    assert representatives["note"].str.contains("no member has demonstrated").all()


def test_the_declared_bridge_pass_rule_is_checked_not_assumed():
    edge = ng.CandidateEdge(edge_id="A--B", left="A", right="B", distance=0.1, rank=0)
    confirmed = {"A": True, "B": True}
    verdict = ng.classify_edge(
        edge, endpoint_confirmed=confirmed,
        probe_evidence=[nc.EVIDENCE_CONFIRMED] * 3, n_declared=3, pass_rule=PASS_RULE,
    )
    assert verdict.supported
    with pytest.raises(ContractError, match="will not silently substitute"):
        ng.classify_edge(
            edge, endpoint_confirmed=confirmed,
            probe_evidence=[nc.EVIDENCE_CONFIRMED] * 3, n_declared=3,
            pass_rule="any_probe_confirming_is_enough",
        )


def test_the_minimal_edge_set_is_read_back_not_recomputed(tmp_path):
    """The frozen choice of which bridges were paid for must be the one judged."""
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    frame = ne.read_frame_exact(
        harness.dirs[nc.STAGE_BRIDGES] / "candidate_edges.csv"
    )
    minimal = nas.minimal_edges_from_table(frame)
    assert {e.edge_id for e in minimal} == set(
        frame[frame["in_minimal_edge_set"].astype(bool)]["edge_id"]
    )
    without = frame.drop(columns=["in_minimal_edge_set"])
    with pytest.raises(ContractError, match="does not record the minimal edge set"):
        nas.minimal_edges_from_table(without)


def test_an_unresolved_edge_that_would_change_representation_is_recorded(tmp_path):
    """Spec sec. 4: that is the trigger for escalation, and nothing else is."""
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    out = harness.regions()
    payload = json.loads((out / "unresolved_blocking_edges.json").read_text())
    assert "edge_ids" in payload
    verdicts = ne.read_frame_exact(out / "edge_verdicts.csv")
    components = ne.read_frame_exact(out / "components.csv")
    # An edge can only be blocking if it is unresolved and spans two components.
    unresolved = set(verdicts[verdicts["edge_label"] == nc.EDGE_UNRESOLVED]["edge_id"])
    assert set(payload["edge_ids"]) <= unresolved
    if payload["edge_ids"]:
        assert len(components) > 1
    assert "emulator" in payload["escalation"]


# ==========================================================================
# Wall-clock cap (spec sec. 5)
# ==========================================================================

def test_the_wall_clock_cap_stops_dispatch_and_bounds_in_flight_work():
    cap = ne.WallClockCap(cap_seconds=10.0, started_utc=0.0)
    # Pretend time by passing `now`: a unit may start before the cap...
    assert cap.may_start(now=5.0)
    cap.record_dispatch()
    # ...the in-flight unit is allowed to overrun, and the overrun is recorded...
    cap.record_completion(now=12.0)
    assert cap.overran_by_seconds == pytest.approx(2.0)
    # ...and no further unit is dispatched.
    assert not cap.may_start(now=12.0)
    cap.record_withheld(7)
    row = cap.as_row()
    assert row["stopped_for_wall_clock_cap"] is True
    assert row["n_units_dispatched"] == 1
    assert row["n_units_withheld_at_cap"] == 7
    assert "cannot become a parent" in row["consequence"]


def test_a_positive_cap_is_required():
    for bad in (0.0, -1.0):
        with pytest.raises(ContractError, match="must be positive"):
            ne.WallClockCap(cap_seconds=bad)


def test_the_cap_leaves_a_partial_non_parentable_stage(tmp_path):
    """An expired cap must not produce a stage the next one can descend from."""
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]
    # A cap already expired on arrival: dispatch stops before the first unit.
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
        "--wall-time-cap-seconds", "1e-9",
    ]) == 0
    harness.merge(nc.STAGE_DISCOVERY)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    assert manifest["status"] == nc.STATUS_PARTIAL
    assert manifest["parentable"] is False
    assert manifest["n_incomplete"] > 0
    # And the next stage refuses to descend from it.
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path), "--out", str(tmp_path / "child"),
        "--anchors", str(harness.anchors), "--stage", nc.STAGE_PROMOTION,
        "--parent-dir", str(stage_dir),
    ])
    assert code == 1


def test_work_withheld_at_the_cap_is_not_run_or_scored(tmp_path):
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
        "--wall-time-cap-seconds", "1e-9",
    ]) == 0
    shard = ne.read_frame_exact(stage_dir / "shards" / "shard_000_of_001.csv")
    assert shard.empty, "no unit may be evaluated after the cap expires"
    ledger = ne.Ledger.load(stage_dir)
    assert not ledger.completed_keys()
    # The withheld units are "work not run", never "no support".
    evidence = nas.evidence_by_design(ledger, None)
    assert {state.evidence for state in evidence.values()} == {nc.EVIDENCE_NOT_RUN}


def test_resuming_after_a_cap_completes_the_same_scientific_table(tmp_path):
    """Spec sec. 5: only the attempt audit may differ."""
    (tmp_path / "ref").mkdir()
    (tmp_path / "cap").mkdir()
    reference = Harness(tmp_path / "ref")
    reference.prepare(nc.STAGE_DISCOVERY)
    reference.evaluate(nc.STAGE_DISCOVERY, 1)
    reference.merge(nc.STAGE_DISCOVERY)

    capped = Harness(tmp_path / "cap")
    capped.prepare(nc.STAGE_DISCOVERY)
    stage_dir = capped.dirs[nc.STAGE_DISCOVERY]
    # Stop almost immediately, then resume with no cap pressure.
    assert runner.main([
        "evaluate", "--plan", str(capped.plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
        "--wall-time-cap-seconds", "1e-9",
    ]) == 0
    capped.evaluate(nc.STAGE_DISCOVERY, 1)
    capped.merge(nc.STAGE_DISCOVERY)

    assert json.loads(
        (stage_dir / "manifest.json").read_text()
    )["status"] == nc.STATUS_COMPLETE
    left = ne.read_frame_exact(
        reference.dirs[nc.STAGE_DISCOVERY] / "candidate_scores.csv"
    )
    right = ne.read_frame_exact(stage_dir / "candidate_scores.csv")
    pd.testing.assert_frame_equal(left, right)


# ==========================================================================
# Declared source pool and archive neighbour rule
# ==========================================================================

def test_a_declared_source_that_cannot_contribute_is_refused():
    anchor = nd.Anchor.build("A0", fx.interior_vector(fx.m0_space()), fx.m0_space())
    space = fx.m0_space()
    with pytest.raises(ContractError, match="no archive vectors were supplied"):
        nd.correlation_following_directions(
            anchor, space, source_pool=[nd.SOURCE_LOW_J_ARCHIVE],
            confirmed=[anchor.params], confirmed_ids=["A0"],
            neighbour_count=2, rank_truncation=2,
            neighbour_rule="k_nearest_in_normalised_metric",
        )
    with pytest.raises(ContractError, match="no other confirmed vector"):
        nd.correlation_following_directions(
            anchor, space, source_pool=[nd.SOURCE_CONFIRMED_DIFFERENCES],
            confirmed=[anchor.params], confirmed_ids=["A0"],
            neighbour_count=2, rank_truncation=2,
            neighbour_rule="k_nearest_in_normalised_metric",
        )


def test_an_undeclared_source_contributes_nothing():
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 3, seed=11)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    directions = nd.correlation_following_directions(
        anchor, space, source_pool=[nd.SOURCE_CONFIRMED_DIFFERENCES],
        archive=vectors[1:], archive_ids=["Z1", "Z2"], archive_j=[0.1, 0.2],
        confirmed=vectors, confirmed_ids=["A0", "A1", "A2"],
        neighbour_count=2, rank_truncation=4,
        neighbour_rule="k_nearest_in_normalised_metric",
    )
    assert directions
    assert {label for _, label, _ in directions} == {nd.DIRCLASS_CONFIRMED_DIFF}, (
        "an archive pool that was not declared must not reach the design"
    )


def test_the_source_pool_is_validated_rather_than_interpreted():
    with pytest.raises(ContractError, match="is empty"):
        nd.assert_source_pool([])
    with pytest.raises(ContractError, match="unknown correlation-following source"):
        nd.assert_source_pool(["whatever_is_available"])
    with pytest.raises(ContractError, match="repeats"):
        nd.assert_source_pool([nd.SOURCE_CROSS_BLOCK, nd.SOURCE_CROSS_BLOCK])
    # Frozen order, so the written order cannot change the assembled design.
    assert nd.assert_source_pool(
        [nd.SOURCE_CROSS_BLOCK, nd.SOURCE_CONFIRMED_DIFFERENCES]
    ) == (nd.SOURCE_CONFIRMED_DIFFERENCES, nd.SOURCE_CROSS_BLOCK)


def test_the_cross_block_quota_and_the_declared_pool_must_agree():
    assert nd.assert_cross_block_declared([nd.SOURCE_CROSS_BLOCK], 4) is True
    assert nd.assert_cross_block_declared([nd.SOURCE_CONFIRMED_DIFFERENCES], 0) is False
    with pytest.raises(ContractError, match="would never be used"):
        nd.assert_cross_block_declared([nd.SOURCE_CROSS_BLOCK], 0)
    with pytest.raises(ContractError, match="undeclared source"):
        nd.assert_cross_block_declared([nd.SOURCE_CONFIRMED_DIFFERENCES], 4)


def test_the_low_j_rule_refuses_to_fall_back_to_distance_only():
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 3, seed=5)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    with pytest.raises(ContractError, match="no J values were supplied"):
        nd.archive_directions(
            anchor, space, archive=vectors[1:], archive_ids=["Z1", "Z2"],
            neighbour_count=2, rank_truncation=2,
            neighbour_rule="k_nearest_low_J_archive_vectors",
        )


def test_the_low_j_rule_consults_in_j_order(tmp_path):
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 4, seed=9)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    # Z3 is the furthest but has the lowest J, so the low-J rule must take it
    # first while the distance rule must not.
    by_j = nd.archive_directions(
        anchor, space, archive=vectors[1:], archive_ids=["Z1", "Z2", "Z3"],
        archive_j=[9.0, 5.0, 0.1], neighbour_count=1, rank_truncation=1,
        neighbour_rule="k_nearest_low_J_archive_vectors",
    )
    assert [parents[1] for _, _, parents in by_j] == ["Z3"]
    by_distance = nd.archive_directions(
        anchor, space, archive=vectors[1:], archive_ids=["Z1", "Z2", "Z3"],
        archive_j=[9.0, 5.0, 0.1], neighbour_count=3, rank_truncation=3,
        neighbour_rule="k_nearest_in_normalised_metric",
    )
    assert len(by_distance) == 3


def test_the_archive_neighbour_count_is_its_own_decision():
    """It is not the graph's neighbour count, which an earlier version reused."""
    assert "archive_following.neighbour_count" in nc.REQUIRED_DECISIONS
    assert "graph.neighbour_count" in nc.REQUIRED_DECISIONS
    source = (ROOT / "run_robust_nroy_freeze.py").read_text()
    archive_block = source[
        source.index("directions = nd.correlation_following_directions"):
    ][:900]
    assert '"graph.neighbour_count"' not in archive_block, (
        "the archive-following kernel must not read the graph's neighbour count"
    )


def test_the_declared_archive_pool_reaches_a_real_design(tmp_path):
    """End to end: --archive plus a declared low-J source changes the design."""
    space = fx.m0_space()
    archive = _write_anchors(tmp_path / "archive.csv", space, count=4, seed=404)
    frame = pd.read_csv(archive)
    frame["j"] = [0.9, 0.2, 0.5, 0.1]
    frame.to_csv(archive, index=False)
    harness = Harness(
        tmp_path,
        **{
            "archive_following.source_pool": [
                nd.SOURCE_LOW_J_ARCHIVE, nd.SOURCE_CONFIRMED_DIFFERENCES,
                nd.SOURCE_CROSS_BLOCK,
            ],
            "archive_following.neighbour_rule": "k_nearest_low_J_archive_vectors",
        },
    )
    stage_dir = harness.prepare(
        nc.STAGE_DISCOVERY, extra=["--archive", str(archive)]
    )
    proposals = ne.read_frame_exact(stage_dir / "design_proposals.csv")
    archive_rows = proposals[proposals["kernel_id"] == nc.KERNEL_ARCHIVE_FOLLOWING]
    assert not archive_rows.empty
    assert (archive_rows["direction_class"] == nd.DIRCLASS_ARCHIVE_DIFF).any(), (
        "the declared archive pool must actually supply directions"
    )
    # Lineage: an archive-derived probe names the archive vector it came from.
    parents = set()
    for value in archive_rows["parent_ids"]:
        parents.update(str(value).split("|"))
    assert parents & set(frame["candidate_id"].astype(str))


# ==========================================================================
# Correlated-output diagnostic (spec sec. 7)
# ==========================================================================

def _seed_frame(
    n_candidates: int, n_seeds: int, *, correlated: bool, seed: int = 3
) -> pd.DataFrame:
    """Confirmation-style seed rows with a known stochastic structure."""
    rng = np.random.default_rng(seed)
    rows = []
    for candidate in range(n_candidates):
        level = 100.0 + 10.0 * candidate
        for index in range(n_seeds):
            shared = rng.standard_normal()
            values = []
            for target in range(len(TARGETS)):
                noise = rng.standard_normal()
                # A single shared factor across targets when correlated.
                values.append(level + (shared if correlated else noise))
            rows.append({
                "design_id": f"D{candidate}",
                "seed": 900 + index,
                **{f"target:{name}": v for name, v in zip(TARGETS, values)},
            })
    return pd.DataFrame(rows)


def _contract() -> nc.TargetContract:
    return nc.TargetContract(
        frozen_order=TARGETS, register_path="x", register_sha256="h"
    )


def test_the_diagnostic_refuses_to_run_on_too_few_seed_blocks():
    """The effective sample size is seed blocks, not candidates times seeds."""
    frame = _seed_frame(n_candidates=6, n_seeds=2, correlated=True)
    result = nas.correlated_output_diagnostic(
        frame, targets=_contract(), shrinkage=0.1, n_bootstrap=20,
        bootstrap_seed=7, rank_tolerance=1.0, direction_cosine=0.9,
    )
    assert result.status == nas.DIAGNOSTIC_INSUFFICIENT
    assert "seed block" in result.reason
    assert result.effective_rank is None
    payload = result.as_dict()
    assert payload["effective_stochastic_sample_size"] == 2
    assert payload["n_candidates"] == 6


def test_the_diagnostic_reports_rank_and_directions_when_stable():
    frame = _seed_frame(n_candidates=6, n_seeds=12, correlated=True)
    result = nas.correlated_output_diagnostic(
        frame, targets=_contract(), shrinkage=0.05, n_bootstrap=25,
        bootstrap_seed=11, rank_tolerance=10.0, direction_cosine=0.0,
    )
    assert result.status == nas.DIAGNOSTIC_AVAILABLE
    assert result.effective_rank is not None and result.effective_rank > 0.0
    assert result.condition_number is not None
    assert len(result.leading_directions) == 2
    # A single shared factor should give a low effective rank relative to 13.
    assert result.effective_rank < len(TARGETS)


def test_an_unstable_diagnostic_is_marked_unavailable_not_repaired():
    frame = _seed_frame(n_candidates=3, n_seeds=4, correlated=False)
    result = nas.correlated_output_diagnostic(
        frame, targets=_contract(), shrinkage=0.0, n_bootstrap=15,
        bootstrap_seed=5, rank_tolerance=1e-6, direction_cosine=0.999999,
    )
    assert result.status == nas.DIAGNOSTIC_UNSTABLE
    assert "unavailable rather than repaired" in result.reason
    assert "dropping targets" in result.reason


def test_the_diagnostic_carries_no_route_to_acceptance():
    """Structural, not conventional: there is no pass field to misread."""
    frame = _seed_frame(n_candidates=5, n_seeds=10, correlated=True)
    result = nas.correlated_output_diagnostic(
        frame, targets=_contract(), shrinkage=0.1, n_bootstrap=10,
        bootstrap_seed=3, rank_tolerance=10.0, direction_cosine=0.0,
    )
    payload = result.as_dict()
    assert payload["can_change_primary_acceptance"] is False
    assert payload["is_secondary"] is True
    assert "NOT a multivariate acceptance calculation" in payload["interpretation"]
    forbidden = {"nominal_pass", "bound_pass", "promoted", "i_max", "nroy", "eligible"}
    assert not forbidden & set(payload), (
        f"the diagnostic must expose no acceptance field: {forbidden & set(payload)}"
    )


def test_the_diagnostic_compares_its_ranking_with_pointwise_i_max():
    frame = _seed_frame(n_candidates=6, n_seeds=10, correlated=True)
    scores = pd.DataFrame([
        {"design_id": f"D{index}", "i_max": 1.0 + index} for index in range(6)
    ])
    result = nas.correlated_output_diagnostic(
        frame, targets=_contract(), shrinkage=0.1, n_bootstrap=10,
        bootstrap_seed=3, rank_tolerance=10.0, direction_cosine=0.0, scores=scores,
    )
    assert result.rank_correlation_with_i_max is not None
    assert -1.0 <= result.rank_correlation_with_i_max <= 1.0


def test_shrinkage_outside_the_unit_interval_is_refused():
    frame = _seed_frame(n_candidates=4, n_seeds=8, correlated=True)
    for bad in (-0.1, 1.5):
        with pytest.raises(ContractError, match="must lie in"):
            nas.correlated_output_diagnostic(
                frame, targets=_contract(), shrinkage=bad, n_bootstrap=5,
                bootstrap_seed=1, rank_tolerance=10.0, direction_cosine=0.0,
            )


def test_diagnose_is_refused_while_the_plan_leaves_it_disabled(tmp_path):
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 1)
    harness.merge(nc.STAGE_DISCOVERY)
    code = runner.main([
        "diagnose", "--plan", str(harness.plan_path),
        "--stage-dir", str(harness.dirs[nc.STAGE_DISCOVERY]),
    ])
    assert code == 1, "the diagnostic must not run on defaults"


def test_diagnose_runs_and_writes_its_own_file_when_signed(tmp_path):
    harness = Harness(tmp_path, **{
        "correlated_output_diagnostic.enabled": True,
        "correlated_output.shrinkage": 0.1,
        "correlated_output.n_seed_block_bootstrap": 10,
        "correlated_output.bootstrap_seed": 990001,
        "correlated_output.stability_rank_tolerance": 10.0,
        "correlated_output.stability_direction_cosine": 0.0,
    })
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 1)
    harness.merge(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]
    before = ccs.sha256_file(stage_dir / "candidate_scores.csv")
    code = runner.main([
        "diagnose", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir),
    ])
    assert code in (0, 4), "available or explicitly unstable, never a crash"
    payload = json.loads((stage_dir / "correlated_output_diagnostic.json").read_text())
    assert payload["status"] in (
        nas.DIAGNOSTIC_AVAILABLE, nas.DIAGNOSTIC_UNSTABLE, nas.DIAGNOSTIC_INSUFFICIENT
    )
    assert payload["can_change_primary_acceptance"] is False
    # The primary table is untouched: the diagnostic cannot reach acceptance.
    assert ccs.sha256_file(stage_dir / "candidate_scores.csv") == before


# ==========================================================================
# The production evaluator adapter, up to but not including the engine
# ==========================================================================

def _dig(node, path):
    for part in path.split("."):
        node = node[part]
    return node


def test_the_production_adapter_injects_each_value_at_its_declared_path(tmp_path):
    harness = Harness(tmp_path)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    space = fx.m0_space()
    vector = fx.interior_vector(space)
    unit = ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id="D", seed=1,
        vector_hash=space.vector_hash(vector), kernel=nc.KERNEL_AMBIENT,
        parent_ids=("A0",), params=vector, parent_manifest_sha256="p",
        code_sha256="c", config_sha256="g", target_schema=TARGETS,
    )
    config = adapter.config_for(unit, vector)
    injected = 0
    for parameter in adapter.m0_calib_space["parameters"]:
        name, path = str(parameter["name"]), str(parameter["config_path"])
        if name not in vector:
            continue
        try:
            value = _dig(config, path)
        except (KeyError, TypeError):
            continue  # a derived parameter the injector handles specially
        if isinstance(value, (int, float)):
            assert value == pytest.approx(vector[name]), name
            injected += 1
    assert injected >= 20, (
        f"only {injected} coordinates were verified at their declared paths"
    )


def test_m1_injection_fails_loudly_if_the_config_lacks_the_p_block(tmp_path):
    """The silent-discard hazard that made ``p_parameter.config_path`` a decision.

    ``config_reader`` discards an unlisted disease-level block without a word,
    so a wrong or missing ``p`` path could leave M1 running as M0 while
    claiming otherwise.  Injection is therefore the right place to fail, and it
    does: ``set_nested`` refuses a path that does not exist.

    This is also a real production prerequisite, recorded rather than worked
    around: the frozen production base config must already carry the
    ``stage_structured_diagnosis`` block before M1 can be launched.
    """
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    assert fx.P_CONFIG_PATH.endswith("aids_symptom_annual_probability")
    space = fx.m1_space()
    vector = nc.embed_m0_in_m1(
        fx.m0_space(), space, fx.interior_vector(fx.m0_space()), p_value=0.25
    )
    unit = ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M1, design_id="D", seed=1,
        vector_hash=space.vector_hash(vector), kernel=nc.KERNEL_P_BOUNDARY,
        parent_ids=("A0",), params=vector, parent_manifest_sha256="p",
        code_sha256="c", config_sha256="g", target_schema=TARGETS,
    )
    with pytest.raises(KeyError, match="stage_structured_diagnosis"):
        adapter.config_for(unit, vector)


def test_m1_injection_lands_p_at_the_signed_path_when_the_block_exists(tmp_path):
    """With the block present, ``p`` lands exactly where the plan says."""
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    node = adapter.base_config["transmission_model"]["disease"]["diagnosis"]
    node["stage_structured_diagnosis"] = {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": 1.0},
        "aids_symptom_annual_probability": {"value": 0.0},
    }
    space = fx.m1_space()
    vector = nc.embed_m0_in_m1(
        fx.m0_space(), space, fx.interior_vector(fx.m0_space()), p_value=0.25
    )
    unit = ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M1, design_id="D", seed=1,
        vector_hash=space.vector_hash(vector), kernel=nc.KERNEL_P_BOUNDARY,
        parent_ids=("A0",), params=vector, parent_manifest_sha256="p",
        code_sha256="c", config_sha256="g", target_schema=TARGETS,
    )
    config = adapter.config_for(unit, vector)
    assert _dig(config, fx.P_CONFIG_PATH) == pytest.approx(0.25)
    # And the routine hazard scale the identity depends on is still pinned.
    assert _dig(
        config,
        "transmission_model.disease.diagnosis.stage_structured_diagnosis."
        "routine_hazard_scale.value",
    ) == 1.0


def test_the_production_adapter_resolves_every_target_in_the_frozen_order(tmp_path):
    harness = Harness(tmp_path)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    assert [t.key for t in adapter.targets] == list(harness.plan.targets.frozen_order)
    assert adapter.start_year <= adapter.end_year
    assert adapter.config_sha256


def test_the_production_adapter_refuses_a_reordered_target_vector(tmp_path):
    harness = Harness(tmp_path)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    unit = ne.WorkUnit(
        stage=nc.STAGE_DISCOVERY, model=nc.MODEL_M0, design_id="D", seed=1,
        vector_hash="h", kernel=nc.KERNEL_AMBIENT, parent_ids=("A0",), params={},
        parent_manifest_sha256="p", code_sha256="c", config_sha256="g",
        target_schema=TARGETS,
    )
    with pytest.raises(ContractError, match="not the frozen contract"):
        adapter(unit, {}, tuple(reversed(TARGETS)))


def test_the_production_adapter_refuses_a_missing_production_config(tmp_path):
    harness = Harness(tmp_path, **{"models.base_config": "configs/does_not_exist.json"})
    with pytest.raises(ContractError, match="does not exist"):
        runner.ProductionEvaluator(harness.plan, root=ROOT)


def test_the_production_adapter_generates_m1_as_m0_plus_one_coordinate(tmp_path):
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    m0_names = [str(p["name"]) for p in adapter.m0_calib_space["parameters"]]
    m1_names = [str(p["name"]) for p in adapter.calib_space_for(nc.MODEL_M1)["parameters"]]
    assert m1_names == m0_names + [fx.P_NAME], (
        "M1 must be M0 plus p appended last, generated rather than hand-edited"
    )


def test_a_plan_that_unpins_the_routine_hazard_scale_never_loads(tmp_path):
    """The M1 nesting precondition is refused at the earliest possible point.

    ``is_identity`` is ``rho == 1.0 AND p == 0.0``.  A plan that pins anything
    else is rejected by the loader, so no design, no injected config and no
    production evaluator can ever be built from it -- the guard does not wait
    until a trajectory is about to run.
    """
    with pytest.raises(ContractError, match="would not nest M0"):
        Harness(
            tmp_path, model=nc.MODEL_M1,
            **{"p_parameter.routine_hazard_scale_identity_value": 0.8},
        )
    # And the adapter re-checks it on every injected M1 config, so a plan that
    # somehow carried a different value could still not produce one.
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    assert float(
        adapter.plan.require("p_parameter.routine_hazard_scale_identity_value")
    ) == 1.0
    source = (ROOT / "run_robust_nroy_freeze.py").read_text()
    assert "assert_m1_identity_preconditions" in source.split("def config_for")[1][:1200]


def test_the_whole_chain_is_reachable_and_leaves_every_required_table(tmp_path):
    """The headline of this module: discovery through regions, via the CLI."""
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    regions = harness.regions()
    assert runner.main([
        "report", "--plan", str(harness.plan_path), "--stage-dir", str(regions),
    ]) == 0

    produced = {
        path.name
        for directory in harness.dirs.values()
        for path in directory.glob("*.csv")
    }
    for required in (
        "local_support.csv", "candidate_edges.csv", "edge_verdicts.csv",
        "components.csv", "representatives.csv", "design_evidence.csv",
        "candidate_scores.csv", "seed_results.csv", "graph_nodes.csv",
    ):
        assert required in produced, f"the pipeline never produced {required}"

    facts = json.loads((regions / "package" / "summary_facts.json").read_text())
    assert facts, "the package must report from the consolidated tables"

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

import copy
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

def _write_anchors(path: Path, space, count=3, seed=77, prefix="A") -> Path:
    rows = [
        {"candidate_id": f"{prefix}{index}", **vector}
        for index, vector in enumerate(fx.valid_interior_vectors(space, count, seed=seed))
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _acceptance_inputs(tmp_path: Path, *, confirming: bool) -> tuple[Path, Path]:
    """Acceptance inputs that make the surrogate either pass or fail everything.

    This matters more than it looks.  ``surrogate_evaluator`` returns values
    around 100-110 for every target, so the repository's usual synthetic
    inputs (``observed = 0``, ``B_i = 1``) give ``I_max`` around 100 and
    **nothing ever confirms**.  A chain driven that way exercises every
    structure and not one positive finding: no ``broadly_supported`` label, no
    ``sampled_path_supported`` edge, no representative actually selected and no
    region support.  That is the same shape of mistake as an identity test run
    on an epidemic that never starts.

    ``confirming=True`` centres the observed values inside the surrogate's own
    range with a wide frozen variance, so every candidate clears the cutoff and
    the positive paths are genuinely driven.  ``confirming=False`` keeps the
    everything-fails case, which is what the negative labels need.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    if confirming:
        observed = {name: 105.0 for name in TARGETS}
        base_variance = {name: 10000.0 for name in TARGETS}
    else:
        observed, base_variance = fx.observed_and_variance(TARGETS)
    suffix = "pass" if confirming else "fail"
    observed_path = tmp_path / f"observed_{suffix}.json"
    variance_path = tmp_path / f"variance_{suffix}.json"
    observed_path.write_text(json.dumps(observed))
    variance_path.write_text(json.dumps(base_variance))
    return observed_path, variance_path


def _write_island_anchors(path: Path, space) -> Path:
    """Anchors placed so the bridge probes land exactly on the islands.

    One spacing beyond each end island, so the declared 25/50/75% bridge
    fractions interpolate onto the three island centres.  Every other
    coordinate sits at 0.5, which is where the islands live, so the only
    coordinate that moves is the one the field reads.
    """
    centres = runner.ISLAND_CENTRES
    spacing = centres[1] - centres[0]
    rows = []
    for name, position in (("A0", centres[0] - spacing), ("A1", centres[-1] + spacing)):
        u = np.full(space.n, 0.5)
        u[0] = position
        params = space.as_dict(space.denormalise(u))
        reasons = ccs.invalid_reasons(space.space, [params[n] for n in space.names])
        assert not reasons, f"island anchor {name} violates a declared constraint: {reasons}"
        rows.append({"candidate_id": name, **params})
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class Harness:
    """Drives the real CLI through a whole stage chain with the surrogate.

    Deliberately uses the CLI rather than calling the library directly: the
    point of these tests is that the stages are *reachable*, which a direct
    library call would not demonstrate.

    One acceptance basis is used for the whole chain, always: ``merge`` now
    refuses a stage whose acceptance inputs differ from its parent's frozen
    identity, so a harness that varied them per stage could not run at all.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        model: str = nc.MODEL_M0,
        confirming: bool = True,
        evaluator: str = "surrogate",
        anchors: Path | None = None,
        **overrides,
    ):
        self.tmp_path = tmp_path
        self.model = model
        self.confirming = bool(confirming)
        self.evaluator = str(evaluator)
        self.observed, self.variance = _acceptance_inputs(
            tmp_path, confirming=self.confirming
        )
        self.space = fx.m1_space() if model == nc.MODEL_M1 else fx.m0_space()
        self.anchors = (
            Path(anchors) if anchors is not None
            else _write_anchors(
                tmp_path / "anchors.csv", self.space,
                prefix="M1P" if model == nc.MODEL_M1 else "A",
            )
        )
        self.plan_path = fx.write_signed_plan(
            tmp_path,
            acceptance_observed=self.observed,
            acceptance_variance=self.variance,
            m1_anchors=self.anchors if model == nc.MODEL_M1 else None,
            **overrides,
        )
        self.plan = nc.FrozenPlan.load(self.plan_path, root=ROOT)
        self.dirs: dict[str, Path] = {}
        self.m0_source: Harness | None = None

    def prepare(self, stage: str, *, parent: str | None = None, extra=()) -> Path:
        out = self.tmp_path / f"stage_{stage}"
        argv = [
            "prepare", "--plan", str(self.plan_path), "--out", str(out),
            "--model", self.model, "--stage", stage,
        ]
        if stage == nc.STAGE_DISCOVERY and self.model == nc.MODEL_M0:
            argv += ["--anchors", str(self.anchors)]
        if self.model == nc.MODEL_M1:
            prerequisites = self.plan.get("production_prerequisites")
            if "--identity-gate" not in extra:
                argv += ["--identity-gate", str(prerequisites["identity_gate"])]
            if stage == nc.STAGE_DISCOVERY:
                source_root = self.tmp_path / "m0_embedding_source"
                source_root.mkdir(exist_ok=True)
                self.m0_source = Harness(source_root, model=nc.MODEL_M0)
                # M0 and M1 are two models under one exact signed production
                # plan.  A separately generated fixture plan is correctly
                # rejected by the parent-plan lineage gate.
                self.m0_source.plan_path = self.plan_path
                self.m0_source.plan = self.plan
                self.m0_source.chain_to_confirmation()
                argv += ["--m0-confirmed-dir", str(
                    self.m0_source.dirs[nc.STAGE_CONFIRMATION]
                )]
                if "--anchors" not in extra:
                    argv += ["--anchors", str(self.anchors)]
                if "--m1-anchor-evidence" not in extra:
                    argv += [
                        "--m1-anchor-evidence",
                        str(prerequisites["m1_anchor_confirmation"]),
                    ]
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
                "--of", str(n_shards), "--evaluator", self.evaluator, *extra,
            ]) == 0

    def merge(self, stage: str, *, observed: Path | None = None,
              variance: Path | None = None) -> int:
        """Merge one stage against the chain's single acceptance basis.

        ``observed`` and ``variance`` override it only for the adversarial
        tests, which exist to show that an override is *refused*.
        """
        return runner.main([
            "merge", "--plan", str(self.plan_path), "--stage-dir", str(self.dirs[stage]),
            "--observed", str(observed or self.observed),
            "--base-variance", str(variance or self.variance),
        ])

    def classify(self, stage: str) -> pd.DataFrame:
        assert runner.main([
            "classify", "--plan", str(self.plan_path),
            "--stage-dir", str(self.dirs[stage]),
        ]) == 0
        return ne.read_frame_exact(self.dirs[stage] / "local_support.csv")

    def full_local_stage(self, stage: str, *, parent: str | None = None) -> Path:
        self.prepare(stage, parent=parent)
        self.evaluate(stage)
        assert self.merge(stage) == 0, f"merge {stage} failed"
        self.classify(stage)
        return self.dirs[stage]

    def chain_to_confirmation(self) -> Path:
        self.full_local_stage(nc.STAGE_DISCOVERY)
        self.prepare(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
        return self.full_local_stage(nc.STAGE_CONFIRMATION, parent=nc.STAGE_PROMOTION)

    def chain_to_bridges(self) -> Path:
        self.chain_to_confirmation()
        self.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)
        self.evaluate(nc.STAGE_BRIDGES)
        assert self.merge(nc.STAGE_BRIDGES) == 0, "merge bridges failed"
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


def test_classify_refuses_to_silently_replace_an_existing_classification(tmp_path):
    """A classification is a completed artifact a child may already depend on."""
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 1)
    harness.merge(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]
    argv = ["classify", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir)]
    assert runner.main(argv) == 0
    before = ccs.sha256_file(stage_dir / "local_support.csv")
    assert runner.main(argv) == 1, "a second classify must be refused"
    assert ccs.sha256_file(stage_dir / "local_support.csv") == before
    # ...but it can be replaced deliberately.
    assert runner.main(argv + ["--reclassify"]) == 0


def test_an_undispatched_probe_makes_its_anchor_unresolved_not_negative(tmp_path):
    """Spec sec. 9: an incomplete design must never read as "no support".

    Driven at library level, and deliberately so.  A *partially sharded* stage
    cannot reach this path through the CLI: sharding is by result key, so a
    half-evaluated stage leaves some designs with one replicate, and
    ``recompute_candidate_scores`` then refuses the whole merge -- correctly,
    because a predictive variance needs two draws.  See the recorded gap about
    partial merges.  What this test pins is the classification rule itself: a
    probe with no attempt is ``work_not_run``, and its anchor is
    ``unresolved``, never ``no_local_support_detected``.
    """
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY, 1)
    harness.merge(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]

    scores = ne.read_frame_exact(stage_dir / "candidate_scores.csv")
    proposals = ne.read_frame_exact(stage_dir / "design_proposals.csv")
    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv")
    anchors = ne.read_frame_exact(stage_dir / "anchors.csv")
    ledger = ne.Ledger.load(stage_dir)
    model = fx.m0_space()

    evidence = nas.evidence_by_design(ledger, scores)
    assert all(e.evidence == nc.EVIDENCE_CONFIRMED for e in evidence.values()), (
        "the baseline must be fully confirmed, or dropping one probe proves nothing"
    )
    complete = nas.local_support_table(
        anchors=anchors, proposals=proposals, design_vectors=vectors,
        kernel_outcomes=ne.read_frame_exact(stage_dir / "kernel_outcomes.csv"),
        evidence=evidence, model=model,
        smaller_ambient_scale=harness.plan.smaller_ambient_scale(),
        min_confirming=2, min_direction_classes=2, max_abs_direction_cosine=0.99, scores=scores,
    )
    assert (complete["local_support_label"] == nc.LABEL_BROADLY_SUPPORTED).all()

    # Now withhold exactly one probe belonging to the first anchor.
    first = str(anchors.iloc[0]["candidate_id"])
    withheld = next(
        design_id for design_id in sorted(evidence)
        if str(design_id).split("/")[3] == first
    )
    partial = {k: v for k, v in evidence.items() if k != withheld}
    support = nas.local_support_table(
        anchors=anchors, proposals=proposals, design_vectors=vectors,
        kernel_outcomes=ne.read_frame_exact(stage_dir / "kernel_outcomes.csv"),
        evidence=partial, model=model,
        smaller_ambient_scale=harness.plan.smaller_ambient_scale(),
        min_confirming=2, min_direction_classes=2, max_abs_direction_cosine=0.99, scores=scores,
    )
    row = support[support["candidate_id"] == first].iloc[0]
    assert int(row["n_neighbours_not_run"]) == 1, "the probe really was withheld"
    # A demonstrated POSITIVE survives: support is monotone in the number of
    # probes, so the withheld probe could only have added a confirmation.
    assert row["local_support_label"] == nc.LABEL_BROADLY_SUPPORTED
    assert bool(row["eligible_for_representation"])
    # Every other anchor is untouched: one undispatched probe does not
    # contaminate the rest of the stage.
    others = support[support["candidate_id"] != first]
    assert (others["local_support_label"] == nc.LABEL_BROADLY_SUPPORTED).all()


def test_incomplete_work_blocks_a_NEGATIVE_label_but_not_a_positive_one(tmp_path):
    """The asymmetry in ``classify_local_support``, stated as a test.

    A negative label needs the complete design, because the missing probe might
    have been the confirming one.  A positive label does not, because support
    is monotone in the number of probes.  Getting this backwards either throws
    away real evidence or reports an execution failure as a finding about the
    model -- and the second is what spec sec. 3 and sec. 9 forbid.
    """
    def neighbour(index: int, *, confirmed: bool, state: str) -> nc.NeighbourEvidence:
        return nc.NeighbourEvidence(
            design_id=f"D{index}", kernel=nc.KERNEL_AMBIENT, scale=0.025,
            direction_class="ambient_isotropic", vector_hash=f"h{index}",
            distance=0.03, evidence=state, confirmed=confirmed,
        )

    confirmed = [
        neighbour(i, confirmed=True, state=nc.EVIDENCE_CONFIRMED) for i in range(3)
    ]
    rejected = [
        neighbour(i, confirmed=False, state=nc.EVIDENCE_NO_SUPPORT)
        for i in range(10, 13)
    ]
    missing = neighbour(99, confirmed=False, state=nc.EVIDENCE_NOT_RUN)
    failed = neighbour(98, confirmed=False, state=nc.EVIDENCE_EXECUTION_FAILURE)

    kwargs = dict(
        smaller_ambient_scale=0.025, min_confirming=2, min_direction_classes=2, max_abs_direction_cosine=0.99,
    )
    # Complete and positive -> supported.
    assert nc.classify_local_support(
        confirmed, designed_neighbours=3, **kwargs
    ).label == nc.LABEL_BROADLY_SUPPORTED
    # Positive with missing or failed work -> STILL supported.
    for extra in (missing, failed):
        result = nc.classify_local_support(
            [*confirmed, extra], designed_neighbours=4, **kwargs
        )
        assert result.label == nc.LABEL_BROADLY_SUPPORTED, (
            "a demonstrated positive must not be discarded over unrelated "
            "missing work"
        )
        assert result.eligible
    # Complete and negative -> the negative label is allowed.
    assert nc.classify_local_support(
        rejected, designed_neighbours=3, **kwargs
    ).label == nc.LABEL_NO_LOCAL_SUPPORT
    # Negative with missing or failed work -> unresolved, never negative.
    for extra, word in ((missing, "not run"), (failed, "execution failure")):
        result = nc.classify_local_support(
            [*rejected, extra], designed_neighbours=4, **kwargs
        )
        assert result.label == nc.LABEL_UNRESOLVED, (
            "an execution failure or undispatched probe must never read as "
            "'no local support detected'"
        )
        assert word in result.reason
        assert not result.eligible


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
    assert len(edges) == len(nodes) * (len(nodes) - 1) // 2
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
    harness.prepare(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
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


def test_m1_bridges_preserve_prerequisite_lineage_through_evaluate_preflight(
    tmp_path,
):
    """The bridges graph metadata may not erase the sealed M1 gate identity."""
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    parent = tmp_path / "minimal_m1_confirmation_parent"
    parent.mkdir()
    acceptance = runner._freeze_acceptance_package(
        parent, runner._validate_declared_acceptance_package(harness.plan)
    )
    prerequisites = harness.plan.get("production_prerequisites")
    gate_source = Path(prerequisites["identity_gate"])
    gate_path = parent / runner.nig.GATE_FILENAME
    gate_path.write_bytes(gate_source.read_bytes())
    gate = json.loads(gate_path.read_text())
    gate_sha = ccs.sha256_file(gate_path)

    anchors = ne.read_frame_exact(harness.anchors)
    anchors["vector_hash"] = [
        nc.vector_hash(
            {name: float(row[name]) for name in harness.space.names},
            names=harness.space.names,
        )
        for _, row in anchors.iterrows()
    ]
    ne.atomic_write_frame(parent / "anchors.csv", anchors)
    ne.atomic_write_frame(parent / "local_support.csv", pd.DataFrame([
        {
            "candidate_id": str(row["candidate_id"]),
            "endpoint_confirmed": True,
            "local_support_label": nc.LABEL_BROADLY_SUPPORTED,
            "eligible_for_representation": True,
        }
        for _, row in anchors.iterrows()
    ]))
    parent_ledger = ne.Ledger(
        stage=nc.STAGE_CONFIRMATION, model=nc.MODEL_M1, units=()
    )
    parent_ledger.save(parent)
    ne.write_stage_manifest(
        parent,
        stage=nc.STAGE_CONFIRMATION,
        model=nc.MODEL_M1,
        status=nc.STATUS_COMPLETE,
        ledger=parent_ledger,
        contract=harness.plan.manifest_contract(),
        parent_manifest=None,
        extra={
            "acceptance_input_identity": acceptance[
                "acceptance_input_identity"
            ],
            "acceptance_package_identity": acceptance,
            "identity_gate_source": str(gate_source),
            "identity_gate_source_sha256": gate_sha,
            "identity_gate_sha256": gate_sha,
            "identity_gate_schema": gate["schema"],
        },
    )
    harness.dirs[nc.STAGE_CONFIRMATION] = parent
    bridges = harness.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)

    lineage_fields = (
        "identity_gate_source",
        "identity_gate_source_sha256",
        "identity_gate_sha256",
        "identity_gate_schema",
    )
    manifests = {
        nc.STAGE_CONFIRMATION: json.loads((parent / "manifest.json").read_text()),
        nc.STAGE_BRIDGES: json.loads((bridges / "manifest.json").read_text()),
    }
    parent_lineage = {
        field: manifests[nc.STAGE_CONFIRMATION]["extra"][field]
        for field in lineage_fields
    }
    assert {
        field: manifests[nc.STAGE_BRIDGES]["extra"][field]
        for field in lineage_fields
    } == parent_lineage
    assert (
        manifests[nc.STAGE_BRIDGES]["extra"]["acceptance_package_identity"]
        == manifests[nc.STAGE_CONFIRMATION]["extra"][
            "acceptance_package_identity"
        ]
    )

    # A sparse shard still executes the complete immutable-artifact,
    # acceptance-package, identity-gate and M1 prerequisite preflight. Before
    # the fix, bridges failed here because graph metadata assignment deleted
    # the identity-gate manifest fields.
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path),
        "--stage-dir", str(bridges), "--shard", "9999", "--of", "10000",
        "--evaluator", "surrogate",
    ]) == 0


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


def _chain_with_negative_local_support(tmp_path: Path) -> Harness:
    """Local assessment negative, bridges confirming -- on ONE acceptance basis.

    This case used to be built by merging the confirmation stage against
    everything-fails acceptance inputs and the bridges stage against
    everything-confirms inputs.  That was the hole the cross-stage identity
    check now closes, and a test may not require the very defect it is meant
    to guard against, so the geometry does the work instead.

    ``surrogate_islands`` is plausible only within a small radius of three
    island centres, spaced along the first normalised coordinate.  The two
    anchors sit one spacing beyond each end, so:

    * every ambient probe around an anchor is at least 0.10 from the nearest
      island and fails, making both anchors ``no_local_support_detected``;
    * the declared 25/50/75% bridge fractions interpolate exactly onto the
      three islands, so every probe on the edge confirms.

    One observed-value file, one base-variance file, one target register, one
    acceptance rule, for every stage in the chain -- which is the point.
    """
    harness = Harness(
        tmp_path,
        confirming=False,  # observed = 0, B_i = 1, so recomputed I_max IS the field
        evaluator="surrogate_islands",
        anchors=_write_island_anchors(tmp_path / "island_anchors.csv", fx.m0_space()),
    )
    harness.full_local_stage(nc.STAGE_DISCOVERY)
    harness.prepare(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
    harness.full_local_stage(nc.STAGE_CONFIRMATION, parent=nc.STAGE_PROMOTION)
    harness.prepare(nc.STAGE_BRIDGES, parent=nc.STAGE_CONFIRMATION)
    harness.evaluate(nc.STAGE_BRIDGES)
    assert harness.merge(nc.STAGE_BRIDGES) == 0, "merge bridges failed"
    return harness


def test_the_island_field_is_plausible_only_on_its_islands():
    """The fixture's geometry, asserted rather than assumed.

    If this drifts, ``_chain_with_negative_local_support`` stops testing what
    it claims to test, so the field's two relevant properties are checked
    directly: the islands are plausible and an anchor one spacing away is not.
    """
    centres = runner.ISLAND_CENTRES
    spacing = centres[1] - centres[0]
    n = 33
    for position in centres:
        assert runner.island_field_i_max(runner.island_centre(position, n)) <= 3.0
    for position in (centres[0] - spacing, centres[-1] + spacing):
        anchor = runner.island_centre(position, n)
        assert runner.island_field_i_max(anchor) > 3.0
        # And so is everything an ambient probe at the largest declared scale
        # could reach from there.
        for scale in (0.025, 0.05):
            moved = anchor.copy()
            moved[0] += scale if position < 0.5 else -scale
            assert runner.island_field_i_max(moved) > 3.0


# ==========================================================================
# One acceptance basis per lineage (the cross-stage identity check)
# ==========================================================================

def _variant_plan(tmp_path: Path, name: str, **overrides) -> Path:
    """A second signed plan file, so the baseline one is not overwritten."""
    path = tmp_path / name
    path.write_text(json.dumps(fx.signed_plan_dict(**overrides), indent=1))
    return path


def _refused_because(capsys) -> str:
    """The refusal text a CLI command printed, so a test can check the reason.

    An exit code of 1 only says "some contract error". These tests are about
    one specific refusal, so they assert which one.
    """
    return capsys.readouterr().err


def _chain_to_a_prepared_child(tmp_path: Path) -> Harness:
    """Discovery merged and classified, promotion prepared from it."""
    harness = Harness(tmp_path)
    harness.full_local_stage(nc.STAGE_DISCOVERY)
    harness.prepare(nc.STAGE_PROMOTION, parent=nc.STAGE_DISCOVERY)
    harness.prepare(nc.STAGE_CONFIRMATION, parent=nc.STAGE_PROMOTION)
    harness.evaluate(nc.STAGE_CONFIRMATION)
    return harness


def test_prepare_seals_the_reviewed_acceptance_package(tmp_path):
    harness = Harness(tmp_path)
    stage = harness.prepare(nc.STAGE_DISCOVERY)
    manifest = json.loads((stage / "manifest.json").read_text())
    indexed = {
        row["path"] for row in json.loads((stage / "artifacts.json").read_text())["files"]
    }
    expected = {
        runner.nai.PROVENANCE_FILENAME,
        runner.nai.OBSERVED_FILENAME,
        runner.nai.BASE_VARIANCE_FILENAME,
        runner.nai.COMPONENT_FILENAME,
        runner.FROZEN_ACCEPTANCE_IDENTITY_FILENAME,
    }
    assert expected <= indexed
    package = manifest["extra"]["acceptance_package_identity"]
    assert package["acceptance_input_identity"] == manifest["extra"][
        "acceptance_input_identity"
    ]
    assert package["approved_by"].startswith("verification-suite-fixture")


def test_evaluate_refuses_a_rewritten_sealed_acceptance_input(tmp_path):
    harness = Harness(tmp_path)
    stage = harness.prepare(nc.STAGE_DISCOVERY)
    observed = json.loads((stage / runner.nai.OBSERVED_FILENAME).read_text())
    observed[TARGETS[0]] += 1.0
    (stage / runner.nai.OBSERVED_FILENAME).write_text(json.dumps(observed))
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path),
        "--stage-dir", str(stage), "--shard", "0", "--of", "1",
        "--evaluator", "surrogate",
    ]) == 1
    assert not (stage / "results").exists()


def test_merge_consumes_sealed_inputs_without_cli_paths(tmp_path):
    harness = Harness(tmp_path)
    stage = harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY)
    assert runner.main([
        "merge", "--plan", str(harness.plan_path), "--stage-dir", str(stage),
    ]) == 0
    assert (stage / "candidate_scores.csv").exists()


def test_prepare_refuses_a_review_required_acceptance_package(tmp_path, capsys):
    harness = Harness(tmp_path)
    provenance = Path(
        json.loads(harness.plan_path.read_text())["production_prerequisites"][
            "acceptance_inputs"
        ]
    )
    payload = json.loads(provenance.read_text())
    payload.update({
        "status": "FINAL_REVIEW_REQUIRED",
        "production_eligible": False,
        "approved": False,
        "approved_by": None,
        "approved_utc": None,
    })
    provenance.write_text(json.dumps(payload))
    assert runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "refused_stage"),
        "--model", nc.MODEL_M0, "--stage", nc.STAGE_DISCOVERY,
        "--anchors", str(harness.anchors),
    ]) == 1
    reason = _refused_because(capsys)
    assert "status must equal 'FINAL'" in reason
    assert not (tmp_path / "refused_stage").exists()


def test_the_parent_acceptance_identity_is_frozen_into_the_child_at_prepare(tmp_path):
    """The freeze itself: complete, and covering every field the spec names."""
    harness = _chain_to_a_prepared_child(tmp_path)
    child = harness.dirs[nc.STAGE_PROMOTION]
    frozen = json.loads((child / runner.PARENT_IDENTITY_FILENAME).read_text())
    identity = frozen["acceptance_input_identity"]

    for field in nc.ACCEPTANCE_IDENTITY_FIELDS:
        assert field in identity, f"the frozen identity omits {field}"
    assert identity["identity_sha256"]
    parent_manifest = json.loads(
        (harness.dirs[nc.STAGE_DISCOVERY] / "manifest.json").read_text()
    )
    assert parent_manifest["extra"]["acceptance_input_identity"] == identity
    assert frozen["acceptance_package_identity"] == parent_manifest["extra"][
        "acceptance_package_identity"
    ]
    # And it is covered by the child's own artifact index, so it cannot be
    # quietly edited later.
    index = json.loads((child / "artifacts.json").read_text())
    assert runner.PARENT_IDENTITY_FILENAME in {row["path"] for row in index["files"]}


def test_deleting_the_frozen_parent_identity_refuses_merge_before_outputs(tmp_path):
    harness = _chain_to_a_prepared_child(tmp_path)
    child = harness.dirs[nc.STAGE_CONFIRMATION]
    (child / runner.PARENT_IDENTITY_FILENAME).unlink()
    assert harness.merge(nc.STAGE_CONFIRMATION) == 1
    assert not (child / "seed_results.csv").exists()
    assert not (child / "candidate_scores.csv").exists()


def test_rewriting_the_frozen_parent_identity_refuses_classification(tmp_path):
    harness = _chain_to_a_prepared_child(tmp_path)
    child = harness.dirs[nc.STAGE_CONFIRMATION]
    assert harness.merge(nc.STAGE_CONFIRMATION) == 0
    identity_path = child / runner.PARENT_IDENTITY_FILENAME
    payload = json.loads(identity_path.read_text())
    payload["acceptance_input_identity"]["observed_sha256"] = "0" * 64
    identity_path.write_text(json.dumps(payload))
    assert runner.main([
        "classify", "--plan", str(harness.plan_path), "--stage-dir", str(child),
    ]) == 1
    assert not (child / "local_support.csv").exists()


def test_the_identity_names_every_input_the_specification_requires():
    """Target order, register, observed, base variance, scoring, rule."""
    assert set(nc.ACCEPTANCE_IDENTITY_FIELDS) == {
        "scoring_version", "target_order", "target_register_sha256",
        "target_contract_sha256", "observed_sha256", "base_variance_sha256",
        "discrepancy", "acceptance_rule",
    }
    # The observed and base-variance hashes are the two that were missing, and
    # they are the two the plan cannot re-derive, so they must not appear in
    # the plan-derived subset.
    assert "observed_sha256" not in nc.PLAN_DERIVED_ACCEPTANCE_FIELDS
    assert "base_variance_sha256" not in nc.PLAN_DERIVED_ACCEPTANCE_FIELDS


def test_a_child_merged_against_different_observed_values_is_refused(tmp_path, capsys):
    """The exact hole: a child scored against other observed values z_i."""
    harness = _chain_to_a_prepared_child(tmp_path)
    other = tmp_path / "other_observed.json"
    other.write_text(json.dumps({name: 104.0 for name in TARGETS}))
    code = harness.merge(nc.STAGE_CONFIRMATION, observed=other)
    assert code == 1
    reason = _refused_because(capsys)
    assert "observed_sha256" in reason and "observed target values" in reason
    assert not (harness.dirs[nc.STAGE_CONFIRMATION] / "candidate_scores.csv").exists(), (
        "a refused merge must not leave a scored table behind"
    )


def test_a_child_merged_against_different_base_variances_is_refused(tmp_path, capsys):
    """And the denominator, which is just as much of the acceptance rule."""
    harness = _chain_to_a_prepared_child(tmp_path)
    variance = tmp_path / "wider_variance.json"
    variance.write_text(json.dumps({name: 250000.0 for name in TARGETS}))
    assert harness.merge(nc.STAGE_CONFIRMATION, variance=variance) == 1
    reason = _refused_because(capsys)
    assert "base_variance_sha256" in reason and "base variances" in reason


def test_a_child_merged_against_a_changed_discrepancy_is_refused(tmp_path, capsys):
    """The discrepancy is frozen globally and cannot be changed by a child plan."""
    harness = _chain_to_a_prepared_child(tmp_path)
    altered = _variant_plan(
        tmp_path, "plan_other_discrepancy.json",
        **{"acceptance.discrepancy": 0.25},
    )
    code = runner.main([
        "merge", "--plan", str(altered),
        "--stage-dir", str(harness.dirs[nc.STAGE_CONFIRMATION]),
        "--observed", str(harness.observed), "--base-variance", str(harness.variance),
    ])
    assert code == 1
    reason = _refused_because(capsys)
    assert "may not change the primary model-discrepancy rule" in reason


def test_a_child_merged_against_changed_register_contents_is_refused(tmp_path, capsys):
    """Same target names, same order, changed register contents.

    The identity pins the register by hash, so **any** change to it
    invalidates the comparison -- deliberately, because deciding which fields
    of a target register are acceptance-relevant is exactly the judgement a
    fail-closed check should not be making.  The edit here is a version
    string, which is the mildest change the register admits; a changed ``z_i``
    or ``v_obs`` is caught by the same comparison.
    """
    harness = _chain_to_a_prepared_child(tmp_path)
    register_path = Path(harness.plan.targets.register_path)
    if not register_path.is_absolute():
        register_path = ROOT / register_path
    edited = tmp_path / "edited_register.json"
    payload = json.loads(register_path.read_text())
    payload["version"] = str(payload.get("version", "")) + "-edited"
    edited.write_text(json.dumps(payload, indent=1))
    altered = _variant_plan(
        tmp_path, "plan_other_register.json",
        **{
            "target_contract.register_path": str(edited),
            "target_contract.register_sha256": ccs.sha256_file(edited),
        },
    )
    code = runner.main([
        "merge", "--plan", str(altered),
        "--stage-dir", str(harness.dirs[nc.STAGE_CONFIRMATION]),
        "--observed", str(harness.observed), "--base-variance", str(harness.variance),
    ])
    assert code == 1
    reason = _refused_because(capsys)
    assert "target_register_sha256" in reason


def test_a_child_merged_against_a_reordered_target_order_is_refused(tmp_path, capsys):
    """A reordered contract at merge time is refused -- by the earlier gate.

    Recorded with the gate that actually fires, rather than attributed to the
    new check: each work unit carries the frozen target schema, so a plan whose
    order was reversed after ``prepare`` is rejected by the ledger's own schema
    comparison in ``merge_seed_results``, before any score exists to compare.
    The acceptance identity is the backstop behind it -- ``target_order`` and
    ``target_contract_sha256`` are both compared, which
    ``test_every_field_of_the_identity_is_compared_not_only_the_summary_hash``
    drives directly -- and it is what would catch a reordering that somehow
    reached a scored table.
    """
    harness = _chain_to_a_prepared_child(tmp_path)
    reordered = _variant_plan(
        tmp_path, "plan_reordered_targets.json",
        **{"target_contract.frozen_order": list(reversed(list(TARGETS)))},
    )
    code = runner.main([
        "merge", "--plan", str(reordered),
        "--stage-dir", str(harness.dirs[nc.STAGE_CONFIRMATION]),
        "--observed", str(harness.observed), "--base-variance", str(harness.variance),
    ])
    assert code == 1
    reason = _refused_because(capsys)
    assert "acceptance-input identity" in reason and "target_order" in reason
    assert not (harness.dirs[nc.STAGE_CONFIRMATION] / "candidate_scores.csv").exists()


def test_every_field_of_the_identity_is_compared_not_only_the_summary_hash(tmp_path):
    """One field at a time, including the target order.

    The end-to-end tests above vary one real input each.  This one drives the
    comparison directly so that *every* field is shown to be load-bearing,
    including the two -- the scoring version and the acceptance rule -- that no
    command line can currently vary.
    """
    plan = fx.signed_plan(tmp_path)
    base = plan.acceptance_identity(observed_sha256="aa", base_variance_sha256="bb")
    mutations = {
        "scoring_version": "nroy_scoring/9.9.9",
        "target_order": list(reversed(base["target_order"])),
        "target_register_sha256": "0" * 64,
        "target_contract_sha256": "1" * 64,
        "observed_sha256": "cc",
        "base_variance_sha256": "dd",
        "discrepancy": float(base["discrepancy"]) + 1.0,
        "acceptance_rule": "I_max <= 4.0",
    }
    assert set(mutations) == set(nc.ACCEPTANCE_IDENTITY_FIELDS)
    for field, value in mutations.items():
        nc.assert_acceptance_identity(base, base, what="unchanged")
        with pytest.raises(ContractError) as excinfo:
            nc.assert_acceptance_identity(base, {**base, field: value}, what="changed")
        assert field in str(excinfo.value)


def test_an_absent_identity_is_a_refusal_not_a_pass():
    """A stage that cannot show its acceptance basis is refused."""
    for frozen, current in (({}, {"a": 1}), ({"a": 1}, {}), (None, None)):
        with pytest.raises(ContractError):
            nc.assert_acceptance_identity(frozen, current, what="missing", fields=("a",))


def test_classification_is_refused_when_the_plan_changed_after_the_merge(tmp_path, capsys):
    """Before any label is written, not after."""
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    harness.evaluate(nc.STAGE_DISCOVERY)
    assert harness.merge(nc.STAGE_DISCOVERY) == 0
    altered = _variant_plan(
        tmp_path, "plan_classify_drift.json", **{"acceptance.discrepancy": 0.75}
    )
    code = runner.main([
        "classify", "--plan", str(altered),
        "--stage-dir", str(harness.dirs[nc.STAGE_DISCOVERY]),
    ])
    assert code == 1
    reason = _refused_because(capsys)
    assert "may not change the primary model-discrepancy rule" in reason
    assert not (harness.dirs[nc.STAGE_DISCOVERY] / "local_support.csv").exists()


def test_edge_verdicts_are_refused_when_the_plan_changed_after_the_bridge_merge(tmp_path, capsys):
    """The same gate in front of the graph, which is where it matters most."""
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    altered = _variant_plan(
        tmp_path, "plan_regions_drift.json", **{"acceptance.discrepancy": 0.75}
    )
    code = runner.main([
        "regions", "--plan", str(altered),
        "--parent-dir", str(harness.dirs[nc.STAGE_BRIDGES]),
        "--out", str(tmp_path / "regions_drift"),
    ])
    assert code == 1
    reason = _refused_because(capsys)
    assert "may not change the primary model-discrepancy rule" in reason
    assert not (tmp_path / "regions_drift" / "edge_verdicts.csv").exists()


def test_the_regions_package_records_the_basis_its_verdicts_were_reached_on(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    out = harness.regions()
    manifest = json.loads((out / "manifest.json").read_text())
    bridges = json.loads((harness.dirs[nc.STAGE_BRIDGES] / "manifest.json").read_text())
    assert (
        manifest["extra"]["acceptance_input_identity"]
        == bridges["extra"]["acceptance_input_identity"]
    )


def test_a_point_with_neither_local_nor_region_support_gets_no_representative(tmp_path):
    """A zero-promotion discovery stops before confirmation or representation."""
    harness = Harness(tmp_path, confirming=False)
    harness.full_local_stage(nc.STAGE_DISCOVERY)
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "promotion"), "--model", nc.MODEL_M0,
        "--stage", nc.STAGE_PROMOTION,
        "--parent-dir", str(harness.dirs[nc.STAGE_DISCOVERY]),
    ])
    assert code == 1
    assert not (tmp_path / "promotion" / "representatives.csv").exists()


def test_region_support_is_reachable_without_local_support(tmp_path):
    """Bridge search cannot resurrect candidates excluded before confirmation."""
    harness = Harness(
        tmp_path, confirming=False, evaluator="surrogate_islands",
        anchors=_write_island_anchors(tmp_path / "island_anchors.csv", fx.m0_space()),
    )
    harness.full_local_stage(nc.STAGE_DISCOVERY)
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "promotion"), "--model", nc.MODEL_M0,
        "--stage", nc.STAGE_PROMOTION,
        "--parent-dir", str(harness.dirs[nc.STAGE_DISCOVERY]),
    ])
    assert code == 1


def test_eligibility_records_its_ground_rather_than_a_bare_flag(tmp_path):
    harness = Harness(tmp_path)
    harness.chain_to_bridges()
    out = harness.regions()
    eligibility = ne.read_frame_exact(out / "representation_eligibility.csv")
    assert set(eligibility["eligibility_ground"]) <= {
        "local_support", "region_support_via_sampled_path",
        "local_and_region_support", "no_local_or_region_support_detected",
    }
    for _, row in eligibility.iterrows():
        has_local = bool(row["has_local_support"])
        has_region = bool(row["has_region_support"])
        assert bool(row["eligible_for_representation"]) == (has_local or has_region)
    manifest = json.loads((out / "manifest.json").read_text())
    assert "local/region support" in manifest["extra"]["eligibility_rule"]


def test_a_singleton_component_has_no_region_support(tmp_path):
    """Region support needs a supporting edge, and a singleton has none."""
    nodes = pd.DataFrame([
        {"candidate_id": "A", "local_support_label": nc.LABEL_NO_LOCAL_SUPPORT,
         "eligible_for_representation": False, "u:x": 0.1},
        {"candidate_id": "B", "local_support_label": nc.LABEL_NO_LOCAL_SUPPORT,
         "eligible_for_representation": False, "u:x": 0.9},
    ])
    lonely = ng.Component(
        component_id="R001", members=("A",), supporting_edges=(),
        unresolved_edges=("A--B",),
    )
    joined = ng.Component(
        component_id="R002", members=("B",), supporting_edges=("B--C",),
        unresolved_edges=(),
    )
    support = nas.region_support(nodes, [lonely, joined])
    assert support["A"] is False
    assert support["B"] is True
    eligible, ground = nas.representation_eligibility(nodes, [lonely, joined])
    assert eligible == {"A": False, "B": True}
    assert ground["A"] == "no_local_or_region_support_detected"
    assert ground["B"] == "region_support_via_sampled_path"


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


def test_representation_changing_unresolved_edges_publish_no_final_shortlist(
    tmp_path, monkeypatch,
):
    """A blocking edge makes regions conditional, never final or parentable."""
    harness = Harness(tmp_path)
    bridges = harness.chain_to_bridges()
    edge_id = str(ne.read_frame_exact(bridges / "candidate_edges.csv").iloc[0]["edge_id"])
    monkeypatch.setattr(
        ng, "unresolved_changes_representation", lambda groups, verdicts: [edge_id]
    )

    out = tmp_path / "regions_blocked"
    code = runner.main([
        "regions", "--plan", str(harness.plan_path),
        "--parent-dir", str(bridges), "--out", str(out),
    ])
    assert code == 3
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == nc.STATUS_PARTIAL
    assert manifest["parentable"] is False
    assert manifest["extra"]["n_representatives"] == 0
    assert manifest["extra"]["n_provisional_representatives"] > 0
    assert (out / "provisional_representatives.csv").exists()
    assert not (out / "representatives.csv").exists()


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


def test_some_completed_rows_in_a_partial_stage_are_never_scored(tmp_path):
    """Completion-time selection cannot turn one finished shard into evidence."""
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir),
        "--shard", "0", "--of", "2", "--evaluator", "surrogate",
    ]) == 0
    assert harness.merge(nc.STAGE_DISCOVERY) == 0
    seed_rows = ne.read_frame_exact(stage_dir / "seed_results.csv")
    assert 0 < len(seed_rows) < len(ne.Ledger.load(stage_dir).units)
    assert not (stage_dir / "candidate_scores.csv").exists()
    assert runner.main([
        "classify", "--plan", str(harness.plan_path), "--stage-dir", str(stage_dir),
    ]) == 1


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


def test_a_partial_merge_remains_resumable_and_can_be_merged_complete(tmp_path):
    """A checkpoint seal must protect science without freezing runtime growth."""
    harness = Harness(tmp_path)
    harness.prepare(nc.STAGE_DISCOVERY)
    stage_dir = harness.dirs[nc.STAGE_DISCOVERY]

    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path),
        "--stage-dir", str(stage_dir), "--shard", "0", "--of", "2",
        "--evaluator", "surrogate",
    ]) == 0
    assert harness.merge(nc.STAGE_DISCOVERY) == 0
    assert json.loads((stage_dir / "manifest.json").read_text())["status"] == nc.STATUS_PARTIAL

    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path),
        "--stage-dir", str(stage_dir), "--shard", "1", "--of", "2",
        "--evaluator", "surrogate",
    ]) == 0
    assert harness.merge(nc.STAGE_DISCOVERY) == 0
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    assert manifest["status"] == nc.STATUS_COMPLETE
    assert manifest["n_incomplete"] == 0
    assert (stage_dir / "candidate_scores.csv").exists()


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
            neighbour_count=2, max_directions=2,
            neighbour_rule="k_nearest_in_normalised_metric",
            confirmed_difference_rule="nearest_in_normalised_metric_then_candidate_id",
        )


def test_a_declared_source_with_only_zero_differences_is_refused():
    space = fx.m0_space()
    params = fx.interior_vector(space)
    anchor = nd.Anchor.build("A0", params, space)

    with pytest.raises(ContractError, match="no usable nonzero direction"):
        nd.correlation_following_directions(
            anchor, space, source_pool=[nd.SOURCE_LOW_J_ARCHIVE],
            archive=[params], archive_ids=["ARCHIVE_DUPLICATE"], archive_j=[0.1],
            neighbour_count=1, max_directions=1,
            neighbour_rule="lowest_j_then_distance",
            confirmed_difference_rule="nearest_in_normalised_metric_then_candidate_id",
        )

    with pytest.raises(ContractError, match="no usable nonzero direction"):
        nd.correlation_following_directions(
            anchor, space, source_pool=[nd.SOURCE_CONFIRMED_DIFFERENCES],
            confirmed=[params, dict(params)], confirmed_ids=["A0", "A1"],
            neighbour_count=1, max_directions=1,
            neighbour_rule="k_nearest_in_normalised_metric",
            confirmed_difference_rule="nearest_in_normalised_metric_then_candidate_id",
        )
    with pytest.raises(ContractError, match="no other confirmed vector"):
        nd.correlation_following_directions(
            anchor, space, source_pool=[nd.SOURCE_CONFIRMED_DIFFERENCES],
            confirmed=[anchor.params], confirmed_ids=["A0"],
            neighbour_count=2, max_directions=2,
            neighbour_rule="k_nearest_in_normalised_metric",
            confirmed_difference_rule="nearest_in_normalised_metric_then_candidate_id",
        )


def test_an_undeclared_source_contributes_nothing():
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 3, seed=11)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    directions = nd.correlation_following_directions(
        anchor, space, source_pool=[nd.SOURCE_CONFIRMED_DIFFERENCES],
        archive=vectors[1:], archive_ids=["Z1", "Z2"], archive_j=[0.1, 0.2],
        confirmed=vectors, confirmed_ids=["A0", "A1", "A2"],
        neighbour_count=2, max_directions=4,
        neighbour_rule="k_nearest_in_normalised_metric",
        confirmed_difference_rule="nearest_in_normalised_metric_then_candidate_id",
    )
    assert directions
    assert {label for _, label, _ in directions} == {nd.DIRCLASS_CONFIRMED_DIFF}, (
        "an archive pool that was not declared must not reach the design"
    )


def test_confirmed_difference_selection_uses_distance_not_candidate_name():
    space = fx.m0_space()
    anchor_u = np.full(space.n, 0.5)
    far_u = anchor_u.copy()
    near_u = anchor_u.copy()
    far_u[0] = 0.8
    near_u[0] = 0.6
    anchor_params = space.as_dict(space.space.denormalise(anchor_u))
    far_params = space.as_dict(space.space.denormalise(far_u))
    near_params = space.as_dict(space.space.denormalise(near_u))
    anchor = nd.Anchor.build("M_ANCHOR", anchor_params, space)

    selected = nd.confirmed_difference_directions(
        anchor,
        space,
        confirmed=[anchor_params, far_params, near_params],
        confirmed_ids=["M_ANCHOR", "A_FAR_LEXICOGRAPHICALLY_FIRST", "Z_NEAR"],
        max_directions=1,
        selection_rule="nearest_in_normalised_metric_then_candidate_id",
    )
    assert len(selected) == 1
    assert selected[0][2] == ("M_ANCHOR", "Z_NEAR")


def test_retired_or_undeclared_direction_rules_are_not_silently_reinterpreted():
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 3, seed=31)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    with pytest.raises(ContractError, match="unknown archive neighbour rule"):
        nd.archive_directions(
            anchor, space, archive=vectors[1:], archive_ids=["A1", "A2"],
            archive_j=[0.1, 0.2], neighbour_count=2, max_directions=1,
            neighbour_rule="k_nearest_low_J_archive_vectors",
        )
    with pytest.raises(ContractError, match="unknown confirmed-difference selection rule"):
        nd.confirmed_difference_directions(
            anchor, space, confirmed=vectors, confirmed_ids=["A0", "A1", "A2"],
            max_directions=1, selection_rule="candidate_id_order",
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
            neighbour_count=2, max_directions=2,
            neighbour_rule="lowest_j_then_distance",
        )


def test_the_low_j_rule_consults_in_j_order(tmp_path):
    space = fx.m0_space()
    vectors = fx.valid_interior_vectors(space, 4, seed=9)
    anchor = nd.Anchor.build("A0", vectors[0], space)
    # Z3 is the furthest but has the lowest J, so the low-J rule must take it
    # first while the distance rule must not.
    by_j = nd.archive_directions(
        anchor, space, archive=vectors[1:], archive_ids=["Z1", "Z2", "Z3"],
        archive_j=[9.0, 5.0, 0.1], neighbour_count=1, max_directions=1,
        neighbour_rule="lowest_j_then_distance",
    )
    assert [parents[1] for _, _, parents in by_j] == ["Z3"]
    by_distance = nd.archive_directions(
        anchor, space, archive=vectors[1:], archive_ids=["Z1", "Z2", "Z3"],
        archive_j=[9.0, 5.0, 0.1], neighbour_count=3, max_directions=3,
        neighbour_rule="k_nearest_in_normalised_metric",
    )
    assert len(by_distance) == 3


def test_the_archive_neighbour_count_is_its_own_decision():
    """It remains local-design input; the complete graph needs no neighbour count."""
    assert "archive_following.neighbour_count" in nc.REQUIRED_DECISIONS
    assert "graph.neighbour_count" not in nc.REQUIRED_DECISIONS
    assert "graph.edge_proposal_rule" in nc.REQUIRED_DECISIONS
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
            "archive_following.quota": 8,
            "archive_following.source_pool": [
                nd.SOURCE_LOW_J_ARCHIVE, nd.SOURCE_CONFIRMED_DIFFERENCES,
                nd.SOURCE_CROSS_BLOCK,
            ],
            "archive_following.neighbour_rule": "lowest_j_then_distance",
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
        {
            "design_id": f"D{index}", "i_max": 1.0 + index,
            **{
                f"residual:{name}": float(
                    frame[frame["design_id"] == f"D{index}"][f"target:{name}"].mean()
                )
                for name in TARGETS
            },
        }
        for index in range(6)
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
    diagnosis = adapter.base_config["transmission_model"]["disease"]["diagnosis"]
    diagnosis.pop("stage_structured_diagnosis")
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
    with pytest.raises(ContractError, match="missing.*stage_structured_diagnosis"):
        adapter.config_for(unit, vector)


def test_m1_injection_lands_p_at_the_signed_path_when_the_block_exists(tmp_path):
    """With the block present, ``p`` lands exactly where the plan says."""
    harness = Harness(
        tmp_path, model=nc.MODEL_M1,
        **{"p_parameter.symptom_start_year": 1990},
    )
    adapter = runner.ProductionEvaluator(harness.plan, root=ROOT)
    base_before = copy.deepcopy(adapter.base_config)
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
    assert adapter.base_config == base_before
    assert _dig(config, fx.P_CONFIG_PATH) == pytest.approx(0.25)
    assert _dig(
        config,
        "transmission_model.disease.diagnosis.stage_structured_diagnosis.enabled.value",
    ) is True
    # And the routine hazard scale the identity depends on is still pinned.
    assert _dig(
        config,
        "transmission_model.disease.diagnosis.stage_structured_diagnosis."
        "routine_hazard_scale.value",
    ) == 1.0
    assert _dig(
        config,
        "transmission_model.disease.diagnosis.stage_structured_diagnosis."
        "symptom_start_year.value",
    ) == 1990


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


def test_production_config_route_removes_m1_block_from_m0_but_injects_it_for_m1(
    tmp_path,
):
    plan_path = fx.write_signed_plan(tmp_path)
    raw = json.loads(plan_path.read_text())
    base_path = Path(raw["models"]["base_config"])
    base = json.loads(base_path.read_text())
    node = base
    for key in runner.nig.PROFILE_PATH[:-1]:
        node = node[key]
    assert node[runner.nig.PROFILE_PATH[-1]]["enabled"]["value"] is False

    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    adapter = runner.ProductionEvaluator(plan, root=ROOT)
    m0, m1 = runner._model_spaces(plan)[nc.MODEL_M0], runner._model_spaces(plan)[nc.MODEL_M1]
    m0_params = m0.as_dict(m0.denormalise(np.full(m0.n, 0.5)))
    m1_params = nc.embed_m0_in_m1(m0, m1, m0_params, p_value=0.0)

    def unit(model_name, model, params):
        return ne.WorkUnit(
            stage=nc.STAGE_DISCOVERY, model=model_name, design_id=model_name, seed=1,
            vector_hash=nc.vector_hash(params, names=model.names),
            kernel=nc.KERNEL_ANCHOR, parent_ids=("identity",), params=params,
            parent_manifest_sha256="", code_sha256="code", config_sha256="config",
            target_schema=plan.targets.frozen_order,
        )

    m0_config = adapter.config_for(unit(nc.MODEL_M0, m0, m0_params), m0_params)
    m1_config = adapter.config_for(unit(nc.MODEL_M1, m1, m1_params), m1_params)

    def has_profile(config):
        current = config
        for key in runner.nig.PROFILE_PATH:
            if key not in current:
                return False
            current = current[key]
        return True

    assert not has_profile(m0_config)
    assert has_profile(m1_config)


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
    import inspect
    assert "assert_m1_identity_preconditions" in inspect.getsource(adapter.config_for)


# ==========================================================================
# The M0/M1 identity gate as a pre-production command
# ==========================================================================

def test_m1_prepare_refuses_without_the_identity_gate(tmp_path, capsys):
    """A green full suite does not establish the signed identity certificate."""
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    Path(plan.get("production_prerequisites")["identity_gate"]).unlink()
    anchors = _write_anchors(tmp_path / "anchors.csv", fx.m1_space())
    code = runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "m1"),
        "--anchors", str(anchors), "--model", nc.MODEL_M1,
    ])
    assert code == 1
    assert not (tmp_path / "m1" / "manifest.json").exists()
    assert "identity gate artifact does not exist" in capsys.readouterr().err


def test_m0_prepare_does_not_require_the_gate(tmp_path):
    """The identity is an M1 prerequisite; M0 is not gated on it."""
    plan_path = fx.write_signed_plan(tmp_path)
    anchors = _write_anchors(tmp_path / "anchors.csv", fx.m0_space())
    assert runner.main([
        "prepare", "--plan", str(plan_path), "--out", str(tmp_path / "m0"),
        "--anchors", str(anchors), "--model", nc.MODEL_M0,
    ]) == 0


def test_m1_prepare_rejects_valid_but_unsigned_certificate_paths(tmp_path, capsys):
    """Certificate validity cannot replace the plan's signed path authority."""
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    prerequisites = harness.plan.get("production_prerequisites")
    alternate_gate = tmp_path / "alternate_gate.json"
    alternate_gate.write_bytes(Path(prerequisites["identity_gate"]).read_bytes())
    assert runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "wrong_gate_stage"),
        "--model", nc.MODEL_M1,
        "--anchors", str(harness.anchors),
        "--identity-gate", str(alternate_gate),
    ]) == 1
    assert "exact path signed" in capsys.readouterr().err

    source_root = tmp_path / "signed_path_m0_source"
    source = Harness(source_root, model=nc.MODEL_M0)
    source.plan_path = harness.plan_path
    source.plan = harness.plan
    source.chain_to_confirmation()
    alternate_evidence = tmp_path / "alternate_confirmation.json"
    alternate_evidence.write_bytes(
        Path(prerequisites["m1_anchor_confirmation"]).read_bytes()
    )
    assert runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "wrong_evidence_stage"),
        "--model", nc.MODEL_M1, "--stage", nc.STAGE_DISCOVERY,
        "--anchors", str(harness.anchors),
        "--identity-gate", str(prerequisites["identity_gate"]),
        "--m1-anchor-evidence", str(alternate_evidence),
        "--m0-confirmed-dir", str(source.dirs[nc.STAGE_CONFIRMATION]),
    ]) == 1
    assert "exact path signed" in capsys.readouterr().err


def test_the_gate_check_refuses_a_missing_failed_incomplete_or_stale_artifact(tmp_path):
    import nroy_identity_gate as nig

    with pytest.raises(ContractError, match="does not exist"):
        nig.assert_gate_satisfied(tmp_path / "nowhere.json")

    failed = fx.write_identity_gate(tmp_path / "failed", passed=False)
    with pytest.raises(ContractError, match="did NOT pass"):
        nig.assert_gate_satisfied(failed)

    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    partial = fx.write_identity_gate(partial_dir, checks=[
        {"check": "identity_at_exact_p_zero", "passed": True, "detail": "", "evidence": {}}
    ])
    with pytest.raises(ContractError, match="missing check"):
        nig.assert_gate_satisfied(partial)

    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    fingerprint = dict(nig.code_fingerprint())
    fingerprint["run_v3.py"] = "0" * 64
    stale = fx.write_identity_gate(stale_dir, code_sha256=fingerprint)
    with pytest.raises(ContractError, match="stale"):
        nig.assert_gate_satisfied(stale)

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old = fx.write_identity_gate(old_dir, schema="m0_m1_identity_gate/0.9.0")
    with pytest.raises(ContractError, match="requires"):
        nig.assert_gate_satisfied(old)


def test_a_passing_current_artifact_satisfies_the_gate(tmp_path):
    import nroy_identity_gate as nig

    artifact = nig.assert_gate_satisfied(fx.write_identity_gate(tmp_path))
    assert artifact["passed"] is True
    assert artifact["n_checks"] == 4


def test_the_gate_requires_all_four_checks_not_only_the_identity():
    """Recording only the identity would be satisfied by a vacuous fixture."""
    import nroy_identity_gate as nig

    source = (ROOT / "nroy_identity_gate.py").read_text()
    body = source.split("def run_gate")[1]
    for check in (
        "check_not_vacuous", "check_identity",
        "check_deliberate_failures", "check_positive_p_is_live",
    ):
        assert check in body, f"run_gate does not run {check}"
    assert "all(check.passed for check in checks)" in body, (
        "the gate must pass only when every check passes"
    )


def test_the_gate_command_is_documented_and_explicit():
    parser = runner.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert "identity-gate" in choices, "the gate must be a first-class command"
    assert "RUNS THE ENGINE" in choices["identity-gate"].format_help()
    # The gate module names the reason it exists rather than leaving it implicit.
    doc = (ROOT / "nroy_identity_gate.py").read_text()
    assert "no-engine guard" in doc
    assert "cannot be skipped" in doc

    readiness = (
        ROOT / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15/README.md"
    ).read_text()
    assert "run_robust_nroy_freeze.py identity-gate" in readiness
    assert "compatibility" in readiness and "assertions only" in readiness
    assert "exact corresponding signed prerequisite" in readiness
    assert "--m0-confirmed-dir" in readiness
    assert "--m0-confirmation-dir" not in readiness


def test_prepared_work_and_identity_gate_cover_the_production_dependency_surface():
    import nroy_identity_gate as nig

    required = {
        "engine_v3.py", "config_utils.py", "config_reader.py",
        "transmission_corrected.py", "demography.py", "migration.py",
        "schedule_utils.py", "stage_structured_diagnosis.py",
        "aids_observation.py", "exogenous_diagnoses.py", "output_writer.py",
        "hm_target_register.py", "hm_variance_budget.py",
    }
    assert required <= set(runner.CODE_FILES)
    assert required <= set(nig.GATE_CODE_MODULES)
    assert "nroy_acceptance_inputs.py" in runner.CODE_FILES


def test_m1_cli_embeds_every_confirmed_m0_vector_at_exact_zero(tmp_path):
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    stage = harness.prepare(nc.STAGE_DISCOVERY)
    assert harness.m0_source is not None
    source_dir = harness.m0_source.dirs[nc.STAGE_CONFIRMATION]
    source_scores = ne.read_frame_exact(source_dir / "candidate_scores.csv")
    source_ids = set(source_scores.loc[
        source_scores["nominal_pass"].astype(bool), "design_id"
    ].astype(str))
    source_vectors = ne.read_frame_exact(source_dir / "design_vectors.csv").set_index("design_id")
    embedded = ne.read_frame_exact(stage / "anchors.csv").set_index("candidate_id")
    assert source_ids < set(embedded.index.astype(str))
    for candidate_id in source_ids:
        assert float(embedded.loc[candidate_id, fx.P_NAME]) == 0.0
        for name in fx.m0_space().names:
            assert float(embedded.loc[candidate_id, name]) == float(
                source_vectors.loc[candidate_id, name]
            )


def test_m1_discovery_requires_independent_strictly_positive_p_anchors(
    tmp_path, capsys,
):
    harness = Harness(tmp_path, model=nc.MODEL_M1)
    source_root = tmp_path / "m0_source_for_positive_p_test"
    source = Harness(source_root, model=nc.MODEL_M0)
    source.plan_path = harness.plan_path
    source.plan = harness.plan
    source.chain_to_confirmation()
    zero = ne.read_frame_exact(harness.anchors)
    zero[fx.P_NAME] = 0.0
    zero_path = tmp_path / "zero_only_m1.csv"
    zero.to_csv(zero_path, index=False)
    code = runner.main([
        "prepare", "--plan", str(harness.plan_path),
        "--out", str(tmp_path / "m1_zero_only"),
        "--model", nc.MODEL_M1, "--stage", nc.STAGE_DISCOVERY,
        "--anchors", str(zero_path),
        "--m1-anchor-evidence", str(fx.write_m1_anchor_evidence(
            tmp_path, plan_path=harness.plan_path, anchors_path=zero_path,
        )),
        "--identity-gate", str(fx.write_identity_gate(tmp_path)),
        "--m0-confirmed-dir", str(source.dirs[nc.STAGE_CONFIRMATION]),
    ])
    assert code == 1
    assert "strictly positive p" in capsys.readouterr().err


def test_m1_discovery_unions_independent_positive_p_and_embedded_m0_anchors(
    tmp_path, monkeypatch,
):
    """M1 evidence is additive; embedding M0 may never erase confirmed M1."""
    space = fx.m1_space()
    rows = []
    for index, vector in enumerate(fx.valid_interior_vectors(space, 3, seed=991)):
        vector[fx.P_NAME] = 0.1 + 0.1 * index
        rows.append({"candidate_id": f"M1P{index}", **vector})
    independent_path = tmp_path / "independent_m1.csv"
    pd.DataFrame(rows).to_csv(independent_path, index=False)

    harness = Harness(tmp_path, model=nc.MODEL_M1, anchors=independent_path)
    stage = harness.prepare(
        nc.STAGE_DISCOVERY, extra=("--anchors", str(independent_path)),
    )
    frozen = ne.read_frame_exact(stage / "anchors.csv").set_index("candidate_id")
    independent = runner._load_anchors(independent_path, space)
    for anchor in independent:
        assert anchor.anchor_id in frozen.index
        assert float(frozen.loc[anchor.anchor_id, fx.P_NAME]) > 0.0
        actual = {
            name: float(frozen.loc[anchor.anchor_id, name]) for name in space.names
        }
        assert nc.vector_hash(actual, names=space.names) == anchor.vector_hash

    source_dir = harness.m0_source.dirs[nc.STAGE_CONFIRMATION]
    source_scores = ne.read_frame_exact(source_dir / "candidate_scores.csv")
    embedded_ids = set(source_scores.loc[
        source_scores["nominal_pass"].astype(bool), "design_id"
    ].astype(str))
    assert embedded_ids <= set(frozen.index.astype(str))
    assert all(float(frozen.loc[candidate_id, fx.P_NAME]) == 0.0 for candidate_id in embedded_ids)

    manifest = json.loads((stage / "manifest.json").read_text())
    extra = manifest["extra"]
    assert extra["n_independently_confirmed_m1_anchors"] == 3
    assert extra["n_embedded_m0_identity_anchors"] == len(embedded_ids)
    assert int(extra["n_anchors"]) == len(frozen)

    sealed_evidence = stage / runner.M1_ANCHOR_EVIDENCE_FILENAME
    sealed_gate = stage / runner.nig.GATE_FILENAME
    sealed_independent = stage / runner.M1_INDEPENDENT_ANCHORS_FILENAME
    assert sealed_gate.is_file()
    assert sealed_independent.read_bytes() == independent_path.read_bytes()
    indexed = {
        row["path"] for row in json.loads((stage / "artifacts.json").read_text())["files"]
    }
    assert runner.nig.GATE_FILENAME in indexed
    assert runner.M1_ANCHOR_EVIDENCE_FILENAME in indexed
    assert runner.M1_INDEPENDENT_ANCHORS_FILENAME in indexed
    packaged = json.loads(sealed_evidence.read_text())
    assert packaged["anchors_path"] == runner.M1_INDEPENDENT_ANCHORS_FILENAME
    packaged_files = []
    for row in packaged["candidates"]:
        relative = Path(row["seed_result_artifact_path"])
        assert not relative.is_absolute()
        packaged_result = stage / relative
        assert packaged_result.is_file()
        assert ccs.sha256_file(packaged_result) == row["seed_result_artifact_sha256"]
        assert relative.as_posix() in indexed
        packaged_files.append(packaged_result)

    validations = []
    real_validator = runner._validate_m1_anchor_evidence

    def record_semantic_validation(*args, **kwargs):
        validations.append(Path(args[0]))
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(
        runner, "_validate_m1_anchor_evidence", record_semantic_validation
    )
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage),
        "--shard", "9999", "--of", "10000", "--evaluator", "surrogate",
    ]) == 0
    assert validations == [sealed_evidence]
    original_seed_result = packaged_files[0].read_bytes()
    packaged_files[0].write_bytes(original_seed_result + b"\nmutated\n")
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
    ]) == 1, "mutating packaged seed-level evidence must stop dispatch"
    packaged_files[0].write_bytes(original_seed_result)
    changed = json.loads(sealed_evidence.read_text())
    changed["passed"] = False
    sealed_evidence.write_text(json.dumps(changed))
    assert runner.main([
        "evaluate", "--plan", str(harness.plan_path), "--stage-dir", str(stage),
        "--shard", "0", "--of", "1", "--evaluator", "surrogate",
    ]) == 1, "mutating the sealed M1 confirmation evidence must stop dispatch"


def test_m1_anchor_confirmation_evidence_is_typed_and_reconciled(tmp_path):
    plan_path = fx.write_signed_plan(tmp_path)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    model = runner._model_spaces(plan)[nc.MODEL_M1]
    anchors_path = _write_anchors(tmp_path / "positive_m1.csv", model, prefix="M1P")
    anchors = runner._load_anchors(anchors_path, model)
    evidence_path = fx.write_m1_anchor_evidence(
        tmp_path, plan_path=plan_path, anchors_path=anchors_path,
    )
    acceptance = runner._validate_declared_acceptance_package(plan)
    validation_inputs = {
        "acceptance_identity": acceptance["acceptance_input_identity"],
        "observed": json.loads(Path(acceptance["observed_path"]).read_text()),
        "base_variance": json.loads(
            Path(acceptance["base_variance_path"]).read_text()
        ),
    }
    runner._validate_m1_anchor_evidence(
        evidence_path, anchors_path=anchors_path, anchors=anchors,
        plan=plan, model=model, **validation_inputs,
    )

    original = json.loads(evidence_path.read_text())
    mutations = {
        "generic_pass_only": {"passed": True},
        "nonfinal_status": {**original, "status": "INCOMPLETE"},
        "not_production_eligible": {**original, "production_eligible": False},
        "wrong_plan": {**original, "plan_sha256": "0" * 64},
        "wrong_runtime": {**original, "scientific_runtime": {}},
        "wrong_base_config": {**original, "base_config_sha256": "0" * 64},
        "wrong_seeds": {**original, "confirmation_seeds": list(plan.seeds.discovery)},
        "wrong_acceptance_basis": {
            **original,
            "acceptance_input_identity": {
                **original["acceptance_input_identity"],
                "observed_sha256": "0" * 64,
            },
        },
        "wrong_vector": {
            **original,
            "candidates": [
                {**row, "vector_hash": "f" * 64} if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "too_few_replicates": {
            **original,
            "candidates": [
                {**row, "n_replicates": plan.k_confirmation - 1}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "too_few_per_seed_hashes": {
            **original,
            "candidates": [
                {**row, "per_seed_joint_vector_hashes": row["per_seed_joint_vector_hashes"][:-1]}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "wrong_per_seed_joint_vector": {
            **original,
            "candidates": [
                {**row, "per_seed_joint_vector_hashes": ["f" * 64] * plan.k_confirmation}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "missing_seed_result_artifact_hash": {
            **original,
            "candidates": [
                {**row, "seed_result_artifact_sha256": None}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "missing_seed_result_artifact_path": {
            **original,
            "candidates": [
                {**row, "seed_result_artifact_path": None}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "absolute_seed_result_artifact_path": {
            **original,
            "candidates": [
                {**row, "seed_result_artifact_path": str(tmp_path / "absolute.json")}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "escaping_seed_result_artifact_path": {
            **original,
            "candidates": [
                {**row, "seed_result_artifact_path": "../outside.json"}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "wrong_seed_result_artifact_hash": {
            **original,
            "candidates": [
                {**row, "seed_result_artifact_sha256": "0" * 64}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
        "failed_primary_rule": {
            **original,
            "candidates": [
                {**row, "nominal_i_max": plan.cutoff + 0.01, "nominal_pass": False}
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        },
    }
    for label, payload in mutations.items():
        path = tmp_path / f"bad_{label}.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ContractError):
            runner._validate_m1_anchor_evidence(
                path, anchors_path=anchors_path, anchors=anchors,
                plan=plan, model=model, **validation_inputs,
            )

    first_row = original["candidates"][0]
    first_result = evidence_path.parent / first_row["seed_result_artifact_path"]
    original_seed_result = json.loads(first_result.read_text())
    first_target = plan.targets.frozen_order[0]
    seed_mutations = {
        "wrong_candidate": {
            **original_seed_result,
            "candidate_id": "not-the-anchor",
        },
        "wrong_acceptance_basis": {
            **original_seed_result,
            "acceptance_input_identity": {
                **original_seed_result["acceptance_input_identity"],
                "base_variance_sha256": "0" * 64,
            },
        },
        "wrong_target_order": {
            **original_seed_result,
            "target_order": list(reversed(original_seed_result["target_order"])),
        },
        "wrong_complete_vector": {
            **original_seed_result,
            "parameters": {
                **original_seed_result["parameters"],
                model.names[0]: float(original_seed_result["parameters"][model.names[0]])
                + 0.01,
            },
        },
        "duplicate_reserved_seed": {
            **original_seed_result,
            "seed_results": [
                original_seed_result["seed_results"][0],
                original_seed_result["seed_results"][0],
                *original_seed_result["seed_results"][2:],
            ],
        },
        "wrong_per_seed_vector": {
            **original_seed_result,
            "seed_results": [
                {**record, "vector_hash": "f" * 64} if index == 0 else record
                for index, record in enumerate(original_seed_result["seed_results"])
            ],
        },
        "missing_target_output": {
            **original_seed_result,
            "seed_results": [
                {
                    **record,
                    "outputs": {
                        key: value for key, value in record["outputs"].items()
                        if key != first_target
                    },
                }
                if index == 0 else record
                for index, record in enumerate(original_seed_result["seed_results"])
            ],
        },
        "nonfinite_target_output": {
            **original_seed_result,
            "seed_results": [
                {
                    **record,
                    "outputs": {**record["outputs"], first_target: float("nan")},
                }
                if index == 0 else record
                for index, record in enumerate(original_seed_result["seed_results"])
            ],
        },
        "string_target_output": {
            **original_seed_result,
            "seed_results": [
                {
                    **record,
                    "outputs": {**record["outputs"], first_target: "0.0"},
                }
                if index == 0 else record
                for index, record in enumerate(original_seed_result["seed_results"])
            ],
        },
        "aggregate_pass_cannot_override_outputs": {
            **original_seed_result,
            "seed_results": [
                {
                    **record,
                    "outputs": {**record["outputs"], first_target: 4.0},
                }
                for record in original_seed_result["seed_results"]
            ],
        },
    }
    for label, seed_payload in seed_mutations.items():
        bad_result = tmp_path / f"bad_seed_result_{label}.json"
        bad_result.write_text(json.dumps(seed_payload))
        bad_evidence = {
            **original,
            "candidates": [
                {
                    **row,
                    "seed_result_artifact_path": bad_result.name,
                    "seed_result_artifact_sha256": ccs.sha256_file(bad_result),
                }
                if index == 0 else row
                for index, row in enumerate(original["candidates"])
            ],
        }
        bad_evidence_path = tmp_path / f"bad_semantics_{label}.json"
        bad_evidence_path.write_text(json.dumps(bad_evidence))
        with pytest.raises(ContractError):
            runner._validate_m1_anchor_evidence(
                bad_evidence_path, anchors_path=anchors_path, anchors=anchors,
                plan=plan, model=model, **validation_inputs,
            )

    aggregate_override = {
        **original,
        "candidates": [
            {**row, "nominal_i_max": 0.5} if index == 0 else row
            for index, row in enumerate(original["candidates"])
        ],
    }
    aggregate_override_path = tmp_path / "bad_self_declared_aggregate.json"
    aggregate_override_path.write_text(json.dumps(aggregate_override))
    with pytest.raises(ContractError, match="does not match recomputed"):
        runner._validate_m1_anchor_evidence(
            aggregate_override_path, anchors_path=anchors_path, anchors=anchors,
            plan=plan, model=model, **validation_inputs,
        )

    first_result.write_text(first_result.read_text() + "\nmutated\n")
    with pytest.raises(ContractError, match="seed-result artifact SHA-256 does not match"):
        runner._validate_m1_anchor_evidence(
            evidence_path, anchors_path=anchors_path, anchors=anchors,
            plan=plan, model=model, **validation_inputs,
        )


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
    region_report = (regions / "package" / "REGION_REPORT.md").read_text()
    assert "| parameter | min | max |" in region_report
    assert "binding targets:" in region_report
    assert "baseline-state ranges: unavailable" in region_report

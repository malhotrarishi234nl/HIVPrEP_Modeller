"""Shared fixtures for the robust-NROY freeze verification suite.

Nothing here constructs a simulation engine or runs a trajectory.  The
"simulator" is a deterministic surrogate keyed by vector hash and seed, and the
synthetic geometries below are closed-form: a broad basin, a thin correlated
ridge, an isolated point, two disconnected basins and a curved corridor whose
straight bridge fails.  That is what lets the whole distributed contract be
verified without spending production compute.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import coupled_calibration_search as ccs  # noqa: E402
import nroy_contract as nc  # noqa: E402
import nroy_design as nd  # noqa: E402
import nroy_execution as ne  # noqa: E402
import nroy_identity_gate as nig  # noqa: E402

PRODUCTION_PLAN = ROOT / "configs" / "robust_nroy_freeze_production.json"
M0_CALIB_SPACE = ROOT / "configs" / "calib_space_v13_25_unordered_orientation_20260921.json"
SHARED_PRODUCTION_CONFIG = (
    "configs/"
    "V13.22_20260604_nearfit_rank001_basecase_onart_two_era_"
    "stochastic_m0_m1_20261015.json"
)

P_NAME = "aids_symptom_annual_probability"
P_CONFIG_PATH = (
    "transmission_model.disease.diagnosis.stage_structured_diagnosis."
    "aids_symptom_annual_probability"
)

#: Values used only to make the *verification suite* runnable.  They are test
#: fixtures, not signed production decisions: the shipped production plan
#: deliberately leaves every one of these unsigned.
TEST_SIGNATURES: dict[str, object] = {
    "models.m0.calib_space": "configs/calib_space_v13_25_unordered_orientation_20260921.json",
    "models.base_config": SHARED_PRODUCTION_CONFIG,
    "target_contract.register_path": "configs/hm_target_register_robust_nroy_production_20261015.json",
    "target_contract.register_sha256": ccs.sha256_file(
        ROOT / "configs" / "hm_target_register_robust_nroy_production_20261015.json"
    ),
    "target_contract.n_targets": 13,
    "target_contract.frozen_order": list(ccs.FROZEN_TARGET_ORDER),
    "p_parameter.name": P_NAME,
    "p_parameter.config_path": P_CONFIG_PATH,
    "p_parameter.min": 0.0,
    "p_parameter.max": 0.5,
    "p_parameter.bins": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
    "p_parameter.routine_hazard_scale_identity_value": 1.0,
    "p_parameter.symptom_start_year": 1985,
    "budget.max_trajectories": 100000,
    "budget.max_attempts": 100000,
    "budget.wall_time_cap_seconds": 3600,
    "representative_selection.rule": "maximin_geometry",
    "representative_selection.n_per_component": 2,
    "ambient.scales": [0.025, 0.05],
    "ambient.scale_semantics": "radius",
    "ambient.quota_per_scale": 4,
    "ambient.direction_generator": "gaussian_unit_sphere",
    "ambient.max_attempts_per_anchor": 2000,
    # One retained confirmed-difference direction x two scales x two signs.
    "archive_following.quota": 4,
    "archive_following.scales": [0.025, 0.05],
    "archive_following.neighbour_rule": "k_nearest_in_normalised_metric",
    "archive_following.neighbour_count": 3,
    "archive_following.max_directions": 1,
    "archive_following.source_pool": ["confirmed_differences", "cross_block_directions"],
    "cross_block.quota": 4,
    "cross_block.scales": [0.025, 0.05],
    "separation.min_anchor_neighbour": 1e-4,
    "separation.min_neighbour_neighbour": 1e-4,
    "separation.min_direction_classes": 2,
    "separation.max_abs_direction_cosine": 0.99,
    "promotion.variance_alpha": 0.05,
    "support.min_confirming_neighbours": 2,
    "graph.edge_proposal_rule": "complete_pairwise_distances",
    "graph.tie_breaking": "candidate_id_ascending",
    "graph.minimal_edge_algorithm": "kruskal_minimum_spanning_tree",
    "graph.bridge_fractions": [0.25, 0.5, 0.75],
    "graph.bridge_pass_rule": "all_declared_probes_must_confirm",
    "seeds.discovery": [910000, 910001, 910002, 910003, 910004],
    "seeds.confirmation": list(range(910100, 910120)),
    "seeds.proposal_rng": 910300,
}


def _assign(node: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def signed_plan_dict(**overrides) -> dict:
    """The production plan with the test signatures applied.

    Built *from* the shipped production plan so that a new required decision
    added to :data:`nroy_contract.REQUIRED_DECISIONS` without a matching test
    signature makes the suite fail loudly rather than silently skip it.
    """
    raw = json.loads(PRODUCTION_PLAN.read_text())
    for path, value in TEST_SIGNATURES.items():
        _assign(raw, path, value)
    _assign(raw, "models.m0.calib_space", str(M0_CALIB_SPACE.relative_to(ROOT)))
    raw["sign_off"] = {
        "signed_by": "verification-suite-fixture (NOT a production signature)",
        "signed_date": "2026-10-14",
        "spec_version": "ROBUST_NROY_FREEZE_SPEC_2026-10-14",
    }
    for path, value in overrides.items():
        _assign(raw, path, value)
    return raw


def write_signed_plan(
    tmp_path: Path,
    *,
    acceptance_observed: Path | Mapping[str, float] | None = None,
    acceptance_variance: Path | Mapping[str, float] | None = None,
    m1_anchors: Path | None = None,
    **overrides,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    # Production validation correctly refuses the shipped deterministic debug
    # config.  The verification fixture therefore makes an explicit stochastic
    # copy and binds all four non-decision prerequisites to the exact plan.
    base = json.loads((ROOT / SHARED_PRODUCTION_CONFIG).read_text())
    base.setdefault("run_control", {})["deterministic_mode"] = {
        "value": False,
        "notes": "verification-suite stochastic fixture",
    }
    base_path = tmp_path / "stochastic_base_config.json"
    base_path.write_text(json.dumps(base, indent=1))
    # Acceptance-package unit tests use a minimal fabricated workbook and
    # private two-series register. Both exact byte identities are bound so the
    # production-only invariant is exercised without external data or any
    # pretence that this fixture is scientific evidence.
    if "target_contract.register_path" not in overrides:
        fixture_workbook = tmp_path / "acceptance_inputs" / "fixture_source_workbook.xlsx"
        fixture_workbook.parent.mkdir(parents=True, exist_ok=True)
        fixture_series = (
            (
                "SHM",
                "shm_new_diagnoses_msm",
                "new_diagnoses_msm",
                [1996, 2000, 2004, 2010, 2015, 2019, 2024],
            ),
            (
                "AIDS",
                "rivm_aids_msm",
                "aids_prevalence_msm",
                [2000, 2005, 2010, 2015, 2019, 2022],
            ),
        )
        if not fixture_workbook.exists():
            with pd.ExcelWriter(fixture_workbook) as writer:
                for sheet, _series, _output, years in fixture_series:
                    pd.DataFrame([["fixture spacer"]]).to_excel(
                        writer, sheet_name=sheet, index=False, header=False
                    )
                    pd.DataFrame(
                        {"Year": years, "Value": range(1, len(years) + 1)}
                    ).to_excel(writer, sheet_name=sheet, index=False, startrow=1)
        fixture_register = {
            "version": "verification-fixture-only",
            "source_workbook": str(fixture_workbook),
            "source_workbook_sha256": ccs.sha256_file(fixture_workbook),
            "defaults": {
                "v_disc": {"method": "relative_to_target", "multiplier": 0.1},
                "v_ensemble": {"reason": "verification fixture"},
            },
            "targets": [
                {
                    "series_name": series,
                    "model_output": output,
                    "source_sheet": sheet,
                    "source_organisation": "verification fixture",
                    "year_column": "Year",
                    "estimate_column": "Value",
                    "year_coverage": [min(years), max(years)],
                    "epistemic_tier": 1,
                    "role": "calibration",
                    "z_derivation": "fabricated verification fixture",
                    "v_obs": {"method": "underivable", "reason": "fixture"},
                    "v_disc": {"method": "default"},
                }
                for sheet, series, output, years in fixture_series
            ],
        }
        fixture_register_path = tmp_path / "fixture_target_register.json"
        fixture_register_path.write_text(json.dumps(fixture_register, indent=1))
        overrides = {
            "target_contract.register_path": str(fixture_register_path),
            "target_contract.register_sha256": ccs.sha256_file(fixture_register_path),
            **overrides,
        }
    prereq = {
        "config_registry": str(tmp_path / "CONFIG_REGISTRY.md"),
        "acceptance_inputs": str(
            tmp_path / "acceptance_inputs" / "acceptance_input_provenance.json"
        ),
        "measured_pilot": str(tmp_path / "measured_pilot.json"),
        "priced_budget": str(tmp_path / "priced_budget.json"),
        "seed_disjointness_audit": str(tmp_path / "seed_disjointness_audit.json"),
        "identity_gate": str(tmp_path / nig.GATE_FILENAME),
        "m1_anchor_confirmation": str(tmp_path / "m1_anchor_confirmation.json"),
    }
    overrides = {
        "models.base_config": str(base_path),
        "production_prerequisites": prereq,
        **overrides,
    }
    raw = signed_plan_dict(**overrides)
    path = tmp_path / "signed_plan.json"
    path.write_text(json.dumps(raw, indent=1))
    plan_sha = ccs.sha256_file(path)
    _write_test_acceptance_package(
        tmp_path / "acceptance_inputs",
        plan_path=path,
        observed=acceptance_observed,
        variance=acceptance_variance,
    )
    if m1_anchors is None:
        model = m1_space()
        rows = []
        for index, vector in enumerate(valid_interior_vectors(model, 3, seed=9191)):
            vector[P_NAME] = 0.1 + 0.1 * index
            rows.append({"candidate_id": f"SIGNED_M1_{index}", **vector})
        m1_anchors = tmp_path / "signed_m1_anchors.csv"
        pd.DataFrame(rows).to_csv(m1_anchors, index=False)
    declared_base = Path(str(raw["models"]["base_config"]))
    if not declared_base.is_absolute():
        declared_base = ROOT / declared_base
    if declared_base.is_file():
        write_identity_gate(tmp_path)
        write_m1_anchor_evidence(
            tmp_path, plan_path=path, anchors_path=Path(m1_anchors)
        )
    else:
        # Some negative tests deliberately sign a missing base config and
        # assert that the production adapter refuses it.  Keep fixture setup
        # reachable without fabricating a certificate for a nonexistent file.
        (tmp_path / nig.GATE_FILENAME).write_text("{}")
        (tmp_path / "m1_anchor_confirmation.json").write_text("{}")
    (tmp_path / "CONFIG_REGISTRY.md").write_text(
        "# Verification fixture only\n\n"
        f"plan: {plan_sha}\n"
        f"base config: {ccs.sha256_file(base_path)}\n"
        f"M0 calibration space: {ccs.sha256_file(M0_CALIB_SPACE)}\n"
    )
    common = {"passed": True, "plan_sha256": plan_sha}
    (tmp_path / "measured_pilot.json").write_text(json.dumps({
        **common, "seconds_per_trajectory": 0.001,
    }))
    (tmp_path / "priced_budget.json").write_text(json.dumps({
        **common,
        "cap_scope": "per_stage_ledger",
        "cost_scope": "entire_workflow",
        "max_attempts": int(raw["budget"]["max_attempts"]),
        "max_trajectories": int(raw["budget"]["max_trajectories"]),
        "wall_time_cap_seconds": float(raw["budget"]["wall_time_cap_seconds"]),
    }))
    (tmp_path / "seed_disjointness_audit.json").write_text(json.dumps({
        **common,
        "discovery": list(raw["seeds"]["discovery"]),
        "confirmation": list(raw["seeds"]["confirmation"]),
    }))
    return path


def _write_test_acceptance_package(
    out: Path,
    *,
    plan_path: Path,
    observed: Path | Mapping[str, float] | None,
    variance: Path | Mapping[str, float] | None,
) -> None:
    """Write explicit fabricated review evidence for no-engine tests only."""
    import nroy_acceptance_inputs as nai  # noqa: PLC0415 - avoid fixture cycle
    import nroy_identity_gate as nig  # noqa: PLC0415 - runtime fingerprint
    import run_robust_nroy_freeze as runner  # noqa: PLC0415 - code fingerprint

    out.mkdir(parents=True, exist_ok=True)
    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    default_observed, default_variance = observed_and_variance(
        plan.targets.frozen_order
    )

    observed_path = out / nai.OBSERVED_FILENAME
    variance_path = out / nai.BASE_VARIANCE_FILENAME
    if isinstance(observed, Path):
        shutil.copyfile(observed, observed_path)
        observed_values = json.loads(observed_path.read_text())
    else:
        observed_values = dict(observed or default_observed)
        observed_path.write_text(json.dumps(observed_values))
    if isinstance(variance, Path):
        shutil.copyfile(variance, variance_path)
        variance_values = json.loads(variance_path.read_text())
    else:
        variance_values = dict(variance or default_variance)
        variance_path.write_text(json.dumps(variance_values))

    component_path = out / nai.COMPONENT_FILENAME
    fields = [
        "key", "z", "v_obs_variance", "v_obs_status", "v_code_variance",
        "v_disc_variance", "extra_variance", "b_variance",
    ]
    with component_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key in plan.targets.frozen_order:
            z = float(observed_values[key])
            b_value = float(variance_values[key])
            discrepancy = (plan.discrepancy * abs(z)) ** 2
            remainder = b_value - discrepancy
            if remainder < 0.0:
                raise AssertionError(
                    f"test fixture B_i cannot be below discrepancy variance for {key}"
                )
            writer.writerow({
                "key": key,
                "z": z,
                "v_obs_variance": remainder,
                "v_obs_status": "fabricated_test_fixture",
                "v_code_variance": 0.0,
                "v_disc_variance": discrepancy,
                "extra_variance": 0.0,
                "b_variance": b_value,
            })

    wave_plan = out / "fixture_wave_plan.json"
    wave_plan.write_text("{}\n")
    base_config = Path(str(plan.require("models.base_config")))
    if not base_config.is_absolute():
        base_config = ROOT / base_config
    m0_space = Path(str(plan.require("models.m0.calib_space")))
    if not m0_space.is_absolute():
        m0_space = ROOT / m0_space
    register = Path(plan.targets.register_path)
    if not register.is_absolute():
        register = ROOT / register
    register_payload = nc.strict_json_load(register, what="test target register")
    source_workbook = Path(str(register_payload["source_workbook"]))
    if not source_workbook.is_absolute():
        source_workbook = ROOT / source_workbook
    expected_workbook_sha256 = register_payload.get("source_workbook_sha256")
    base_config_sha256 = (
        ccs.sha256_file(base_config) if base_config.is_file() else "MISSING_TEST_INPUT"
    )

    provenance = {
        "schema": nai.SCHEMA,
        "status": "FINAL",
        "production_eligible": True,
        "approved": True,
        "approved_by": "verification-suite-fixture (NOT a production approval)",
        "approved_utc": "2026-10-15T00:00:00Z",
        "generated_utc": "2026-10-15T00:00:00Z",
        "command": "verification fixture generator; no engine",
        "plan_sha256": plan.plan_sha256,
        "target_order": list(plan.targets.frozen_order),
        "target_count": plan.targets.n,
        "acceptance_rule": "I_max <= 3 on B_i + s_i^2 (1 + 1/K)",
        "base_variance_formula": (
            "B_i = V_obs + V_code + V_disc + declared extra non-process terms"
        ),
        "process_variance": "excluded from B_i; added during scoring",
        "discrepancy": {
            "method": ccs.DISCREPANCY_METHOD,
            "fraction": plan.discrepancy,
            "added_exactly_once": True,
        },
        "source_bindings": {
            "plan": {"path": str(plan_path), "sha256": ccs.sha256_file(plan_path)},
            "base_config": {
                "path": str(base_config), "sha256": base_config_sha256,
            },
            "m0_calibration_space": {
                "path": str(m0_space), "sha256": ccs.sha256_file(m0_space),
            },
            "target_register": {
                "path": str(register), "sha256": ccs.sha256_file(register),
            },
            "source_workbook": {
                "path": str(source_workbook),
                "sha256": ccs.sha256_file(source_workbook),
                **(
                    {"expected_sha256": expected_workbook_sha256}
                    if expected_workbook_sha256 is not None else {}
                ),
            },
            "wave_plan": {"path": str(wave_plan), "sha256": ccs.sha256_file(wave_plan)},
        },
        "scientific_code_sha256": runner._code_sha256(),
        "scientific_runtime": nig.scientific_runtime_fingerprint(),
        "outputs": {
            "observed_values": {
                "path": observed_path.name, "sha256": ccs.sha256_file(observed_path),
            },
            "base_variance": {
                "path": variance_path.name, "sha256": ccs.sha256_file(variance_path),
            },
            "component_table": {
                "path": component_path.name, "sha256": ccs.sha256_file(component_path),
            },
        },
        "limitations": [
            "FABRICATED verification evidence only; never production evidence."
        ],
    }
    (out / nai.PROVENANCE_FILENAME).write_text(json.dumps(provenance, indent=1))


def signed_plan(tmp_path: Path, **overrides) -> nc.FrozenPlan:
    return nc.FrozenPlan.load(write_signed_plan(tmp_path, **overrides), root=ROOT)


def write_identity_gate(
    tmp_path: Path, *, passed: bool = True, base_config: str | Path | None = None,
    **overrides,
) -> Path:
    """A **fabricated** identity-gate artifact, for testing the gate *check*.

    It carries the real code fingerprint, so the staleness check is exercised
    honestly, but no engine was run to produce it.  That is deliberate: these
    tests verify that ``assert_gate_satisfied`` refuses a missing, failed,
    incomplete or stale artifact, which is logic that needs no trajectory.

    The gate itself -- the part that actually proves the identity -- is run by
    ``run_robust_nroy_freeze.py identity-gate`` and by
    ``tests/test_nroy_m0_m1_engine_identity.py``, both of which build real
    engines. A fabricated artifact is never evidence about the model.
    """
    import nroy_identity_gate as nig  # noqa: PLC0415 - test-only

    if base_config is None and (tmp_path / "signed_plan.json").exists():
        base_config = json.loads((tmp_path / "signed_plan.json").read_text())["models"]["base_config"]
    base_config = base_config or nig.DEFAULT_BASE_CONFIG
    resolved_base = Path(base_config)
    if not resolved_base.is_absolute():
        resolved_base = ROOT / resolved_base
    artifact = {
        "schema": nig.GATE_VERSION,
        "passed": bool(passed),
        "generated_utc": "2026-10-14T00:00:00Z",
        "base_config": str(base_config),
        "base_config_sha256": ccs.sha256_file(resolved_base),
        "end_year": nig.DEFAULT_END_YEAR,
        "seeds": list(nig.DEFAULT_SEEDS),
        "code_sha256": nig.code_fingerprint(),
        "environment": nig.scientific_runtime_fingerprint(),
        "checks": [
            {"check": name, "passed": bool(passed), "detail": "fabricated fixture",
             "evidence": {}}
            for name in (
                "not_vacuous", "identity_at_exact_p_zero",
                "deliberate_failures_do_fail", "positive_p_changes_the_trajectory",
            )
        ],
        "n_checks": 4,
        "n_passed": 4 if passed else 0,
        "fixture_warning": (
            "FABRICATED by the verification suite to test the gate CHECK. No engine "
            "was run. This is not evidence that the identity holds."
        ),
    }
    plan_path = tmp_path / "signed_plan.json"
    if plan_path.exists():
        import run_robust_nroy_freeze as runner  # noqa: PLC0415 - test-only

        plan = nc.FrozenPlan.load(plan_path, root=ROOT)
        m1 = runner._model_spaces(plan)[nc.MODEL_M1]
        artifact.update({
            "config_route": "ProductionEvaluator.config_for",
            "route_evidence": {
                "plan_sha256": plan.plan_sha256,
                "scientific_config_sha256": runner._scientific_config_sha256(plan, m1),
                "vectors": {"fabricated-test-vector": {"vector_hash": "fixture"}},
            },
        })
    artifact.update(overrides)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / nig.GATE_FILENAME
    path.write_text(json.dumps(artifact, indent=1))
    return path


def write_m1_anchor_evidence(
    tmp_path: Path,
    *,
    plan_path: Path,
    anchors_path: Path,
    **overrides,
) -> Path:
    """Typed positive-p confirmation evidence for M1 CLI contract tests.

    Like the identity-gate helper above, this is explicitly a verification
    fixture.  It exercises strict reconciliation; it is not scientific
    evidence and must never be copied into a production run.
    """
    import run_robust_nroy_freeze as runner  # noqa: PLC0415 - test-only

    plan = nc.FrozenPlan.load(plan_path, root=ROOT)
    model = runner._model_spaces(plan)[nc.MODEL_M1]
    anchors = runner._load_anchors(anchors_path, model)
    acceptance = runner._validate_declared_acceptance_package(plan)
    acceptance_identity = acceptance["acceptance_input_identity"]
    observed = json.loads(Path(acceptance["observed_path"]).read_text())
    base_variance = json.loads(Path(acceptance["base_variance_path"]).read_text())
    base_config = Path(str(plan.require("models.base_config")))
    if not base_config.is_absolute():
        base_config = ROOT / base_config
    seed_result_dir = tmp_path / "m1_seed_results"
    seed_result_dir.mkdir(parents=True, exist_ok=True)
    candidate_rows = []
    for index, anchor in enumerate(anchors):
        seed_result_path = seed_result_dir / f"candidate_{index:04d}.json"
        seed_results = [
            {
                "seed": seed,
                "candidate_id": anchor.anchor_id,
                "vector_hash": anchor.vector_hash,
                "outputs": {
                    name: float(observed[name]) for name in plan.targets.frozen_order
                },
            }
            for seed in plan.seeds.confirmation
        ]
        seed_result_path.write_text(json.dumps({
            "schema": runner.M1_SEED_RESULT_SCHEMA,
            "model": nc.MODEL_M1,
            "candidate_id": anchor.anchor_id,
            "vector_hash": anchor.vector_hash,
            "parameter_order": list(model.names),
            "parameters": {
                name: float(anchor.params[name]) for name in model.names
            },
            "target_order": list(plan.targets.frozen_order),
            "confirmation_seeds": list(plan.seeds.confirmation),
            "n_replicates": plan.k_confirmation,
            "acceptance_input_identity": acceptance_identity,
            "seed_results": seed_results,
            "fixture_warning": (
                "FABRICATED no-engine outputs equal to the observed targets; "
                "not scientific confirmation evidence"
            ),
        }, indent=1))
        seed_frame = pd.DataFrame([
            {
                "design_id": anchor.anchor_id,
                "seed": record["seed"],
                "vector_hash": anchor.vector_hash,
                **{
                    f"target:{name}": record["outputs"][name]
                    for name in plan.targets.frozen_order
                },
            }
            for record in seed_results
        ])
        recomputed = ne.recompute_candidate_scores(
            seed_frame,
            targets=plan.targets,
            observed=observed,
            base_variance=base_variance,
            cutoff=plan.cutoff,
        ).iloc[0]
        candidate_rows.append({
            "candidate_id": anchor.anchor_id,
            "vector_hash": anchor.vector_hash,
            "p_value": float(anchor.params[model.p_name]),
            "n_replicates": plan.k_confirmation,
            "nominal_i_max": float(recomputed["i_max"]),
            "nominal_pass": bool(recomputed["nominal_pass"]),
            "per_seed_joint_vector_hashes": [
                anchor.vector_hash for _seed in plan.seeds.confirmation
            ],
            "seed_result_artifact_path": str(seed_result_path.relative_to(tmp_path)),
            "seed_result_artifact_sha256": ccs.sha256_file(seed_result_path),
        })
    payload = {
        "schema": runner.M1_ANCHOR_EVIDENCE_SCHEMA,
        "status": "FINAL",
        "production_eligible": True,
        "passed": True,
        "generated_utc": "2026-10-14T00:00:00Z",
        "command": "verification-suite fabricated M1 confirmation fixture",
        "model": nc.MODEL_M1,
        "plan_sha256": plan.plan_sha256,
        "anchors_path": str(anchors_path),
        "anchors_sha256": ccs.sha256_file(anchors_path),
        "scientific_code_sha256": runner._code_sha256(),
        "scientific_config_sha256": runner._scientific_config_sha256(plan, model),
        "scientific_runtime": nig.scientific_runtime_fingerprint(),
        "base_config_sha256": ccs.sha256_file(base_config),
        "target_register_sha256": plan.targets.register_sha256,
        "target_order": list(plan.targets.frozen_order),
        "acceptance_input_identity": acceptance_identity,
        "p_parameter": str(plan.require("p_parameter.name")),
        "confirmation_seeds": list(plan.seeds.confirmation),
        "candidates": candidate_rows,
        "fixture_warning": (
            "FABRICATED by the verification suite to test evidence validation; "
            "not production confirmation evidence"
        ),
    }
    payload.update(overrides)
    path = tmp_path / "m1_anchor_confirmation.json"
    path.write_text(json.dumps(payload, indent=1))
    return path


# ==========================================================================
# Model spaces
# ==========================================================================

def m0_space() -> nc.ModelSpace:
    return nc.load_model_space(json.loads(M0_CALIB_SPACE.read_text()), model=nc.MODEL_M0)


def m1_space(p_min: float = 0.0, p_max: float = 0.5) -> nc.ModelSpace:
    raw = nc.build_m1_calib_space(
        json.loads(M0_CALIB_SPACE.read_text()),
        p_name=P_NAME, p_config_path=P_CONFIG_PATH, p_min=p_min, p_max=p_max,
    )
    return nc.load_model_space(
        raw, model=nc.MODEL_M1, p_name=P_NAME, p_min=p_min, p_max=p_max
    )


def interior_vector(space: nc.ModelSpace, fraction: float = 0.5) -> dict[str, float]:
    """A vector at a fixed fraction of every coordinate's range."""
    u = np.full(space.n, float(fraction))
    return space.as_dict(space.denormalise(u))


def valid_interior_vectors(
    space: nc.ModelSpace, count: int, *, seed: int = 4242
) -> list[dict[str, float]]:
    """``count`` constraint-satisfying interior vectors, found by redrawing.

    Redraw, never repair: a vector that violates a declared constraint is
    discarded, which is the same discipline the design kernels use.
    """
    rng = np.random.default_rng(seed)
    out: list[dict[str, float]] = []
    attempts = 0
    while len(out) < count and attempts < 20000:
        attempts += 1
        u = rng.uniform(0.3, 0.7, size=space.n)
        params = space.as_dict(space.denormalise(u))
        if not ccs.invalid_reasons(space.space, [params[n] for n in space.names]):
            out.append(params)
    if len(out) < count:
        raise AssertionError(f"could only build {len(out)}/{count} valid vectors")
    return out


# ==========================================================================
# Synthetic geometries (spec sec. 9 "Design and scoring")
# ==========================================================================

class SyntheticField:
    """A closed-form implausibility field over normalised space.

    ``i_max(u)`` is deterministic in the parameter vector; replicate noise is
    added per seed so that variances, promotion and the variance bound all have
    something real to act on.  Because the field is known analytically, a test
    can assert the *correct* local-support label rather than merely a
    self-consistent one.
    """

    def __init__(self, kind: str, centres: Sequence[np.ndarray], *, noise: float = 0.02):
        self.kind = kind
        self.centres = [np.asarray(c, dtype=float) for c in centres]
        self.noise = float(noise)

    def i_max(self, u: np.ndarray) -> float:
        u = np.asarray(u, dtype=float)
        if self.kind == "broad_basin":
            return 1.0 + 40.0 * float(np.linalg.norm(u - self.centres[0]))
        if self.kind == "thin_ridge":
            centre = self.centres[0]
            delta = u - centre
            # Cheap in a single direction, very expensive across it: a ridge.
            along = float(delta @ self._ridge_direction(u.size))
            across = float(np.linalg.norm(delta - along * self._ridge_direction(u.size)))
            return 1.0 + 2.0 * abs(along) + 400.0 * across
        if self.kind == "isolated_point":
            distance = float(np.linalg.norm(u - self.centres[0]))
            return 1.0 if distance == 0.0 else 1.0 + 10000.0 * distance
        if self.kind == "two_basins":
            return 1.0 + 40.0 * min(
                float(np.linalg.norm(u - centre)) for centre in self.centres
            )
        if self.kind == "curved_corridor":
            # Plausible on a curved arc between two centres; the straight chord
            # between them leaves the corridor, so a straight bridge fails.
            a, b = self.centres[0], self.centres[1]
            mid = 0.5 * (a + b)
            bulge = np.zeros_like(a)
            bulge[0] = 0.25
            arc = mid + bulge
            return 1.0 + 25.0 * min(
                float(np.linalg.norm(u - point)) for point in (a, arc, b)
            )
        raise AssertionError(f"unknown synthetic field {self.kind!r}")

    @staticmethod
    def _ridge_direction(n: int) -> np.ndarray:
        direction = np.zeros(n)
        direction[: min(4, n)] = 1.0
        return direction / np.linalg.norm(direction)

    def values(self, u: np.ndarray, seed: int, n_targets: int) -> tuple[float, ...]:
        """Target values whose recomputed ``I_max`` reproduces the field.

        One target carries the signal; the rest sit on the observed value.
        With ``observed = 0`` and ``base_variance = 1``, the recomputed
        implausibility of the signal target is the field value, up to the
        declared replicate noise.
        """
        rng = np.random.default_rng([int(seed), abs(hash(tuple(np.round(u, 12)))) % (2 ** 31)])
        signal = self.i_max(u) + self.noise * rng.standard_normal()
        return tuple([signal] + [self.noise * rng.standard_normal()
                                 for _ in range(n_targets - 1)])


def observed_and_variance(targets: Sequence[str]) -> tuple[dict, dict]:
    """``observed = 0``, ``B_i = 1`` so recomputed ``I_max`` equals the field."""
    return ({name: 0.0 for name in targets}, {name: 1.0 for name in targets})


# ==========================================================================
# Ledger helpers
# ==========================================================================

def work_units(
    proposals: Sequence[nd.Proposal],
    seeds: Sequence[int],
    *,
    stage: str,
    model: str,
    targets: Sequence[str],
    code_sha256: str = "code",
    config_sha256: str = "config",
    parent_manifest_sha256: str = "parent",
) -> tuple[ne.WorkUnit, ...]:
    return tuple(
        ne.WorkUnit(
            stage=stage, model=model, design_id=p.design_id, seed=int(seed),
            vector_hash=p.vector_hash, kernel=p.kernel, parent_ids=p.parent_ids,
            params=p.params, parent_manifest_sha256=parent_manifest_sha256,
            code_sha256=code_sha256, config_sha256=config_sha256,
            target_schema=tuple(targets),
        )
        for p in proposals for seed in seeds
    )


def evaluate_units(
    units: Sequence[ne.WorkUnit],
    field: SyntheticField,
    space: nc.ModelSpace,
    targets: Sequence[str],
    *,
    vectors: Mapping[str, Mapping[str, float]],
    ledger: ne.Ledger | None = None,
    fail: Sequence[tuple[str, int]] = (),
) -> list[ne.SeedResult]:
    """Evaluate work units against the synthetic field.

    ``fail`` names ``(design_id, seed)`` pairs that should be recorded as
    execution failures rather than results, so a test can prove that a failure
    is never read as a scientific finding.
    """
    failures = {(str(d), int(s)) for d, s in fail}
    out: list[ne.SeedResult] = []
    for unit in units:
        # Ask the ledger for the next id, so a retry after a lost or failed
        # attempt gets a fresh attempt_id exactly as a real worker would.
        attempt_id = (
            ledger.next_attempt_id(unit.design_id, int(unit.seed))
            if ledger is not None
            else f"{unit.design_id}#s{unit.seed}#a001"
        )
        if (unit.design_id, int(unit.seed)) in failures:
            if ledger is not None:
                ledger.record_attempt(ne.AttemptRecord(
                    attempt_id=attempt_id, design_id=unit.design_id, seed=int(unit.seed),
                    status=ne.ATTEMPT_FAILED, vector_hash=unit.vector_hash,
                    failure_reason="injected failure", failure_class="InjectedFailure",
                ))
            continue
        params = dict(vectors[unit.design_id])
        u = space.normalise(params)
        values = field.values(u, int(unit.seed), len(targets))
        result = ne.SeedResult(
            design_id=unit.design_id, seed=int(unit.seed), stage=unit.stage,
            model=unit.model, vector_hash=unit.vector_hash, target_order=tuple(targets),
            values=values, attempt_id=attempt_id,
            code_sha256=unit.code_sha256, config_sha256=unit.config_sha256,
            parent_manifest_sha256=unit.parent_manifest_sha256,
        )
        if ledger is not None:
            ledger.record_attempt(ne.AttemptRecord(
                attempt_id=attempt_id, design_id=unit.design_id, seed=int(unit.seed),
                status=ne.ATTEMPT_SUCCESS, vector_hash=unit.vector_hash,
                result_hash=result.result_hash,
            ))
        out.append(result)
    return out

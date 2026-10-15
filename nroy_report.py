#!/usr/bin/env python3
"""Artifact package for the robust-NROY freeze stage (spec sec. 8).

Raw data stay tidy and machine-readable; the human-readable summaries are
*derived* from those consolidated tables and never from a parallel code path.

That derivation is the testable part of the contract.  :func:`summary_facts`
computes every headline number from the tables alone and returns them as a
flat mapping; :func:`render_executive_summary` only formats what it is given.
A test can therefore recompute each published number from the CSVs and compare,
which is what "every executive-summary number is recomputed from consolidated
tables in a test" (spec sec. 9) actually requires.

No result is reported only in logs, binary objects or hundreds of
per-candidate files, and every headline links back to candidate/region ids.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

import nroy_contract as nc
import nroy_execution as ne
from coupled_calibration_search import ContractError

#: Row unit and purpose of every table the package writes.  Doubles as the
#: data dictionary's top level and as the TABLE_INDEX.md source, so the two
#: can never disagree.
TABLE_SPEC: dict[str, dict[str, str]] = {
    "design_proposals": {
        "row_unit": "one proposed probe vector",
        "description": (
            "Every probe the design generated, accepted or rejected, with its kernel, "
            "scale, direction class, parent lineage and rejection reasons."
        ),
        "key_columns": "design_id, kernel_id, scale, direction_class, parent_ids, accepted",
    },
    "seed_results": {
        "row_unit": "one work unit at one seed",
        "description": (
            "Seed-level simulator outputs per target. The only source for every "
            "recomputed candidate statistic."
        ),
        "key_columns": "design_id, seed, vector_hash, target:<name>",
    },
    "candidate_scores": {
        "row_unit": "one candidate (design_id)",
        "description": (
            "Means, sample variances, per-target implausibility, I_max, binding target and "
            "promotion decision, all recomputed from seed_results."
        ),
        "key_columns": "design_id, i_max, binding_target, nominal_pass, promoted",
    },
    "local_support": {
        "row_unit": "one confirmed anchor",
        "description": (
            "Local-support label per anchor with its underlying counts, direction classes, "
            "feasible-direction coverage and eligibility for representative selection."
        ),
        "key_columns": "candidate_id, local_support_label, eligible_for_representation",
    },
    "candidate_edges": {
        "row_unit": "one proposed graph edge",
        "description": (
            "Provisional nearest-neighbour edges proposed by normalised Euclidean distance, "
            "flagged for membership of the minimal audited edge set."
        ),
        "key_columns": "edge_id, distance_normalised, in_minimal_edge_set",
    },
    "edge_verdicts": {
        "row_unit": "one evaluated graph edge",
        "description": (
            "Sampled-path verdict per edge with bridge-probe counts. 'unresolved' never "
            "means disconnected."
        ),
        "key_columns": "edge_id, edge_label, evidence_state, n_bridge_probes_confirmed",
    },
    "components": {
        "row_unit": "one sampled-path-supported component",
        "description": (
            "Groups of points joined by sampled paths under the declared design, with the "
            "unresolved edges on their boundary."
        ),
        "key_columns": "component_id, members, n_unresolved_boundary_edges",
    },
    "representatives": {
        "row_unit": "one selected representative (or one component with none)",
        "description": (
            "Representatives chosen per component on parameter/baseline geometry only. "
            "Policy outcomes are not available to selection."
        ),
        "key_columns": "component_id, candidate_id, selection_rank, selection_rule",
    },
    "kernel_audit": {
        "row_unit": "one kernel family",
        "description": (
            "Proposed, rejected, dispatched, completed, promoted and confirmed counts per "
            "kernel, with the declared quota. Count conservation must hold."
        ),
        "key_columns": "kernel_id, quota, n_proposed, n_dispatched, n_confirmed",
    },
    "ledger_units": {
        "row_unit": "one frozen work unit",
        "description": "The immutable work list written before any dispatch.",
        "key_columns": "stage, model, design_id, seed, vector_hash, result_key",
    },
    "ledger_attempts": {
        "row_unit": "one execution attempt",
        "description": (
            "Every attempt including failures and lost work. Failed attempts are audit "
            "history, never scientific results."
        ),
        "key_columns": "attempt_id, design_id, seed, status, failure_class",
    },
    "design_evidence": {
        "row_unit": "one frozen design id",
        "description": (
            "The resolved evidence state of every frozen design: confirmed support, no "
            "support detected, unresolved evidence, execution failure or work not run. "
            "The last two are execution bookkeeping and are never evidence about the model."
        ),
        "key_columns": "design_id, evidence_state, n_seeds_succeeded, nominal_pass",
    },
    "graph_nodes": {
        "row_unit": "one graph node (one assessed confirmed point)",
        "description": (
            "The declared node set the candidate-edge graph was built over, with each "
            "node's local-support label, eligibility and normalised coordinates. The "
            "'u:' columns are the only geometry representative selection sees."
        ),
        "key_columns": "candidate_id, local_support_label, eligible_for_representation",
    },
    "p_coverage": {
        "row_unit": "one declared p bin",
        "description": (
            "Descriptive coverage of the M1 p profile. A covered bin is not evidence that "
            "every value in it is plausible."
        ),
        "key_columns": "bin_lower, bin_upper, n_confirmed, n_evaluated",
    },
}


def _fraction(numerator: int, denominator: int) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


@dataclass(frozen=True)
class SummaryFacts:
    """Every headline number, derived only from the consolidated tables."""

    values: dict

    def __getitem__(self, key: str):
        return self.values[key]

    def as_dict(self) -> dict:
        return dict(self.values)


def summary_facts(
    tables: Mapping[str, pd.DataFrame],
    *,
    status: str,
    model: str,
    manifest: Mapping | None = None,
) -> SummaryFacts:
    """Recompute every published number from the tidy tables.

    Deliberately total: if a table is absent, its counts are reported as
    ``None`` and the corresponding evidence state is ``work_not_run`` rather
    than silently zero.  "Nothing ran" and "nothing was found" are different
    statements and the package must never conflate them.
    """
    if status not in nc.ALL_STATUSES:
        raise ContractError(f"unknown status {status!r}")

    facts: dict = {
        "status": status,
        "model": model,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stage_parentable": status in nc.PARENTABLE_STATUSES,
    }

    support = tables.get("local_support")
    if support is None or support.empty:
        facts.update({
            "n_anchors": None,
            "evidence_state": nc.EVIDENCE_NOT_RUN,
            "assessment_ran": False,
            "n_broadly_supported": None,
            "n_ridge_supported": None,
            "n_no_local_support_detected": None,
            "n_unresolved": None,
            "n_eligible": None,
            "eligible_candidate_ids": [],
            "no_support_candidate_ids": [],
            "unresolved_candidate_ids": [],
        })
    else:
        labels = support["local_support_label"].astype(str)
        facts["n_anchors"] = int(len(support))
        # The evidence state describes what the assessment FOUND, not merely
        # that it ran. Publishing "confirmed_support" for a run whose anchors
        # are all negative or unresolved would be an overclaim, and it is the
        # exact conflation sec. 9 forbids.
        if bool((labels.isin(sorted(nc.ELIGIBLE_LABELS))).any()):
            facts["evidence_state"] = nc.EVIDENCE_CONFIRMED
        elif bool((labels == nc.LABEL_UNRESOLVED).any()):
            facts["evidence_state"] = nc.EVIDENCE_UNRESOLVED
        else:
            facts["evidence_state"] = nc.EVIDENCE_NO_SUPPORT
        facts["assessment_ran"] = True
        for label in nc.LOCAL_SUPPORT_LABELS:
            facts[f"n_{label}"] = int((labels == label).sum())
        facts["n_eligible"] = int(support["eligible_for_representation"].astype(bool).sum())
        ids = support["candidate_id"].astype(str)
        facts["eligible_candidate_ids"] = sorted(
            ids[support["eligible_for_representation"].astype(bool)].tolist()
        )
        facts["no_support_candidate_ids"] = sorted(
            ids[labels == nc.LABEL_NO_LOCAL_SUPPORT].tolist()
        )
        facts["unresolved_candidate_ids"] = sorted(ids[labels == nc.LABEL_UNRESOLVED].tolist())

    scores = tables.get("candidate_scores")
    if scores is None or scores.empty:
        facts.update({
            "n_candidates_scored": None, "n_nominal_pass": None, "n_promoted": None,
            "n_variance_unstable": None,
        })
    else:
        facts["n_candidates_scored"] = int(len(scores))
        facts["n_nominal_pass"] = (
            int(scores["nominal_pass"].astype(bool).sum()) if "nominal_pass" in scores else None
        )
        facts["n_promoted"] = (
            int(scores["promoted"].astype(bool).sum()) if "promoted" in scores else None
        )
        facts["n_variance_unstable"] = (
            int(scores["variance_unstable"].astype(bool).sum())
            if "variance_unstable" in scores else None
        )

    edges = tables.get("edge_verdicts")
    if edges is None or edges.empty:
        facts.update({
            "n_edges_evaluated": None,
            "n_edges_sampled_path_supported": None,
            "n_edges_unresolved": None,
        })
    else:
        labels = edges["edge_label"].astype(str)
        facts["n_edges_evaluated"] = int(len(edges))
        facts["n_edges_sampled_path_supported"] = int(
            (labels == nc.EDGE_SAMPLED_PATH_SUPPORTED).sum()
        )
        facts["n_edges_unresolved"] = int((labels == nc.EDGE_UNRESOLVED).sum())

    comps = tables.get("components")
    if comps is None or comps.empty:
        # None, not 0: no component table means the connectivity stage did not
        # run. Printing "0 components" would read as "we looked and found no
        # supported region", which is a different claim entirely.
        facts.update({
            "n_components": None,
            "component_ids": [],
            "n_components_with_unresolved_boundary": None,
        })
    else:
        facts["n_components"] = int(len(comps))
        facts["component_ids"] = sorted(comps["component_id"].astype(str).tolist())
        facts["n_components_with_unresolved_boundary"] = int(
            (comps["n_unresolved_boundary_edges"].astype(int) > 0).sum()
        )

    reps = tables.get("representatives")
    if reps is None or reps.empty:
        # None, not 0, for the same reason as components above.
        facts.update({
            "n_representatives": None,
            "n_components_without_representative": None,
        })
    else:
        has_id = reps["candidate_id"].notna()
        facts["n_representatives"] = int(has_id.sum())
        facts["n_components_without_representative"] = int((~has_id).sum())

    attempts = tables.get("ledger_attempts")
    if attempts is None or attempts.empty:
        facts.update({
            "n_attempts": None, "n_successful_attempts": None,
            "n_failed_attempts": None, "n_lost_attempts": None,
        })
    else:
        status_series = attempts["status"].astype(str)
        facts["n_attempts"] = int(len(attempts))
        facts["n_successful_attempts"] = int((status_series == ne.ATTEMPT_SUCCESS).sum())
        facts["n_failed_attempts"] = int((status_series == ne.ATTEMPT_FAILED).sum())
        facts["n_lost_attempts"] = int((status_series == ne.ATTEMPT_LOST).sum())

    units = tables.get("ledger_units")
    if units is not None and not units.empty:
        facts["n_work_units"] = int(len(units))
        if attempts is not None and not attempts.empty:
            done = attempts[attempts["status"].astype(str) == ne.ATTEMPT_SUCCESS]
            completed = set(zip(done["design_id"].astype(str), done["seed"].astype(int)))
            keys = set(zip(units["design_id"].astype(str), units["seed"].astype(int)))
            facts["n_units_complete"] = len(keys & completed)
            facts["n_units_incomplete"] = len(keys - completed)
            facts["shard_completeness"] = _fraction(len(keys & completed), len(keys))

    coverage = tables.get("p_coverage")
    if model == nc.MODEL_M1:
        if coverage is None or coverage.empty:
            facts["p_coverage_state"] = nc.EVIDENCE_NOT_RUN
            facts["n_p_bins_covered"] = None
            facts["n_p_bins"] = None
        else:
            facts["n_p_bins"] = int(len(coverage))
            facts["n_p_bins_covered"] = int((coverage["n_confirmed"].astype(int) > 0).sum())
            facts["p_coverage_state"] = (
                nc.EVIDENCE_CONFIRMED
                if facts["n_p_bins_covered"] == facts["n_p_bins"]
                else nc.EVIDENCE_UNRESOLVED
            )
    if manifest:
        facts["ledger_frozen_sha256"] = manifest.get("ledger_frozen_sha256")
        facts["parent_manifest_sha256"] = manifest.get("parent_manifest_sha256")
    return SummaryFacts(values=facts)


def _blockers(facts: Mapping, *, unsigned: Sequence[str] = ()) -> list[str]:
    out: list[str] = []
    if unsigned:
        out.append(
            f"{len(unsigned)} required numerical decision(s) are unsigned, so no production "
            "simulation may start: " + ", ".join(sorted(unsigned)[:8])
            + (" ..." if len(unsigned) > 8 else "")
        )
    if facts.get("status") == nc.STATUS_PARTIAL:
        out.append(
            f"the stage is PARTIAL ({facts.get('n_units_incomplete')} incomplete work units) "
            "and cannot become a parent for the next stage"
        )
    if facts.get("evidence_state") == nc.EVIDENCE_NOT_RUN:
        out.append("no local assessment has been run, so no point is eligible for selection")
    if facts.get("n_failed_attempts"):
        out.append(
            f"{facts['n_failed_attempts']} execution failure(s) are recorded; these are not "
            "evidence about the model"
        )
    if facts.get("n_unresolved"):
        out.append(
            f"{facts['n_unresolved']} anchor(s) have unresolved local evidence and remain "
            "reported but ineligible"
        )
    if facts.get("p_coverage_state") == nc.EVIDENCE_UNRESOLVED:
        out.append(
            f"the M1 p profile covers only {facts.get('n_p_bins_covered')} of "
            f"{facts.get('n_p_bins')} declared bins"
        )
    return out


def render_executive_summary(
    facts: SummaryFacts,
    *,
    question: str,
    unsigned: Sequence[str] = (),
    next_decision: str = "",
) -> str:
    """Format the executive summary.  Performs no computation of its own."""
    f = facts.as_dict()
    blockers = _blockers(f, unsigned=unsigned)

    def count(key: str) -> str:
        value = f.get(key)
        return "not run" if value is None else str(value)

    lines = [
        "# Robust NROY freeze — executive summary",
        "",
        f"**Model:** {f['model']}  |  **Run status:** `{f['status']}`  "
        f"|  **Usable as a parent stage:** {'yes' if f['stage_parentable'] else 'no'}",
        f"**Generated:** {f['generated_utc']}",
        "",
        "## Question",
        "",
        question,
        "",
        "## Run status",
        "",
        f"- work units: {count('n_work_units')} "
        f"(complete {count('n_units_complete')}, incomplete {count('n_units_incomplete')})",
        f"- attempts: {count('n_attempts')} "
        f"(failed {count('n_failed_attempts')}, lost {count('n_lost_attempts')})",
        f"- candidates scored: {count('n_candidates_scored')}",
        "",
        "## Supported regions",
        "",
        f"- sampled-path-supported components: {count('n_components')}"
        + (f" ({', '.join(f['component_ids'])})" if f.get("component_ids") else ""),
        f"- representatives selected: {count('n_representatives')}",
        f"- components receiving no representative: "
        f"{count('n_components_without_representative')}",
        "",
        "Components are groups joined by *sampled paths* under the declared finite design. "
        "They are not topological components of the NROY set, and a supported edge is "
        "discrete evidence at the declared bridge fractions, not continuous connectivity.",
        "",
        "## Local support",
        "",
        f"- broadly supported: {count('n_broadly_supported')}",
        f"- ridge supported: {count('n_ridge_supported')}",
        f"- no local support detected: {count('n_no_local_support_detected')}",
        f"- unresolved evidence: {count('n_unresolved')}",
        f"- eligible for representative selection: {count('n_eligible')}",
        "",
        "\"No local support detected\" is a statement about the declared probes, scales and "
        "budget. It is not a claim that the point is isolated.",
        "",
    ]
    for label, key in (
        ("Eligible", "eligible_candidate_ids"),
        ("No support detected", "no_support_candidate_ids"),
        ("Unresolved", "unresolved_candidate_ids"),
    ):
        ids = f.get(key) or []
        if ids:
            lines += [f"- **{label}:** {', '.join(ids)}", ""]

    if f["model"] == nc.MODEL_M1:
        lines += [
            "## M0/M1 and the p profile",
            "",
            f"- declared p bins: {count('n_p_bins')}; bins with a confirmed point: "
            f"{count('n_p_bins_covered')}",
            f"- coverage state: `{f.get('p_coverage_state', nc.EVIDENCE_NOT_RUN)}`",
            "",
            "Every confirmed M0 vector enters M1 at exact `p = 0` and is rechecked through the "
            "M1 runner; that is a required identity control, not a warm start. A covered bin is "
            "descriptive coverage, not evidence that every value in it is plausible. If no "
            "additional M1 region is found, that is \"none found under the declared search and "
            "budget\", not structural impossibility.",
            "",
        ]

    lines += ["## Blockers", ""]
    lines += [f"- {b}" for b in blockers] or ["- none recorded"]
    lines += [
        "",
        "## Next decision",
        "",
        next_decision or (
            "Sign the outstanding numerical decisions, then re-run the verification suite "
            "before any production simulation."
        ),
        "",
        "## Evidence vocabulary",
        "",
        "| state | meaning |",
        "|---|---|",
        "| `confirmed_support` | support demonstrated under the declared design |",
        "| `no_support_detected` | the complete declared design ran and found none |",
        "| `unresolved_evidence` | coverage/precision insufficient to classify |",
        "| `execution_failure` | the simulation or worker failed; not a scientific result |",
        "| `work_not_run` | never dispatched; not a scientific result |",
        "",
    ]
    return "\n".join(lines)


def render_region_report(
    components: pd.DataFrame,
    local_support: pd.DataFrame,
    *,
    parameter_frame: pd.DataFrame | None = None,
    parameter_names: Sequence[str] = (),
    baseline_frame: pd.DataFrame | None = None,
    binding_targets: Mapping[str, str] | None = None,
) -> str:
    """One concise section per sampled-path-supported component (spec sec. 8)."""
    lines = [
        "# Region report",
        "",
        "One section per sampled-path-supported component. Parameter ranges describe the "
        "*sampled members* of a component; they are not a posterior and carry no probability "
        "weight.",
        "",
    ]
    if components.empty:
        lines += ["No components were produced (`work_not_run`).", ""]
        return "\n".join(lines)

    eligible_of = {}
    if not local_support.empty:
        eligible_of = dict(zip(
            local_support["candidate_id"].astype(str),
            local_support["local_support_label"].astype(str),
        ))

    for _, row in components.iterrows():
        members = [m for m in str(row["members"]).split("|") if m]
        lines += [
            f"## Component {row['component_id']}",
            "",
            f"- members ({len(members)}): {', '.join(members)}",
            f"- supporting edges: {row.get('n_supporting_edges', 0)}",
            f"- unresolved boundary edges: {row.get('n_unresolved_boundary_edges', 0)}",
        ]
        if eligible_of:
            labels = sorted({eligible_of.get(m, "unknown") for m in members})
            lines.append(f"- local-support labels present: {', '.join(labels)}")
        if binding_targets:
            binding = sorted({binding_targets.get(m, "") for m in members} - {""})
            if binding:
                lines.append(f"- binding targets: {', '.join(binding)}")
        if parameter_frame is not None and parameter_names:
            block = parameter_frame[parameter_frame["candidate_id"].astype(str).isin(members)]
            if not block.empty:
                lines += ["", "| parameter | min | max |", "|---|---|---|"]
                for name in parameter_names:
                    if name in block.columns:
                        series = block[name].astype(float)
                        lines.append(f"| {name} | {series.min():.6g} | {series.max():.6g} |")
                correlations = _strong_correlations(block, parameter_names)
                if correlations:
                    lines += ["", "Strongest sampled parameter correlations:"]
                    lines += [
                        f"- {a} vs {b}: r = {r:+.2f}" for a, b, r in correlations
                    ]
        if baseline_frame is not None:
            block = baseline_frame[baseline_frame["candidate_id"].astype(str).isin(members)]
            if not block.empty:
                numeric = [c for c in block.columns if c != "candidate_id"]
                lines += ["", "| baseline state | min | max |", "|---|---|---|"]
                for column in numeric:
                    series = pd.to_numeric(block[column], errors="coerce").dropna()
                    if not series.empty:
                        lines.append(f"| {column} | {series.min():.6g} | {series.max():.6g} |")
        else:
            lines += [
                "",
                "- baseline-state ranges: unavailable; this stage contains no frozen "
                "candidate-level baseline-state table, so none were reconstructed or inferred",
            ]
        lines.append("")
    return "\n".join(lines)


def _strong_correlations(
    block: pd.DataFrame, names: Sequence[str], *, top: int = 5
) -> list[tuple[str, str, float]]:
    usable = [n for n in names if n in block.columns and block[n].astype(float).std() > 0]
    if len(usable) < 2 or len(block) < 3:
        return []
    matrix = block[usable].astype(float).corr()
    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            value = float(matrix.loc[a, b])
            if np.isfinite(value):
                pairs.append((a, b, value))
    pairs.sort(key=lambda item: (-abs(item[2]), item[0], item[1]))
    return pairs[:top]


def render_run_audit(
    facts: SummaryFacts,
    *,
    manifest: Mapping,
    kernel_audit: pd.DataFrame | None = None,
    resume: Mapping | None = None,
    recomputation_verified: bool = False,
) -> str:
    """Shard completeness, failures, resumes, hashes, seeds, recomputation."""
    f = facts.as_dict()
    contract = manifest.get("contract") or {}
    seeds = contract.get("seeds") or {}
    lines = [
        "# Run audit",
        "",
        f"**Stage:** {manifest.get('stage')}  |  **Model:** {manifest.get('model')}  "
        f"|  **Status:** `{manifest.get('status')}`",
        f"**Usable as a parent stage:** {'yes' if manifest.get('parentable') else 'no'}",
        "",
        "## Completeness",
        "",
        f"- frozen work units: {manifest.get('n_units')}",
        f"- unique scientific result keys: {manifest.get('n_unique_result_keys')}",
        f"- completed: {manifest.get('n_completed')}  |  incomplete: "
        f"{manifest.get('n_incomplete')}",
        f"- launch attempts consumed (never refunded): {manifest.get('consumed_attempts')}",
        f"- failed attempts: {f.get('n_failed_attempts')}  |  lost attempts: "
        f"{f.get('n_lost_attempts')}",
        "",
        "A failed or lost attempt is audit history. It is never a scientific result and never "
        "a rejected candidate.",
        "",
        "## Hashes",
        "",
        f"- ledger frozen sha256: `{manifest.get('ledger_frozen_sha256')}`",
        f"- parent manifest sha256: `{manifest.get('parent_manifest_sha256')}`",
        f"- target contract hash: `{contract.get('target_contract_hash')}`",
        f"- run plan sha256: `{contract.get('plan_sha256')}`",
        f"- canonical float serialisation: {contract.get('canonical_float_serialisation')}",
        "",
        "## Seed use",
        "",
        f"- discovery seeds (K={seeds.get('k_discovery')}): {seeds.get('discovery')}",
        f"- confirmation seeds (K={seeds.get('k_confirmation')}): {seeds.get('confirmation')}",
        f"- design RNG seed: {seeds.get('proposal_rng')}",
        f"- {seeds.get('design', '')}",
        "",
        "## Independent recomputation",
        "",
        f"- candidate means, variances, implausibility, promotion and region labels "
        f"recomputed from seed-level rows: **{'verified' if recomputation_verified else 'NOT verified'}**",
        "- the merger never trusts a shard summary",
        "",
    ]
    if resume:
        lines += [
            "## Resume",
            "",
            *[f"- {key}: {value}" for key, value in sorted(dict(resume).items())],
            "",
        ]
    if kernel_audit is not None and not kernel_audit.empty:
        lines += ["## Kernel quota reconciliation", ""]
        lines.append("| " + " | ".join(kernel_audit.columns) + " |")
        lines.append("|" + "---|" * len(kernel_audit.columns))
        for _, row in kernel_audit.iterrows():
            lines.append("| " + " | ".join(str(v) for v in row.tolist()) + " |")
        lines.append("")
    limitations = contract.get("limitations") or []
    if limitations:
        lines += ["## Declared limitations", "", *[f"- {item}" for item in limitations], ""]
    return "\n".join(lines)


def render_table_index(tables: Mapping[str, pd.DataFrame]) -> str:
    """Plain-language description, row unit and key columns for every table."""
    lines = [
        "# Table index",
        "",
        "Every table written by this stage, in plain language.",
        "",
    ]
    for name in sorted(tables):
        frame = tables[name]
        spec = TABLE_SPEC.get(name, {})
        lines += [
            f"## `{name}.csv`",
            "",
            f"- **row unit:** {spec.get('row_unit', 'unspecified')}",
            f"- **rows:** {len(frame)}",
            f"- **what it is:** {spec.get('description', 'no description registered')}",
            f"- **key columns:** {spec.get('key_columns', ', '.join(map(str, frame.columns[:6])))}",
            f"- **all columns:** {', '.join(map(str, frame.columns))}",
            "",
        ]
    unregistered = sorted(set(tables) - set(TABLE_SPEC))
    if unregistered:
        lines += [
            "## Unregistered tables",
            "",
            "These tables have no entry in `TABLE_SPEC` and must be documented before release:",
            *[f"- `{name}`" for name in unregistered],
            "",
        ]
    return "\n".join(lines)


def data_dictionary(tables: Mapping[str, pd.DataFrame]) -> dict:
    """Machine-readable schema for every table, for the raw-data consumer."""
    return {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence_states": {
            nc.EVIDENCE_CONFIRMED: "support demonstrated under the declared design",
            nc.EVIDENCE_NO_SUPPORT: "the complete declared design ran and found none",
            nc.EVIDENCE_UNRESOLVED: "coverage or precision insufficient to classify",
            nc.EVIDENCE_EXECUTION_FAILURE: "simulation/worker failure; not a scientific result",
            nc.EVIDENCE_NOT_RUN: "never dispatched; not a scientific result",
        },
        "local_support_labels": list(nc.LOCAL_SUPPORT_LABELS),
        "edge_labels": list(nc.EDGE_LABELS),
        "tables": {
            name: {
                **TABLE_SPEC.get(name, {}),
                "n_rows": int(len(frame)),
                "columns": [
                    {"name": str(c), "dtype": str(frame[c].dtype)} for c in frame.columns
                ],
            }
            for name, frame in sorted(tables.items())
        },
    }


def assert_ids_resolve(tables: Mapping[str, pd.DataFrame]) -> None:
    """Candidate and region ids must resolve uniquely across tables (spec sec. 9)."""
    problems: list[str] = []
    support = tables.get("local_support")
    if support is not None and not support.empty:
        ids = support["candidate_id"].astype(str)
        if ids.duplicated().any():
            problems.append(
                f"local_support has duplicate candidate_id: "
                f"{sorted(ids[ids.duplicated()].unique())}"
            )
    comps = tables.get("components")
    if comps is not None and not comps.empty:
        cids = comps["component_id"].astype(str)
        if cids.duplicated().any():
            problems.append(f"components has duplicate component_id: {sorted(cids.unique())}")
        members: list[str] = []
        for value in comps["members"].astype(str):
            members.extend(m for m in value.split("|") if m)
        if len(members) != len(set(members)):
            duplicated = sorted({m for m in members if members.count(m) > 1})
            problems.append(f"a candidate appears in more than one component: {duplicated}")
        if support is not None and not support.empty:
            unknown = sorted(set(members) - set(support["candidate_id"].astype(str)))
            if unknown:
                problems.append(f"component members absent from local_support: {unknown}")
    reps = tables.get("representatives")
    if reps is not None and not reps.empty and comps is not None and not comps.empty:
        unknown = sorted(
            set(reps["component_id"].astype(str)) - set(comps["component_id"].astype(str))
        )
        if unknown:
            problems.append(f"representatives name unknown components: {unknown}")
    scores = tables.get("candidate_scores")
    if scores is not None and not scores.empty:
        ids = scores["design_id"].astype(str)
        if ids.duplicated().any():
            problems.append("candidate_scores has duplicate design_id")
    if problems:
        raise ContractError("id resolution failed:\n" + "\n".join(f"  - {p}" for p in problems))


def write_package(
    directory: str | Path,
    tables: Mapping[str, pd.DataFrame],
    *,
    facts: SummaryFacts,
    manifest: Mapping,
    question: str,
    unsigned: Sequence[str] = (),
    next_decision: str = "",
    kernel_audit: pd.DataFrame | None = None,
    resume: Mapping | None = None,
    recomputation_verified: bool = False,
    parameter_frame: pd.DataFrame | None = None,
    parameter_names: Sequence[str] = (),
    baseline_frame: pd.DataFrame | None = None,
    binding_targets: Mapping[str, str] | None = None,
    overwrite_completed: bool = False,
) -> dict[str, str]:
    """Write the tidy tables, the data dictionary and the four summaries."""
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    assert_ids_resolve(tables)

    written: dict[str, str] = {}
    for name, frame in sorted(tables.items()):
        written[f"{name}.csv"] = str(ne.atomic_write_frame(
            base / f"{name}.csv", frame, overwrite_completed=overwrite_completed
        ))
    written["data_dictionary.json"] = str(ne.atomic_write_json(
        base / "data_dictionary.json", data_dictionary(tables),
        overwrite_completed=overwrite_completed,
    ))
    written["summary_facts.json"] = str(ne.atomic_write_json(
        base / "summary_facts.json", facts.as_dict(),
        overwrite_completed=overwrite_completed,
    ))
    written["EXECUTIVE_SUMMARY.md"] = str(ne.atomic_write_text(
        base / "EXECUTIVE_SUMMARY.md",
        render_executive_summary(
            facts, question=question, unsigned=unsigned, next_decision=next_decision
        ),
        overwrite_completed=overwrite_completed,
    ))
    written["REGION_REPORT.md"] = str(ne.atomic_write_text(
        base / "REGION_REPORT.md",
        render_region_report(
            tables.get("components", pd.DataFrame()),
            tables.get("local_support", pd.DataFrame()),
            parameter_frame=parameter_frame,
            parameter_names=parameter_names,
            baseline_frame=baseline_frame,
            binding_targets=binding_targets,
        ),
        overwrite_completed=overwrite_completed,
    ))
    written["RUN_AUDIT.md"] = str(ne.atomic_write_text(
        base / "RUN_AUDIT.md",
        render_run_audit(
            facts, manifest=manifest, kernel_audit=kernel_audit, resume=resume,
            recomputation_verified=recomputation_verified,
        ),
        overwrite_completed=overwrite_completed,
    ))
    written["TABLE_INDEX.md"] = str(ne.atomic_write_text(
        base / "TABLE_INDEX.md", render_table_index(tables),
        overwrite_completed=overwrite_completed,
    ))
    figures = write_figures(
        base / "figures", tables,
        parameter_frame=parameter_frame, parameter_names=parameter_names,
        target_order=[
            c.split(":", 1)[1]
            for c in (tables.get("candidate_scores", pd.DataFrame()).columns)
            if str(c).startswith("mean:")
        ],
    )
    written["figures.json"] = str(ne.atomic_write_json(
        base / "figures.json",
        {
            "figure_set": list(FIGURE_SET),
            "results": figures,
            "note": (
                "Figures are a supplement. Every number shown is already in the tidy "
                "tables; a skipped figure records why it was skipped rather than being "
                "drawn empty."
            ),
        },
        overwrite_completed=True,
    ))
    return written


#: The fixed figure set required by spec sec. 8.  Fixed, not open-ended: the
#: package always offers the same five views so runs stay comparable.
FIGURE_SET = (
    "region_map",
    "local_support",
    "bridge_graph",
    "p_profile",
    "target_residual_heatmap",
)


def write_figures(
    directory: str | Path,
    tables: Mapping[str, pd.DataFrame],
    *,
    parameter_frame: pd.DataFrame | None = None,
    parameter_names: Sequence[str] = (),
    target_order: Sequence[str] = (),
) -> dict[str, str]:
    """Write the small fixed figure set (spec sec. 8).

    Figures are a *supplement*: every number they show is already in the tidy
    tables, because the specification forbids reporting a result only in a
    binary object.  A figure whose inputs are absent is skipped and reported as
    skipped rather than drawn empty, so an empty panel can never be mistaken
    for a finding of "nothing there".

    Returns ``{figure_name: path or reason-it-was-skipped}``.
    """
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")  # headless; never opens a window on a server
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {name: f"skipped: matplotlib unavailable ({exc})" for name in FIGURE_SET}

    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}

    def save(fig, name: str) -> None:
        path = base / f"{name}.png"
        fig.savefig(path, dpi=144, bbox_inches="tight")
        plt.close(fig)
        out[name] = str(path)

    support = tables.get("local_support")

    # 1. Normalised region map / projections.
    if parameter_frame is not None and len(parameter_names) >= 2 and support is not None:
        labels = dict(zip(
            support["candidate_id"].astype(str),
            support["local_support_label"].astype(str),
        ))
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
        for axis, (xi, yi) in zip(axes, ((0, 1), (0, 2) if len(parameter_names) > 2 else (1, 0))):
            x_name, y_name = parameter_names[xi], parameter_names[yi]
            for label in LOCAL_SUPPORT_ORDER:
                ids = [c for c, lab in labels.items() if lab == label]
                block = parameter_frame[
                    parameter_frame["candidate_id"].astype(str).isin(ids)
                ]
                if block.empty or x_name not in block or y_name not in block:
                    continue
                axis.scatter(
                    block[x_name].astype(float), block[y_name].astype(float),
                    label=label, s=28, alpha=0.85,
                )
            axis.set_xlabel(x_name)
            axis.set_ylabel(y_name)
        axes[0].legend(fontsize=7, loc="best")
        fig.suptitle("Sampled region map (projections; not a density or a posterior)")
        save(fig, "region_map")
    else:
        out["region_map"] = "skipped: no parameter frame or too few coordinates"

    # 2. Local-support plot.
    if support is not None and not support.empty:
        counts = (
            support["local_support_label"].astype(str)
            .value_counts()
            .reindex(LOCAL_SUPPORT_ORDER, fill_value=0)
        )
        fig, axis = plt.subplots(figsize=(7, 4))
        axis.bar(range(len(counts)), counts.to_numpy(), color="#4C72B0")
        axis.set_xticks(range(len(counts)))
        axis.set_xticklabels(counts.index, rotation=20, ha="right", fontsize=8)
        axis.set_ylabel("anchors")
        axis.set_title(
            "Local-support labels\n"
            "'no support detected' describes the declared design, not isolation",
            fontsize=9,
        )
        save(fig, "local_support")
    else:
        out["local_support"] = f"skipped: {nc.EVIDENCE_NOT_RUN}"

    # 3. Bridge graph.
    edges = tables.get("edge_verdicts")
    edge_columns = {"left_candidate_id", "right_candidate_id", "edge_label"}
    if edges is not None and not edges.empty and edge_columns <= set(edges.columns):
        nodes = sorted(
            set(edges["left_candidate_id"].astype(str))
            | set(edges["right_candidate_id"].astype(str))
        )
        angles = np.linspace(0.0, 2.0 * np.pi, len(nodes), endpoint=False)
        position = {
            node: (float(np.cos(a)), float(np.sin(a))) for node, a in zip(nodes, angles)
        }
        fig, axis = plt.subplots(figsize=(6, 6))
        for _, row in edges.iterrows():
            left = position[str(row["left_candidate_id"])]
            right = position[str(row["right_candidate_id"])]
            supported = str(row["edge_label"]) == nc.EDGE_SAMPLED_PATH_SUPPORTED
            axis.plot(
                [left[0], right[0]], [left[1], right[1]],
                color="#2E7D32" if supported else "#BDBDBD",
                linestyle="-" if supported else "--",
                linewidth=1.8 if supported else 1.0,
            )
        for node, (x, y) in position.items():
            axis.scatter([x], [y], s=60, color="#333333", zorder=3)
            axis.annotate(node, (x, y), fontsize=7, xytext=(4, 4),
                          textcoords="offset points")
        axis.set_axis_off()
        axis.set_title(
            "Bridge graph: solid = sampled-path supported, dashed = unresolved\n"
            "(unresolved is not disconnection)", fontsize=9,
        )
        save(fig, "bridge_graph")
    elif edges is not None and not edges.empty:
        out["bridge_graph"] = (
            "skipped: edge_verdicts lacks the endpoint columns "
            f"{sorted(edge_columns - set(edges.columns))}"
        )
    else:
        out["bridge_graph"] = f"skipped: {nc.EVIDENCE_NOT_RUN}"

    # 4. p profile.
    coverage = tables.get("p_coverage")
    if coverage is not None and not coverage.empty:
        fig, axis = plt.subplots(figsize=(7, 4))
        lower = coverage["bin_lower"].astype(float).to_numpy()
        upper = coverage["bin_upper"].astype(float).to_numpy()
        axis.bar(
            lower, coverage["n_confirmed"].astype(int).to_numpy(),
            width=(upper - lower), align="edge", color="#4C72B0",
            edgecolor="white", label="confirmed",
        )
        axis.bar(
            lower, coverage["n_evaluated"].astype(int).to_numpy(),
            width=(upper - lower), align="edge", facecolor="none",
            edgecolor="#999999", linestyle="--", label="evaluated",
        )
        axis.set_xlabel("p (annual AIDS-symptom diagnosis probability)")
        axis.set_ylabel("candidates")
        axis.legend(fontsize=8)
        axis.set_title(
            "p coverage: descriptive only\n"
            "a covered bin is not evidence that every value in it is plausible",
            fontsize=9,
        )
        save(fig, "p_profile")
    else:
        out["p_profile"] = "skipped: no p coverage table (M0 run, or work not run)"

    # 5. Target-residual heatmap.
    scores = tables.get("candidate_scores")
    columns = [f"mean:{name}" for name in target_order]
    if scores is not None and not scores.empty and all(c in scores for c in columns) and columns:
        matrix = scores[columns].astype(float).to_numpy()
        fig, axis = plt.subplots(figsize=(max(6, 0.5 * len(columns)), 4.5))
        image = axis.imshow(matrix, aspect="auto", cmap="RdBu_r")
        axis.set_xticks(range(len(target_order)))
        axis.set_xticklabels(target_order, rotation=90, fontsize=6)
        axis.set_ylabel("candidate")
        fig.colorbar(image, ax=axis, label="mean simulated output")
        axis.set_title("Target outputs by candidate", fontsize=9)
        save(fig, "target_residual_heatmap")
    else:
        out["target_residual_heatmap"] = "skipped: no per-target means in candidate_scores"

    return out


#: Stable display order for the local-support labels in figures and tables.
LOCAL_SUPPORT_ORDER = list(nc.LOCAL_SUPPORT_LABELS)


def p_coverage_table(
    bins: Sequence[float],
    p_values: Sequence[float],
    confirmed: Sequence[bool],
) -> pd.DataFrame:
    """Descriptive coverage of the M1 ``p`` profile.

    A bin with a confirmed point is *covered*.  That is a description of where
    the design happened to confirm points, not evidence that every value in the
    bin is plausible, and the column names say so.
    """
    edges = [float(b) for b in bins]
    if sorted(edges) != edges:
        raise ContractError(f"p bins must be ascending: {edges}")
    if len(p_values) != len(confirmed):
        raise ContractError("p_values and confirmed differ in length")
    values = np.asarray([float(v) for v in p_values], dtype=float)
    flags = np.asarray([bool(c) for c in confirmed], dtype=bool)
    rows = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        # Half-open bins, with the last bin closed so the top edge is covered.
        inside = (values >= lower) & (
            (values < upper) if upper != edges[-1] else (values <= upper)
        )
        rows.append({
            "bin_lower": lower,
            "bin_upper": upper,
            "n_evaluated": int(inside.sum()),
            "n_confirmed": int((inside & flags).sum()),
            "descriptive_coverage_only": True,
        })
    return pd.DataFrame(rows)


__all__ = [name for name in dir() if not name.startswith("_")]

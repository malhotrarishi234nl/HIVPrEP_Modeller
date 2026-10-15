# Robust-NROY seed audit: scope and remaining gaps

## Current verdict

`robust_nroy_seed_audit/1.0.0` is **INCOMPLETE** and cannot authorize a run.
The implementation advances the gate by making the inventory reproducible and
reviewable; it does not choose, allocate, recommend, or reserve any seed.

The current source-scope proposal is `seed_source_scope.json` in this report
directory. It is deliberately
`UNRESOLVED` for both owner scope and source coverage. The deterministic output
is `evidence/robust_nroy_seed_audit_20261015.json`.

The current mounted snapshot inventories 74,281 output files plus 156 tracked
configuration/documentation files. It content-scans 2,905 files, records 920
distinct parsed RNG integers, 75,595 ambiguous references, and 43 unparseable
references. Another 71,531 output files are inventoried without content
scanning under the proposed rules. These counts describe this mounted snapshot;
they are not evidence that the source scope is exhaustive.

## What is inspected

- Every Git-tracked file under `configs/` and `documentation/` is inventoried.
  JSON is parsed structurally, CSV columns with explicit RNG semantics are
  parsed, and prose references remain ambiguous rather than becoming numbers.
- Only the mounted project `outputs/` tree is inventoried. The scanner never
  searches parent directories, the home directory, cloud services, HPC storage,
  or unrelated private data.
- In mounted outputs, declared manifest/plan/design/result/summary/validation/
  provenance/metadata JSON is parsed, journals are parsed line by line, every
  CSV header is inspected and seed-bearing CSV columns are read, and declared
  text formats are searched for references requiring review.
- Raw non-metadata JSON, archives, spreadsheets, PDFs, NPZ files, images, and
  other binary formats are inventoried but not opened. This exclusion remains a
  coverage blocker until the owner reviews it or supplies additional evidence.

The scanner recognises explicit simulation, replicate, discovery,
confirmation, smoke, proposal/design, and other operational RNG fields. It does
not silently reinterpret epidemiological parameters such as `seed_prevalence`,
prose ranges such as `200000-200004`, formulas, `UNSIGNED`, or malformed values.
Those appear as ambiguous or unparseable evidence.

## Ordinary commands

Regenerate the current incomplete artifact without running the model:

```bash
.venv/bin/python tools/audit_robust_nroy_seeds.py \
  --scope documentation/reports/calibration/robust_nroy_readiness_2026-10-15/seed_source_scope.json \
  --out evidence/robust_nroy_seed_audit_20261015.json
```

Verify that the committed artifact still matches the mounted evidence:

```bash
.venv/bin/python tools/audit_robust_nroy_seeds.py \
  --scope documentation/reports/calibration/robust_nroy_readiness_2026-10-15/seed_source_scope.json \
  --check evidence/robust_nroy_seed_audit_20261015.json
```

After the owner has approved a complete scope and separately supplied candidate
blocks, add
`--candidate-blocks <candidate-seed-blocks.json> --plan <signed-plan.json>
--generated-utc <ISO-8601-UTC> --require-pass`. A production-eligible `FINAL`
is generated only when those blocks exactly match the fully signed plan and the
complete scan has no unresolved, ambiguous, unparseable, or overlap findings.
The candidate document must have schema
`robust_nroy_candidate_seed_blocks/1.0.0` and explicit integer arrays
`discovery`, `confirmation`, plus one integer `proposal_rng`. The scanner never
creates that document.

## Fail-closed proof rule

Candidate comparisons are `NOT_EVALUATED` when candidate blocks are absent.
When supplied, internal and prior-source overlaps are computed, but the result
is only `PROVEN` when all of the following are true:

1. owner scope is `APPROVED` with reviewer and date;
2. source coverage is `COMPLETE` and declared exclusions were reviewed;
3. no ambiguous or unparseable references remain;
4. explicit candidate discovery and confirmation blocks are internally unique;
5. discovery and confirmation are mutually disjoint;
6. the proposal RNG is disjoint from both simulation blocks; and
7. all three candidate blocks are disjoint from every parsed prior RNG seed.

Even a seed-audit `PASS` is only this one evidence gate. It does not sign the
scientific plan, prove model identity, price compute, or authorize execution.

## Exact remaining gaps

- The owner has not approved that the declared repository plus mounted-output
  scope exhausts every historical run location.
- Deleted work, remote/cloud/HPC work, collaborator-held artifacts, and prior
  machines are not represented unless copied into an approved source scope.
- Excluded raw and binary formats need an explicit owner disposition; file
  inventory alone does not prove that they contain no additional RNG evidence.
- Textual and seed-like references require review so epidemiological seed-state
  parameters are not confused with RNG seeds and genuine RNG references are not
  missed.
- Candidate discovery, confirmation, and proposal blocks do not yet exist and
  were intentionally not invented here, so disjointness is not evaluated.

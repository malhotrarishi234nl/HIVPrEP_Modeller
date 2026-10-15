# Robust-NROY seed audit: scope and remaining gaps

## Current verdict

`robust_nroy_seed_audit/1.2.0` is **INCOMPLETE** and cannot authorize a run.
The implementation advances the gate by making the inventory reproducible and
reviewable; it does not choose, allocate, recommend, or reserve any seed.

The current source-scope proposal is `seed_source_scope.json` in this report
directory. It is deliberately `UNRESOLVED` for both owner scope and source
coverage. It now inventories every Git-tracked path and content-scans tracked
JSON, JSONL, CSV, documentation, Python, shell, and JavaScript text. Three
binary tracked files are exact-path/hash exclusions whose coverage status is
still unresolved.

The committed `evidence/robust_nroy_seed_audit_20261015.json` is the preceding
v1.0 mounted-snapshot artifact: 74,281 output files plus 156 tracked
configuration/documentation files, 2,905 content-scanned files, 920 distinct
parsed RNG integers, 75,597 ambiguous references, 43 unparseable references,
and 71,531 output files inventoried without content scanning. It is retained as
historical evidence and must not be described as a v1.2 result. A v1.2 artifact
must be regenerated where the same private `outputs/` tree is mounted; the
isolated source checkout does not contain that untracked tree. No prior finding
was fabricated as resolved during this change.

## What is inspected

- Every Git-tracked file is inventoried. JSON is parsed structurally, CSV
  columns with explicit RNG semantics are parsed, source/scripts/manifests are
  scanned as text, and prose references remain ambiguous rather than becoming
  numbers. Exact hash-bound binary exclusions remain coverage blockers until
  reviewed.
- Only the mounted project `outputs/` tree is inventoried. The scanner never
  searches parent directories, the home directory, cloud services, HPC storage,
  or unrelated private data.
- In mounted outputs, declared manifest/plan/design/result/summary/validation/
  provenance/metadata JSON is parsed, journals are parsed line by line, every
  CSV header is inspected and seed-bearing CSV columns are read, and declared
  text formats are searched for references requiring review.
- Raw non-metadata JSON, archives, spreadsheets, PDFs, NPZ files, images, and
  other binary formats are not parsed for seeds. Every such file is
  content-bound by exact path, parser-exclusion kind, byte size, and SHA-256 in
  the audit's deterministic exclusion index. It remains a blocker until an
  exact per-file disposition or exact whole-index review covers those bytes.

The scanner recognises explicit simulation, replicate, discovery,
confirmation, smoke, proposal/design, and other operational RNG fields. It does
not silently reinterpret epidemiological parameters such as `seed_prevalence`,
prose ranges such as `200000-200004`, formulas, `UNSIGNED`, or malformed values.
Those appear as ambiguous or unparseable evidence.

## Reviewed resolution contract

The v1.2 scanner accepts an optional `--resolutions` document with schema
`robust_nroy_seed_finding_resolutions/1.1.0`. The incomplete starting template
and JSON Schema are:

- `templates/seed_finding_resolutions.json.template`
- `templates/seed_finding_resolutions.schema.json`

An exact decision binds finding kind, repository-relative source, location,
source-content SHA-256, value SHA-256, classification (`RNG` or `NON_RNG`),
reviewer, UTC review time, and rationale. An RNG decision must also supply the
exact integer(s) and RNG role; those integers are added to the prior-seed set
used by collision checks. A NON_RNG decision removes only that exact current
finding.

Reviewed rules are deliberately narrow: a bounded line range (at most 1,000
lines), a non-root JSON-pointer subtree plus an explicit list of leaf keys, or
one exact CSV column in one exact source. Globs and regular expressions are not
accepted. Every rule also binds the source SHA-256, expected match count, and
the canonical SHA-256 of the sorted exact finding IDs. RNG rules enumerate
integers per finding ID. A stale source/scope hash, absent match, unexpected
match, duplicate entry, overlapping rule, wildcard, or incomplete RNG mapping
is an audit error, not an ignored resolution.

`status: REVIEWED` means only that the resolution document itself has completed
review. Findings not matched by a valid decision remain blockers, and owner
scope/source coverage remain independent blockers.

Output exclusions have no global review switch. For manageable sets, each
disposition repeats the exact path, kind, byte size, and SHA-256 from the fresh
audit index. For a large inventory, one whole-index review may instead bind the
exact file count, total bytes, and canonical SHA-256 of the complete ordered
per-file index. The two modes cannot be mixed. Missing or extra paths, same-size
byte changes, a stale aggregate, or an unbound file fail closed.

An external execution-location row may move from `UNRESOLVED` only when it
names its reviewer and UTC review time and cites exactly one distinct, typed,
hash-bound `robust_nroy_external_seed_location_review/1.0.0` record. That
record must match the location class, status, reviewer, and time; name the
locations and accounts or owners checked; state the search method; and either
index source hashes for `COVERED` or give an explicit no-source basis for
`REVIEWED_NONE`. The four location classes cannot reuse one evidence file. A
bare note such as `"checked HPC"`, an unhashed URL, or reviewer recollection is
not accepted. Start from the deliberately incomplete template and schema in
`templates/external_seed_location_review.*`; global owner approval remains
separately required.

## Ordinary commands

Generate the plan-bound production prerequisite without running the model:

```bash
.venv/bin/python tools/audit_robust_nroy_seeds.py \
  --scope documentation/reports/calibration/robust_nroy_readiness_2026-10-15/seed_source_scope.json \
  --resolutions <reviewed-seed-finding-resolutions.json> \
  --out evidence/robust_nroy/seed_audit.json
```

Verify that the untracked production prerequisite still matches the mounted
evidence:

```bash
.venv/bin/python tools/audit_robust_nroy_seeds.py \
  --scope documentation/reports/calibration/robust_nroy_readiness_2026-10-15/seed_source_scope.json \
  --resolutions <reviewed-seed-finding-resolutions.json> \
  --check evidence/robust_nroy/seed_audit.json
```

After the owner has approved a complete scope and separately supplied candidate
blocks, add
`--resolutions <reviewed-seed-finding-resolutions.json> --candidate-blocks
<candidate-seed-blocks.json> --plan <signed-plan.json>
--generated-utc <ISO-8601-UTC> --require-pass`. A production-eligible `FINAL`
is generated only when those blocks exactly match the fully signed plan and the
complete scan has no unresolved ambiguous/unparseable finding, resolution,
excluded-output disposition, or overlap finding. The audit output,
reviewed-resolution document,
and candidate-block document must remain at untracked paths outside the
declared `outputs/` scan. The CLI refuses self-referential control paths because
rewriting one would change the next inventory and could count its candidate
seeds as prior use. The tracked
`evidence/robust_nroy_seed_audit_20261015.json` is retained only as historical
v1.0 evidence; never overwrite or promote it.
The candidate document must have schema
`robust_nroy_candidate_seed_blocks/1.0.0` and explicit integer arrays
`discovery`, `confirmation`, plus one integer `proposal_rng`. The scanner never
creates that document. The audit records both its exact input path and raw-file
SHA-256, plus the canonical payload SHA-256; the production validator reloads
the strict JSON file and rechecks all three seed blocks.

## Fail-closed proof rule

Candidate comparisons are `NOT_EVALUATED` when candidate blocks are absent.
When supplied, internal and prior-source overlaps are computed, but the result
is only `PROVEN` when all of the following are true:

1. owner scope is `APPROVED` with reviewer and date;
2. source coverage is `COMPLETE`, every external location has its own valid
   typed review record, and every mounted-output exclusion has a byte-exact
   disposition or exact content-index review;
3. every ambiguous or unparseable reference has an exact, current, reviewed
   RNG or NON_RNG disposition;
4. explicit candidate discovery and confirmation blocks are internally unique;
5. discovery and confirmation are mutually disjoint;
6. the proposal RNG is disjoint from both simulation blocks; and
7. all three candidate blocks are disjoint from every parsed prior RNG seed.

Even a seed-audit `PASS` is only this one evidence gate. It does not sign the
scientific plan, prove model identity, price compute, or authorize execution.
Before production readiness accepts that PASS, it deterministically rebuilds
the entire audit from the current scope, resolutions, candidate blocks, signed
plan, tracked sources, and mounted outputs and requires exact canonical
equality. Consequently, validation reads and hashes the full declared source
inventory; for the historical 74,281-file mount this is intentional I/O, not a
simulation or a cached assertion.

## Exact remaining gaps

- The owner has not approved that the declared repository plus mounted-output
  scope exhausts every historical run location.
- Deleted work, remote/cloud/HPC work, collaborator-held artifacts, and prior
  machines each have an explicit unresolved coverage decision in the scope
  manifest; they are not represented unless copied into an approved source
  scope or reviewed with evidence.
- Excluded raw and binary formats need an explicit owner disposition; file
  inventory alone does not prove that they contain no additional RNG evidence.
- Textual and seed-like references require review so epidemiological seed-state
  parameters are not confused with RNG seeds and genuine RNG references are not
  missed.
- Candidate discovery, confirmation, and proposal blocks do not yet exist and
  were intentionally not invented here, so disjointness is not evaluated.

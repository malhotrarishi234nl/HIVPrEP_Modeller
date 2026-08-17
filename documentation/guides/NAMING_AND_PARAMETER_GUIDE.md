# Naming Conventions and Parameter Change Guide — v11

This document governs how concepts are named across the codebase, how config schema
is structured, and how to safely introduce or modify a parameter. It was written
after a full structural audit of v11 and is grounded in the specific inconsistencies
found there — not generic style advice.

Read this before writing a patch. Read it again before merging one.

---

## Part 1 — Naming Conventions

### 1.1 Canonical name registry

The following table fixes the one true name for every concept that has historically
appeared under multiple names. When in doubt, use the name in the "Canonical" column.
Do not introduce synonyms.

| Concept | Canonical name | Deprecated / forbidden aliases |
|---|---|---|
| PrEP arm with no PrEP | `no_prep` | `none`, `control`, `base` |
| PrEP arm — oral product | `oral_prep` | `oral`, `cabotegravir_oral`, `prep_arm1` |
| PrEP arm — injectable product | `lenacapavir` | `lena`, `lnv`, `cab_la`, `prep_arm2` |
| PrEP state label in population arrays | `"Oral_PrEP"` / `"Lenacapavir"` | lowercase variants, abbreviations |
| Arm-to-state mapping constant | `PREP_ARM_TO_STATE` | `_ARM_TO_PREP_STATE`, `_INTERVENTION_TO_PREP_STATE` |
| ART on state | `"on_art"` | `"ART"`, `"art"`, `"treated"`, `on_treatment` |
| ART off state | `"off_art"` | `"no_art"`, `"naive"`, `"untreated"` |
| Diagnosed, not on ART | `"diagnosed_off_art"` | `"aware"`, `"diagnosed"` (unqualified) |
| Undiagnosed | `"undiagnosed"` | `"naive"`, `"undx"` |
| Step fraction (dt) | `step_fraction` | `dt`, `time_step`, `frac`, `step_size_frac` |
| Simulation timestep in years | `timestep_years` | `step_size`, `step_length`, `delta_t` |
| Heterosexual transmission route | `"het_male"` / `"het_female"` | `"hetm"`, `"hetf"`, any substring-matched form |
| MSM transmission route | `"msm"` | `"MSM"`, `"gay"`, `"m2m"` |
| Billing moment — recurring cost | `"ongoing"` | `"recurring"`, `"per_step"`, `"annual"` |
| Billing moment — at treatment start | `"initiation"` | `"start"`, `"onset"` |
| Billing moment — one-time (lump) | `"once"` | `"lump"`, `"single"`, `"one_off"` |
| Billing moment — at diagnosis | `"diagnosis"` | `"dx"`, `"aware_event"` |
| Billing moment — at AIDS death | `"death"` | `"terminal"`, `"end_of_life"` |

### 1.2 Casing rules by context

**Python identifiers:**

| Context | Rule | Example |
|---|---|---|
| Module-level constants | `UPPER_SNAKE_CASE` | `PREP_ARM_TO_STATE`, `ARM_ORDER` |
| Private module constants | `_UPPER_SNAKE_CASE` | `_SEVERITY_GRADES` |
| Functions and methods | `lower_snake_case` | `apply_fractional_aging` |
| Classes | `PascalCase` | `ScheduleManager`, `ChildhoodPool` |
| Local variables | `lower_snake_case` | `step_fraction`, `art_rate` |
| Debug / instrumentation flags | `DEBUG_<CONCEPT>` — and they must be `False` in committed code | `DEBUG_FERTILITY` |

**Config / JSON keys:**

| Context | Rule | Example |
|---|---|---|
| Top-level section keys | `lower_snake_case` | `"art_schedule"`, `"prep_schedules"` |
| Arm names | `lower_snake_case` | `"oral_prep"`, `"no_prep"` |
| PrEP state labels (population arrays) | `TitleCase` with underscore | `"Oral_PrEP"`, `"Lenacapavir"` |
| Distribution type keys | `lowercase` | `"beta"`, `"gamma"`, `"lognormal"` |
| Billing moment values | `lowercase` | `"ongoing"`, `"initiation"` |

**Display / report strings:**
Drive from a single `ARM_LABELS` dict in `hta_report.py`. Do not write display
strings inline anywhere else.

### 1.3 File naming

| File type | Rule | Example |
|---|---|---|
| Core model module | `lower_snake_case.py` | `transmission_corrected.py` |
| Runner / entry point | `run_<scope>.py` | `run_v3.py`, `run_scenarios.py` |
| Config utility | `config_<role>.py` | `config_reader.py`, `config_utils.py` |
| Documentation | `UPPER_SNAKE_CASE.md` | `NAMING_AND_PARAMETER_GUIDE.md` |
| Do NOT create | `*_old.py`, `*_v2.py`, `*_backup.py`, `*_temp.py`, `*_new.py` | — |

When retiring a module, delete it (with a commit message explaining why) rather
than leaving a tombstone file. `hta_config_compat.py` is the cautionary example.

### 1.4 What not to name things

Forbidden patterns anywhere in committed code:

- `temp`, `tmp`, `test_`, `debug_`, `_new`, `_old`, `_v2`, `_backup`, `_PATCH`,
  `PATCH_n`, version tags like `# V11.7.1` in logic comments
- Single-letter variables outside tight numeric loops (`i`, `j`, `k` in a
  numpy inner loop are fine; `x` as a PrEP arm key in `_build_arm_config` is not)
- Magic sentinel integers (`999` for open-ended age bin — use `float("inf")` or
  a named constant `OPEN_AGE_SENTINEL`)

---

## Part 2 — Schema Rules of Thumb

These rules govern the config JSON structure. They exist because the audit found
several places where the validator accepted values the runtime silently ignored.

### 2.1 The cardinal rule

> **If the validator accepts it, the runtime must handle it.
> If the runtime cannot handle it, the validator must reject it.
> These two must never be out of sync.**

Concretely: after any change to a validator, search for all runtime routing logic
that depends on the same field and confirm coverage.

### 2.2 Billing moment discipline

The full set of accepted billing moments is:

```python
BILLING_MOMENTS = {"ongoing", "initiation", "once", "diagnosis", "death"}
```

This is the single source of truth. Both `config_utils._validate_ae_items` and
`adverse_events._compute_v2` must route every member of this set. Any new billing
moment must be added to the constant first, then both the validator and runtime
routing updated in the same commit.

### 2.3 PrEP schedule shape

All PrEP initiation/dropout schedule blocks must follow this nested shape:

```json
{
  "prep_schedules": {
    "oral_prep": {
      "initiation": {
        "schedule": { "2020": 0.05, "2025": 0.08 },
        "distribution": { "type": "beta", "alpha": 5, "beta": 95 }
      },
      "dropout": {
        "schedule": { "2020": 0.10 },
        "distribution": { "type": "beta", "alpha": 10, "beta": 90 }
      }
    }
  }
}
```

The `"schedule"` sub-key is mandatory. Never write a flat `{ year: rate }` dict
directly under `"initiation"` or `"dropout"`. This is the shape that caused the
silent no-op bug in `run_scenarios._build_arm_config`.

### 2.4 Distribution type vocabulary

Use lowercase in all config files:

```
"beta" | "gamma" | "lognormal" | "normal" | "uniform" | "fixed"
```

`"fixed"` means a point value, no uncertainty. Do not use `"Fixed"`, `"Beta"`, etc.
The Python-side validator `VALID_HTA_DIST_TYPES` is the authoritative set.
`KNOWN_DISTRIBUTION_TYPES` (capitalised set) in `config_utils.py` is the source of
a known inconsistency and should be retired once the distribution-casing fix lands.

### 2.5 ART schedule shape

`art_schedule` is mandatory. The model does not have a safe default. A config that
omits `art_schedule` must be rejected at load time with an explicit error, not
silently patched with placeholder rates. Required shape:

```json
{
  "art_schedule": {
    "initiation": {
      "schedule": { "1996": 0.05, "2010": 0.40, "2016": 0.70 }
    },
    "dropout": {
      "schedule": { "1996": 0.20, "2010": 0.08 }
    }
  }
}
```

### 2.6 Forbidden schema patterns

| Pattern | Why forbidden |
|---|---|
| Flat year→rate dict directly under `"initiation"` or `"dropout"` | Breaks `ScheduleManager` parsing (see B1) |
| Omitting `art_schedule` entirely | Activates fabricated fallback constants |
| Capitalised distribution type (`"Beta"`, `"Gamma"`) | Fails `VALID_HTA_DIST_TYPES` validator |
| `billing_moment` value not in `BILLING_MOMENTS` | Either silent zero or validator rejection depending on path taken |
| Hardcoded ART state indices in new code | Must read from `GroupSystem` |
| New PrEP product without updating `PREP_ARM_TO_STATE` | State lookup silently fails |

### 2.7 Where not to put things

| Information | Wrong place | Right place |
|---|---|---|
| PrEP arm display strings | Inline in any module | `hta_report.ARM_LABELS` |
| PrEP arm → state mapping | Per-module local dict | `PREP_ARM_TO_STATE` in a shared constants module |
| Child age bin labels | `demography.py` and `migration.py` independently | Single shared constant, imported by both |
| Step fraction | Rederived from config step-size | Passed through from engine; one derivation only |
| ART state array indices | Hardcoded integers | Runtime lookup from `GroupSystem` |

---

## Part 3 — Process for Introducing or Modifying a Parameter

Follow these steps in order. Do not skip steps to move faster — each step exists
because a real bug in this codebase was caused by skipping it.

### Step 1 — Classify the change

Before writing any code, answer:

| Question | If yes |
|---|---|
| Does this parameter appear in the config JSON? | It needs a validator update (Step 3) and a schema entry (Step 4) |
| Does it affect cost or QALY computation? | It needs a billing moment, and `adverse_events._compute_v2` or `economics.py` must route it |
| Does it involve a PrEP product name? | Update `PREP_ARM_TO_STATE` in the constants module — nowhere else |
| Does it involve a new state in the population array? | Update `GroupSystem` first; derive all indices from it; never hardcode |
| Does it involve a new transmission route? | Add to the explicit route constant map; do not rely on substring matching |
| Does it involve a new billing moment? | Add to `BILLING_MOMENTS` constant; update validator and runtime in same commit |
| Is it a new PrEP arm? | See the PrEP Product Checklist below |

### Step 2 — Check for existing duplicates

Before adding a new constant or mapping, search the codebase:

```bash
grep -rn "<concept_keyword>" --include="*.py" .
```

If the concept already exists under a different name somewhere, consolidate rather
than adding a third copy. The `_ARM_TO_PREP_STATE` / `_INTERVENTION_TO_PREP_STATE`
duplication is the cautionary example.

### Step 3 — Update the validator first

Edit `config_utils.py` to accept the new parameter value before writing any runtime
code. This creates a forcing function: the validator is now the specification.

- For a new billing moment: add to `BILLING_MOMENTS`.
- For a new distribution type: add to `VALID_HTA_DIST_TYPES` (lowercase).
- For a new config section: add a schema validation function following the existing
  pattern and call it from the top-level validator.
- For a modified key name: add the new name to the validator and mark the old name
  deprecated (emit a warning, do not silently accept both indefinitely).

### Step 4 — Add a schema entry to the parameter reference

Open `documentation/V11_Parameter_Reference_Table.docx` and add a row for the new
parameter with:

- Key path (e.g., `prep_schedules.oral_prep.initiation.schedule`)
- Type and expected format
- Units
- Valid range or accepted values
- Which module reads it
- Which module validates it
- Default behaviour if absent (must be "raises ConfigError" for scientifically
  critical parameters; never "silently uses placeholder")

### Step 5 — Write the runtime handler

Now write the code that reads and uses the parameter:

- Read from config via `config_reader.py` — do not read raw config dicts elsewhere.
- If the parameter drives array indexing, derive the index from `GroupSystem`.
- If the parameter drives a schedule, use `ScheduleManager` — do not reimplement
  year-keyed interpolation.
- Use the canonical name from Part 1 throughout; do not introduce a local alias.
- If you are adding a new billing moment handler in `adverse_events._compute_v2`,
  add it alongside the existing `"ongoing"` and `"initiation"` branches — do not
  add an else-clause that silently returns zero.

### Step 6 — Verify validator–runtime alignment

After writing the runtime handler, answer these questions and document the answers
in your commit message:

1. Every value the validator now accepts — does the runtime handle it?
2. Every value the runtime now handles — does the validator accept it?
3. Is there any value where the answer to 1 or 2 is "no"?

If the answer to 3 is yes, fix it before committing. This is the check that would
have caught the billing-moment silent zero bug and the PrEP override shape bug.

### Step 7 — Check for hardcoded duplicates

If you added or changed a constant:

```bash
grep -rn "<old_value_or_name>" --include="*.py" .
```

Find all existing references and update them to use the canonical source.
For PrEP product names, this means updating `PREP_ARM_TO_STATE`, not each module's
local copy.

### Step 8 — Test against a modified config

Run at least one scenario with a config that exercises the new or modified parameter.
Confirm the output changes in the expected direction. For cost parameters, verify
the affected cost column in the HTA output changes by approximately the right
magnitude.

### Step 9 — Update CHANGELOG.md

Add an entry under the current version:

```
### Changed / Added
- [module.py] Added `new_param_name`: <one sentence what it does and why>
  - Config path: `section.key`
  - Billing moment (if applicable): `"once"`
  - Validated by: `config_utils._validate_X`
  - Handled by: `adverse_events._compute_v2`
```

---

## PrEP Product Checklist

Adding a new PrEP product (e.g., a second injectable) requires updates in exactly
these locations and no others:

- [ ] `PREP_ARM_TO_STATE` in shared constants module — add the new arm → state mapping
- [ ] `config_utils.py` — add the new arm name to the accepted set for `prep_schedules`
- [ ] `schedule_utils.py` — if the product list is iterated, read from `PREP_ARM_TO_STATE` keys rather than a hardcoded list
- [ ] `scenario_comparator.py` — `ordered_arms` must include the new arm
- [ ] `hta_report.py` — `ARM_ORDER`, `ARM_LABELS`, `SCENARIO_LABELS` must include the new arm; comparator logic must be generalised if it currently assumes exactly three arms
- [ ] Config JSON — add a `prep_schedules.<new_arm>` block in the correct nested shape (see 2.3)
- [ ] `documentation/V11_Parameter_Reference_Table.docx` — add row for the new product

Do not add the product name as a string literal in any other module.
If you find yourself typing the product name in a new file, ask whether it should
instead be read from `PREP_ARM_TO_STATE`.

---

## Modifying an Existing Parameter

If you are changing the name, shape, or meaning of a parameter that already exists:

1. **Name change:** Add the new name to the validator and emit a `DeprecationWarning`
   when the old name is encountered. Do not silently accept both forever. Set a
   deadline (a version number) and remove the old name at that version.

2. **Shape change** (e.g., flat dict → nested dict): This is high-risk. The
   PrEP schedule shape bug (B1 in the audit) was a shape change that was applied
   in `scenario_executor.py` and `schedule_utils.py` but not in
   `run_scenarios._build_arm_config`. Always search for every site that constructs
   or reads the structure, not just the site you are currently editing.

3. **Meaning change** (e.g., a rate that was per-year is now per-quarter):
   This requires a calibration review. Document the change in CHANGELOG.md with
   the conversion factor so any stored results can be retrospectively interpreted.

4. **Removal:** If removing a parameter, first confirm it is not referenced by
   any live code path (not just the module you are editing). Then remove it from
   the validator, the runtime, and the parameter reference table in the same commit.
   Do not leave a validator that accepts a key the runtime ignores.

---

## Quick Reference Card

```
New parameter?
  → Add to validator first (config_utils.py)
  → Add to parameter reference table
  → Write runtime handler
  → Verify validator ↔ runtime alignment
  → Grep for hardcoded duplicates
  → Test with a config that exercises it
  → CHANGELOG entry

New PrEP product?
  → Work through the PrEP Product Checklist above

Changing a name?
  → DeprecationWarning on old name; remove at next version

Changing a shape?
  → Grep every construction and every read site; update all of them

Schedule block shape (always):
  { "schedule": { "YEAR": rate }, "distribution": { "type": "..." } }

Distribution type casing (always):
  lowercase — "beta", "gamma", "lognormal", "normal", "uniform", "fixed"

Billing moments (exhaustive list):
  "ongoing" | "initiation" | "once" | "diagnosis" | "death"

Arm names (exhaustive list as of v11):
  "no_prep" | "oral_prep" | "lenacapavir"

PrEP state labels (in population arrays):
  None | "Oral_PrEP" | "Lenacapavir"
```

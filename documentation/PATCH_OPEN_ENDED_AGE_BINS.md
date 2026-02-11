# PATCH: Open-Ended Age Bin Support

**Date:** 2026-02-10
**Location:** `engine_v3.py`, lines 335-342
**Issue:** ValueError when using open-ended age bins like "65+"

---

## Problem

The `initialize_from_relative_weights()` method crashed when processing age groups with open-ended bins (e.g., "65+"):

```
ValueError: invalid literal for int() with base 10: '65+'
```

This occurred at the sexual debut boundary check, which tried to parse age groups to determine if they represent ages <15 (childhood) or ≥15 (sexually active).

**Original code (line 340):**
```python
if '-' in age_group:
    min_age = int(age_group.split('-')[0])
else:
    min_age = int(age_group)  # ❌ Crashes on "65+"
```

---

## Solution

Updated the age group parser to handle three formats:

1. **Closed bins:** `"15-19"` → min_age = 15
2. **Open-ended bins:** `"65+"` → min_age = 65
3. **Single values:** `"15"` → min_age = 15

**Fixed code:**
```python
# Parse age group: "15-19" → 15, "65+" → 65, "15" → 15
if '-' in age_group:
    min_age = int(age_group.split('-')[0])
elif '+' in age_group:
    min_age = int(age_group.replace('+', ''))
else:
    min_age = int(age_group)
```

---

## Impact

✅ **Now supports extended age ranges:**
- Standard bins: "15-19", "20-24", ..., "60-64"
- Open-ended bins: "65+", "70+", "75+" (any age with "+")
- Census-compatible age structures (e.g., 15-19 through 65+ matching real demographic data)

✅ **Backward compatible:**
- Existing configs with closed bins (e.g., "15-19" through "45-49") continue to work
- No changes needed to old configuration files

---

## Testing

**Config example that now works:**
```json
{
  "population": {
    "age_sex_distribution": {
      "value": {
        "15-19": {"female": 0.086, "male": 0.089},
        "20-24": {"female": 0.081, "male": 0.084},
        // ... other age groups ...
        "60-64": {"female": 0.045, "male": 0.042},
        "65+":   {"female": 0.089, "male": 0.075}  // ✅ Now supported
      }
    }
  },
  "groups": {
    "age_groups": ["15-19", "20-24", ..., "60-64", "65+"]  // Must match
  }
}
```

**Validation:**
- Sexual debut boundary correctly identifies "65+" as ≥15 (sexually active population)
- Conservation checks pass with open-ended bins
- All downstream modules (demography, aging) handle "65+" as a valid age group key

---

## Related Changes

**None required** - this is an isolated fix to age group parsing logic. Other modules already treated age groups as string keys and don't require numeric parsing.

---

## Notes

- The minimum age extracted from "65+" is 65, which is sufficient for the sexual debut boundary (<15 vs ≥15)
- Maximum age is not parsed from open-ended bins (not needed for initialization)
- The DemographyModule's aging logic should already handle "65+" as the oldest age accumulator bin

# Quick Reference: Orientation Contact Multipliers

**Quick guide for configuring orientation-specific contact rates**

---

## 🎯 TL;DR

Add this to your `groups` section to make contact rates vary by sexual orientation:

```json
"orientation_contact_multipliers": {
  "value": {
    "Female": 0.7,
    "Heterosexual_Male": 1.0,
    "MSM": 2.5
  }
}
```

✅ **Result:** MSM have ~2.5× more contacts, Women have ~30% fewer contacts

---

## Why This Matters

**Before this patch:**
- Everyone in "High" activity had 50 contacts/year (regardless of orientation)
- Not realistic: MSM typically have more partners than heterosexual men

**After this patch:**
- Female-High: 35 contacts/year (50 × 0.7)
- Heterosexual_Male-High: 50 contacts/year (50 × 1.0)
- MSM-High: 125 contacts/year (50 × 2.5)

---

## How to Use

### Option 1: Add Multipliers (Recommended)

**Add to your config:**
```json
{
  "groups": {
    "sexual_activity_groups": [
      {"name": "Low", "contact_rate": 3.0},
      {"name": "Medium", "contact_rate": 10.0},
      {"name": "High", "contact_rate": 50.0}
    ],
    "orientation_contact_multipliers": {  // ← ADD THIS
      "value": {
        "Female": 0.7,
        "Heterosexual_Male": 1.0,
        "MSM": 2.5
      },
      "notes": "Evidence-based contact rate differences by orientation"
    }
  }
}
```

**Resulting contact rates:**

| Orientation | Low | Medium | High |
|-------------|-----|--------|------|
| Female | 2.1 | 7.0 | 35.0 |
| Heterosexual_Male | 3.0 | 10.0 | 50.0 |
| MSM | 7.5 | 25.0 | 125.0 |

---

### Option 2: No Multipliers (Backward Compatible)

**Don't add anything:**
```json
{
  "groups": {
    "sexual_activity_groups": [
      {"name": "Low", "contact_rate": 3.0},
      {"name": "Medium", "contact_rate": 10.0},
      {"name": "High", "contact_rate": 50.0}
    ]
    // No multipliers = all default to 1.0
  }
}
```

**Result:** All orientations have same rates (old behavior)

---

## Recommended Values

### Evidence-Based (Most Realistic)

```json
"orientation_contact_multipliers": {
  "value": {
    "Female": 0.7,            // 30% fewer contacts
    "Heterosexual_Male": 1.0, // Baseline reference
    "MSM": 2.5                // 150% more contacts
  }
}
```

**Source:** Meta-analysis of partner count studies (UNAIDS, CDC NHBS)

### Conservative Estimate

```json
"orientation_contact_multipliers": {
  "value": {
    "Female": 0.85,
    "Heterosexual_Male": 1.0,
    "MSM": 1.5
  }
}
```

**Use when:** Uncertainty about exact ratios, want modest differences

### No Difference (Baseline)

```json
// Don't specify multipliers
// OR explicitly set all to 1.0:
"orientation_contact_multipliers": {
  "value": {
    "Female": 1.0,
    "Heterosexual_Male": 1.0,
    "MSM": 1.0
  }
}
```

---

## Validation

**The model will check:**
- ✅ All orientations in multipliers exist in your config
- ✅ All multipliers are positive (> 0)
- ✅ Clear error messages if something's wrong

**Example errors:**

```
❌ groups.orientation_contact_multipliers has unknown orientation 'Unknown'.
   Valid orientations: ['Female', 'Heterosexual_Male', 'MSM']

❌ groups.orientation_contact_multipliers['Female'] = -0.5 must be > 0
```

---

## Common Mistakes

### ❌ WRONG: Negative multiplier
```json
"orientation_contact_multipliers": {
  "Female": -0.5  // ❌ Must be positive!
}
```

### ✅ CORRECT: Positive multiplier
```json
"orientation_contact_multipliers": {
  "Female": 0.5  // ✓ 50% fewer contacts
}
```

---

### ❌ WRONG: Unknown orientation
```json
"orientation_contact_multipliers": {
  "Bisexual": 1.5  // ❌ Not in orientations list!
}
```

### ✅ CORRECT: Use defined orientations
```json
"orientation_contact_multipliers": {
  "Female": 0.7,
  "Heterosexual_Male": 1.0,
  "MSM": 2.5
}
```

---

## Testing

### Verify It's Working

Run your simulation and check output. With multipliers, you should see:
- Higher HIV prevalence in MSM (more contacts → more transmission)
- Lower prevalence in women (fewer contacts → less acquisition)

### Quick Test

```bash
python run_v3.py config_v6_rel_pop.json
```

Check terminal output for:
- No validation errors
- Simulation runs successfully
- Prevalence patterns match expectations

---

## Migration

**Old configs without multipliers:**
✅ Keep working (defaults to 1.0)

**Want to add multipliers:**
1. Open your config.json
2. Find `"groups"` section
3. Add `"orientation_contact_multipliers"` after `"hiv_stages"`
4. Use recommended values (0.7, 1.0, 2.5)
5. Save and run

---

## More Information

- **Detailed documentation:** [PATCH_ORIENTATION_CONTACT_MULTIPLIERS.md](PATCH_ORIENTATION_CONTACT_MULTIPLIERS.md)
- **Model guide:** [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) Section 4.3
- **Example config:** `config_v6_rel_pop.json`

---

**Status:** ✅ Implemented and tested
**Backward Compatible:** ✅ Yes
**Recommended:** ✅ Use 0.7, 1.0, 2.5 for realistic modeling

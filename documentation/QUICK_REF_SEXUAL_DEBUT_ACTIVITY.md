# Quick Reference: Sexual Debut Activity Distribution

**Quick guide for configuring sexual debut activity distribution**

---

## 🎯 TL;DR

**Use the feature flag to control whether sexual debut uses the same activity distribution as population initialization:**

```json
"sexual_debut": {
  "use_population_activity_distribution": true   // ← Add this
}
```

✅ **`true`** = Consistent (recommended)
⚠️ **`false`** = Custom debut pattern (advanced)

---

## Option 1: Consistent (Recommended) ⭐

**Use the SAME activity distribution for both initialization and sexual debut**

```json
{
  "population": {
    "activity_distribution": {
      "value": {
        "Low": 0.60,
        "Medium": 0.30,
        "High": 0.10
      }
    }
  },
  "sexual_debut": {
    "use_population_activity_distribution": {
      "value": true
    }
    // No debut_activity_distribution needed!
  }
}
```

**Result:**
- ✅ No drift over time
- ✅ Simple and clear
- ✅ Single source of truth
- ✅ **Recommended for most users**

---

## Option 2: Custom Debut Pattern (Advanced)

**Use DIFFERENT activity distributions**

```json
{
  "population": {
    "activity_distribution": {
      "value": {
        "Low": 0.60,
        "Medium": 0.30,
        "High": 0.10
      },
      "notes": "Adult population distribution"
    }
  },
  "sexual_debut": {
    "use_population_activity_distribution": {
      "value": false
    },
    "debut_activity_distribution": {
      "value": {
        "Low": 0.50,
        "Medium": 0.40,
        "High": 0.10
      },
      "notes": "Youth debut with higher Medium activity"
    }
  }
}
```

**Result:**
- ⚠️ Population drifts from 60/30/10 → 50/40/10 over time
- 📊 Useful if modeling age-specific risk behavior
- 📝 **Document why they differ**

---

## Default Behavior

**If you don't specify the flag:**
```json
"sexual_debut": {
  // use_population_activity_distribution defaults to TRUE
}
```

✅ **Defaults to consistent mode** (no drift)

---

## When to Use Each

### Use Consistent (`true`) When:
- ✅ You want simple, interpretable model
- ✅ No evidence for age-specific patterns
- ✅ **Most users should use this**

### Use Custom (`false`) When:
- ⚠️ Evidence-based youth risk patterns
- ⚠️ Calibrating to age-stratified data
- ⚠️ **Advanced modeling only**

---

## Common Mistakes

### ❌ WRONG: Two different distributions without flag
```json
"population": {
  "activity_distribution": {"Low": 0.60, "Medium": 0.30, "High": 0.10}
},
"sexual_debut": {
  "debut_activity_distribution": {"Low": 0.55, "Medium": 0.35, "High": 0.10}
  // Missing flag! Will default to TRUE and ignore debut_activity_distribution
}
```

### ✅ CORRECT: Explicitly set flag
```json
"population": {
  "activity_distribution": {"Low": 0.60, "Medium": 0.30, "High": 0.10}
},
"sexual_debut": {
  "use_population_activity_distribution": false,  // ← Explicit
  "debut_activity_distribution": {"Low": 0.55, "Medium": 0.35, "High": 0.10}
}
```

---

## Migration Guide

**Old config:**
```json
"sexual_debut": {
  "debut_activity_distribution": {"Low": 0.60, "Medium": 0.30, "High": 0.10}
}
```

**Updated config (consistent mode):**
```json
"sexual_debut": {
  "use_population_activity_distribution": true
}
```

**Or (custom mode):**
```json
"sexual_debut": {
  "use_population_activity_distribution": false,
  "debut_activity_distribution": {"Low": 0.60, "Medium": 0.30, "High": 0.10}
}
```

---

## Quick Test

### Test Consistency

**Run your model for 40 years and check:**

```python
# At initialization
initial_low_pct = count_low / total_pop

# At year 40
final_low_pct = count_low / total_pop

# Should be equal if flag is TRUE
assert abs(initial_low_pct - final_low_pct) < 0.01  # Within 1%
```

If drifting significantly → check your flag setting!

---

**For details, see:** [FEATURE_SEXUAL_DEBUT_ACTIVITY_FLAG.md](FEATURE_SEXUAL_DEBUT_ACTIVITY_FLAG.md)

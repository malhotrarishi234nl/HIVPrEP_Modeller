# V5 Quick Start Guide

**Simple guide to using the new V5 features**

---

## What V5 Adds

V5 adds two main capabilities:

1. **Time-varying rates** - ART/PrEP coverage can change over time (e.g., ART starts at 10% in 1996, increases to 85% by 2020)
2. **Multiple scenarios** - Run different "what if" scenarios automatically (e.g., what if we had no PrEP? What if we scaled up faster?)

---

## How to Use It

### Option 1: Keep Using V4 (No Changes Needed)

Your existing configs still work exactly as before:
```bash
python main.py config.json
```

Nothing breaks. V5 features are optional.

---

### Option 2: Add Time-Varying Rates

**What it does:** Instead of ART initiation being 80% forever, you can make it start at 10% in 1996 and gradually increase to 85% by 2020.

**How to do it:** Add an `art_schedule` section to your config:

```json
{
  "art_schedule": {
    "interpolation": {"value": "stepwise"},
    "initiation": {
      "schedule": {
        "1996": 0.1,
        "2000": 0.3,
        "2010": 0.7,
        "2020": 0.85
      }
    }
  }
}
```

**What happens:**
- Before 1996: ART rate = 0 (unavailable)
- 1996-1999: ART rate = 0.1 (10%)
- 2000-2009: ART rate = 0.3 (30%)
- 2010-2019: ART rate = 0.7 (70%)
- 2020+: ART rate = 0.85 (85%)

The model automatically uses these rates at the right times.

**Same works for PrEP:**
```json
{
  "prep_schedules": {
    "interpolation": {"value": "stepwise"},
    "Oral_PrEP": {
      "initiation": {
        "schedule": {
          "2012": 0.01,
          "2015": 0.05,
          "2020": 0.10
        }
      }
    }
  }
}
```

---

### Option 3: Run Multiple Scenarios

**What it does:** Automatically run different "what if" scenarios and compare results.

**Example use cases:**
- "What if PrEP was never available vs scaled up?"
- "Compare 70% ART coverage vs 90% coverage"
- "Test different mixing patterns"

**How to do it:**

1. **Add scenarios to your config:**

```json
{
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {
        "name": "baseline",
        "description": "Current parameters",
        "overrides": {}
      },
      {
        "name": "no_prep",
        "description": "No PrEP available",
        "overrides": {
          "prep_force_disable": true
        }
      },
      {
        "name": "high_art",
        "description": "90% ART coverage",
        "overrides": {
          "disease": {
            "treatment": {
              "art_initiation_rate": {"value": 0.9}
            }
          }
        }
      }
    ]
  }
}
```

2. **Run all scenarios at once:**

```bash
python run_scenarios.py myconfig.json
```

3. **Check outputs:**

You'll get separate output files for each scenario:
```
outputs/
  baseline_baseline_run_001.csv       <- baseline scenario
  baseline_no_prep_run_001.csv        <- no PrEP scenario
  baseline_high_art_run_001.csv       <- high ART scenario
```

Each file has a `scenario` column so you know which is which:
```csv
scenario,step,year,new_infections,prevalence,...
baseline,0,1980.0,2,0.000978,...
baseline,1,1981.0,2,0.000964,...
```

---

## Real Example: Comparing PrEP Scenarios

Let's say you want to compare:
1. No PrEP
2. Oral PrEP scale-up
3. Lenacapavir scale-up

**Step 1:** Create config with scenarios:

```json
{
  "time": {
    "start_year": {"value": 1980},
    "end_year": {"value": 2030}
  },
  
  "prep_schedules": {
    "Oral_PrEP": {
      "initiation": {
        "schedule": {"2012": 0.01, "2020": 0.10, "2030": 0.15}
      }
    },
    "Lenacapavir": {
      "initiation": {
        "schedule": {"2025": 0.02, "2030": 0.08}
      }
    }
  },
  
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {
        "name": "no_prep",
        "description": "Counterfactual without PrEP",
        "overrides": {"prep_force_disable": true}
      },
      {
        "name": "oral_prep",
        "description": "Oral PrEP scale-up",
        "overrides": {}
      },
      {
        "name": "lenacapavir",
        "description": "Switch to lenacapavir",
        "overrides": {
          "prep_schedules": {
            "Oral_PrEP": {
              "initiation": {"schedule": {"2025": 0.0}}
            },
            "Lenacapavir": {
              "initiation": {"schedule": {"2025": 0.05, "2030": 0.12}}
            }
          }
        }
      }
    ]
  }
}
```

**Step 2:** Run it:
```bash
python run_scenarios.py prep_comparison.json
```

**Step 3:** Compare results:
- Each scenario gets its own output file
- Compare infections prevented, costs, etc.
- All scenarios ran with same random seed (reproducible)

---

## Validation

V5 includes automatic checks to catch mistakes:

```bash
python validation.py myconfig.json
```

This checks:
- ✅ Rates are between 0 and 1
- ✅ Years are in order
- ✅ PrEP is actually disabled in no-PrEP scenarios
- ✅ Freeze years are valid
- ⚠️ Warns if schedules extend past simulation end

---

## Key Points

1. **Backward compatible** - Old configs still work
2. **Optional features** - Use what you need, ignore the rest
3. **Config-driven** - No code changes needed
4. **Validated** - Catches errors before simulation runs
5. **Labeled outputs** - Easy to compare scenarios

---

## Common Patterns

### Pattern 1: Just add time-varying ART
```json
{
  "art_schedule": {
    "initiation": {"schedule": {"1996": 0.1, "2020": 0.85}}
  }
}
```
Run normally: `python main.py config.json`

### Pattern 2: Compare 2 scenarios
```json
{
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {"name": "baseline", "overrides": {}},
      {"name": "high_coverage", "overrides": {"disease": {...}}}
    ]
  }
}
```
Run: `python run_scenarios.py config.json`

### Pattern 3: Both together
```json
{
  "art_schedule": {...},
  "prep_schedules": {...},
  "scenarios": {...}
}
```
Scenarios can override schedule values!

---

## Troubleshooting

**"Missing time section"**
→ Config needs `time.start_year` and `time.end_year`

**"Rates outside [0,1]"**
→ Check your schedule values are between 0 and 1

**"Years not sorted"**
→ Schedule years should be in increasing order: 2000, 2010, 2020 (not 2020, 2000, 2010)

**"Scenario outputs not found"**
→ Make sure `output.enabled` is `true` in config

---

## More Help

- **Complete V5 docs:** `V5_PATCH_SUMMARY.md`
- **Example configs:** `config_v5_example.json`, `config_v5_test.json`
- **Detailed phase 4 info:** `PHASE_4_STATUS.md`
- **Run validation:** `python validation.py config.json`

---

**That's it! V5 makes it easy to model time-varying coverage and compare scenarios.**

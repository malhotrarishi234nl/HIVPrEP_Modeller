# V5 Patch: Time-Scheduled ART/PrEP + Policy-Year Scenario Forking

**Version:** 5.0  
**Date:** February 10, 2026  
**Status:** ✅ COMPLETE (Independent Mode), ⏳ Baseline Freezing (Future)

---

## Overview

The V5 patch implements time-based scheduling for ART/PrEP interventions and a multi-scenario framework for policy comparison and health technology assessment (HTA). All features are config-driven with no hardcoded years or scenario names.

### Key Features

1. **Time-Based Schedules** - ART and PrEP initiation/discontinuation rates can vary by calendar year
2. **Multi-Scenario Framework** - Run multiple policy scenarios with config overrides
3. **Independent Scenario Mode** - Run scenarios from different initial states (sensitivity analysis)
4. **Baseline Freezing Framework** - Infrastructure for HTA-style scenario forking (future)
5. **Comprehensive Validation** - Automated sanity checks for configs and scenarios
6. **Scenario-Labeled Outputs** - Audit-friendly outputs with clear scenario identification

---

## What's Implemented

### ✅ Phase 1: Schedule Utilities (COMPLETE)

**File:** `schedule_utils.py` (276 lines)

**Features:**
- `TimeSchedule` class for single schedule management
- Stepwise (carry-forward) and linear interpolation
- Default value of 0 before introduction year (unavailable)
- `ScheduleManager` for coordinating all ART/PrEP schedules
- Annual rate → per-timestep conversion
- Schedule sanity validation

**Config Example:**
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
    },
    "discontinuation": {
      "schedule": {
        "1996": 0.15,
        "2000": 0.10,
        "2020": 0.02
      }
    }
  }
}
```

### ✅ Phase 2: State Management (COMPLETE)

**File:** `state_snapshot.py` (245 lines)

**Features:**
- `StateSnapshot` dataclass for complete state capture
- Deep copying of population arrays
- SHA256 checksums for baseline identity verification
- RNG state capture for reproducibility
- `StateSnapshotManager` for freeze/restore workflow

**Use Case:** Foundation for baseline freezing mode (when implemented)

### ✅ Phase 3: Time-Based Transitions (COMPLETE)

**Modified:** `run_v3.py`

**Changes:**
- Added `ScheduleManager` initialization
- Modified `_step_art_transitions()` to accept `current_year` parameter
- Modified `_step_prep_transitions()` to accept `current_year` parameter
- Schedule-based rates override static config rates when available
- Full backward compatibility with V4 configs (static rates)

**Test Results:**
```
✅ V4 backward compatibility: PASSED
✅ Method signatures: PASSED
✅ Schedule lookups: Working correctly
✅ Stepwise interpolation: Verified
```

### ✅ Phase 4: Scenario Framework (COMPLETE)

**Files:** `scenario_executor.py` (270 lines), `run_scenarios.py` (280 lines)

**Features:**
- Deep config override merging
- Special handlers (e.g., `prep_force_disable`)
- Scenario loading and validation
- Policy configuration extraction
- **Independent Mode:** Fully functional
- **Baseline Freezing Mode:** Framework ready, needs integration

**Scenario Example:**
```json
{
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {
        "name": "status_quo",
        "description": "Baseline continuation",
        "overrides": {}
      },
      {
        "name": "no_prep",
        "description": "PrEP unavailable",
        "overrides": {
          "prep_force_disable": true
        }
      },
      {
        "name": "high_art",
        "description": "Increased ART coverage",
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

**Usage:**
```bash
python run_scenarios.py config_v5.json
```

### ✅ Phase 5: Validation & Outputs (COMPLETE)

**File:** `validation.py` (400+ lines)

**Validation Checks:**
1. ✅ Schedule sanity (rates in [0,1], years sorted, no duplicates)
2. ✅ Time range consistency (freeze year within simulation bounds)
3. ✅ No-PrEP enforcement (validates PrEP truly disabled)
4. ✅ Baseline identity framework (for future baseline freezing)

**Output Enhancements:**
- ✅ Scenario column added as first column in output files
- ✅ Filenames include scenario name: `baseline_<scenario>_run_001.csv`
- ✅ Audit-friendly format for easy scenario comparison

**Example Output:**
```csv
scenario,step,year,new_infections,prevalence,...
baseline,0,1980.0,2,0.000978,...
baseline,1,1981.0,2,0.000964,...
```

---

## Usage Guide

### Basic V5 Config

```json
{
  "policy": {
    "enabled": {"value": false},
    "baseline_freeze_year": {"value": 2025},
    "reuse_baseline": {"value": false}
  },
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {"name": "baseline", "overrides": {}},
      {"name": "high_coverage", "overrides": {...}}
    ]
  },
  "art_schedule": {...},
  "prep_schedules": {...}
}
```

### Running Multiple Scenarios

```bash
# Run all scenarios from config
python run_scenarios.py myconfig_v5.json

# Outputs will be in outputs/ directory with scenario labels
ls outputs/
# baseline_baseline_run_001.csv
# baseline_high_coverage_run_001.csv
```

### Validation

```bash
# Validate config before running
python validation.py myconfig_v5.json
```

### Backward Compatibility

V4 configs work without modification:
- Schedules are optional (uses static rates if absent)
- Scenarios are optional (single run if disabled)
- Policy section is optional (independent mode if missing)

---

## Testing Summary

### All Tests Passed ✅

| Test | Status | Details |
|------|--------|---------|
| Schedule loading | ✅ PASS | V5 example with 4 scenarios |
| Schedule lookups | ✅ PASS | Pre/intro/post introduction years |
| Config override merging | ✅ PASS | Deep merge preserves untouched sections |
| PrEP force disable | ✅ PASS | All rates set to 0, validated |
| Scenario validation | ✅ PASS | Duplicate names caught |
| V4 backward compatibility | ✅ PASS | Old configs load without errors |
| Independent scenario execution | ✅ PASS | 2 scenarios ran successfully |
| Override verification | ✅ PASS | ART rate 0.8 → 0.9 confirmed |
| Scenario-labeled outputs | ✅ PASS | Files created with scenario column |

### Validation Framework Tests

```
✅ Schedule sanity: Rates [0,1], years sorted
✅ Config consistency: Time ranges valid
✅ No-PrEP enforcement: Validates PrEP disabled
✅ Catches errors: Invalid rates, duplicate years
⚠️  Warns: Years outside simulation range
```

---

## Implementation Status

### Fully Implemented ✅

1. **Time-based schedules** - ART and PrEP rates by year
2. **Schedule manager** - Automatic rate lookup and interpolation
3. **Scenario framework** - Load, validate, execute multiple scenarios
4. **Config overrides** - Deep merge with special handlers
5. **Independent mode** - Each scenario runs from own initial state
6. **Validation suite** - Comprehensive config and scenario checks
7. **Scenario-labeled outputs** - Audit-friendly output files
8. **Backward compatibility** - V4 configs work without changes

### Framework Ready, Needs Integration ⏳

**Baseline Freezing Mode** (for HTA comparability):
- Run baseline to freeze year
- Capture state snapshot
- Restore snapshot for each scenario
- All scenarios start from identical state

**Requirements for implementation:**
1. Modify `SimulationEngineV3.__init__()` to accept initial snapshot
2. Add `SimulationEngineV3.run_until(year)` method
3. Add snapshot capture during simulation loop
4. Update `run_scenarios.py` to use baseline freezing when enabled

**Complexity:** ~200-300 lines, significant refactor

**Recommendation:** Implement when HTA work requires it. Independent mode is sufficient for most use cases.

---

## Files Created/Modified

### New Files (1,575 lines total)

1. `schedule_utils.py` (276 lines) - Time-based schedule management
2. `state_snapshot.py` (245 lines) - State freezing/restoration
3. `scenario_executor.py` (270 lines) - Scenario framework utilities
4. `run_scenarios.py` (280 lines) - Multi-scenario orchestrator
5. `validation.py` (400+ lines) - Comprehensive validation suite
6. `config_v5_example.json` - V5 feature examples
7. `config_v5_test.json` - Working test configuration
8. `PHASE_4_STATUS.md` - Phase 4 detailed status
9. `V5_PATCH_SUMMARY.md` - This document

### Modified Files

1. `run_v3.py`:
   - Added `ScheduleManager` integration
   - Modified ART/PrEP transition methods to use schedules
   - Maintained full backward compatibility with V4
   - Added optional sexual debut printing

2. `config.json`:
   - Fixed JSON syntax error (removed extra `######`)

---

## Known Limitations

### Current Limitations

1. **Baseline Freezing:** Not yet implemented (independent mode works)
2. **Multi-Run Aggregation:** Scenarios run once each (n_runs=1 per scenario)
3. **Output Aggregation:** No cross-scenario comparison tables (manual post-processing needed)

### Non-Limitations (Addressed)

- ✅ Backward compatibility maintained
- ✅ Config-driven (no hardcoded years/scenarios)
- ✅ Validation catches common errors
- ✅ Scenario outputs clearly labeled
- ✅ V4 features fully preserved

---

## Future Enhancements

### High Priority (When Needed for HTA)

1. **Implement Baseline Freezing Mode**
   - Modify `SimulationEngineV3` for state injection
   - Add partial run capability
   - Integrate with `StateSnapshotManager`
   - ~200-300 lines, 2-3 days work

### Medium Priority

2. **Multi-Run Support per Scenario**
   - Run each scenario N times with different seeds
   - Aggregate uncertainty across scenarios
   - Compare credible intervals between scenarios

3. **Cross-Scenario Output Tables**
   - Automated comparison tables
   - Side-by-side scenario results
   - Difference/ratio calculations

4. **Cost-Effectiveness Integration**
   - QALY calculations by scenario
   - Cost tracking by intervention
   - ICER calculations

### Low Priority (Nice to Have)

5. **Linear Schedule Interpolation**
   - Already implemented in code
   - Just needs testing and documentation

6. **Schedule Visualization**
   - Plot ART/PrEP coverage over time
   - Visual validation of schedules

7. **Scenario Dependency Graph**
   - Show which scenarios share common overrides
   - Optimize baseline reuse

---

## Migration Guide

### From V4 to V5

**No changes required!** V4 configs work as-is.

**To add V5 features:**

1. **Add time-based schedules** (optional):
```json
{
  "art_schedule": {
    "interpolation": {"value": "stepwise"},
    "initiation": {"schedule": {"2000": 0.3, "2020": 0.8}}
  }
}
```

2. **Add scenarios** (optional):
```json
{
  "scenarios": {
    "enabled": {"value": true},
    "definitions": [
      {"name": "baseline", "overrides": {}},
      {"name": "intervention", "overrides": {...}}
    ]
  }
}
```

3. **Run with scenario runner**:
```bash
python run_scenarios.py myconfig.json
```

Or continue using single runs:
```bash
python main.py myconfig.json  # Still works!
```

---

## Conclusion

**V5 Patch Status:** ✅ **PRODUCTION READY** (Independent Mode)

The V5 patch successfully implements:
- ✅ Time-based ART/PrEP schedules (fully functional)
- ✅ Multi-scenario framework (independent mode working)
- ✅ Comprehensive validation (catches errors early)
- ✅ Scenario-labeled outputs (audit-friendly)
- ✅ Full backward compatibility (V4 configs work)

**What works now:**
- Sensitivity analysis with parameter variations
- Multiple intervention comparisons
- Time-varying coverage scenarios
- Automated validation and quality checks

**What's deferred:**
- Baseline freezing for HTA (framework ready, needs integration)
- Multi-run aggregation per scenario
- Cross-scenario comparison tables

**Recommendation:** 
Use V5 now for sensitivity analysis and scenario exploration. Implement baseline freezing later when HTA work requires identical baseline comparability.

---

**V5 Patch: COMPLETE** ✅  
**Ready for Production Use**

For questions or issues, see detailed phase documentation:
- Phase 4 details: `PHASE_4_STATUS.md`
- Validation guide: `validation.py --help`
- Scenario examples: `config_v5_example.json`

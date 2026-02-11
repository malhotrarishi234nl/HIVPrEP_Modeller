# Documentation Index

Quick navigation guide for all model documentation.

---

## 📚 Start Here

### For New Users
1. **[README.md](../README.md)** - Project overview and quick links
2. **[README_MODEL_GUIDE.md](README_MODEL_GUIDE.md)** - Complete model documentation (1,100+ lines)
3. **[V5_QUICK_START.md](V5_QUICK_START.md)** - Hands-on tutorial

### For Existing Users Upgrading to V5
1. **[V5_PATCH_SUMMARY.md](V5_PATCH_SUMMARY.md)** - What's new in V5
2. **[V5_VALIDATION_COMPLETE.md](V5_VALIDATION_COMPLETE.md)** - V5 validation test results

---

## 📖 Core Documentation

### Complete Reference
- **[README_MODEL_GUIDE.md](README_MODEL_GUIDE.md)** ⭐ MAIN GUIDE
  - Part I: Core Model (sections 1-11)
  - Part II: V4 Enhancements (sections 12-15)
  - Part III: V5 Features (sections 16-18)
  - Part IV: Advanced Customizations (sections 19-22)
  - Part V: Appendices

---

## 🚀 Feature Guides

### V5 Features (Latest)
- **[V5_PATCH_SUMMARY.md](V5_PATCH_SUMMARY.md)** - V5 feature overview
- **[V5_QUICK_START.md](V5_QUICK_START.md)** - V5 tutorial with examples
- **[V5_VALIDATION_COMPLETE.md](V5_VALIDATION_COMPLETE.md)** - V5 validation testing

### V4 Features
- **[PATCH_V4.2_FLUSH_ON_AGING.md](PATCH_V4.2_FLUSH_ON_AGING.md)** - Flush-on-aging implementation
- **[PATCH_V4.3_GUARDRAILS.md](PATCH_V4.3_GUARDRAILS.md)** - V4 guardrails
- **[PATCH_SEXUAL_DEBUT_CONTINUOUS.md](PATCH_SEXUAL_DEBUT_CONTINUOUS.md)** - Sexual debut system

### Population Initialization
- **[POPULATION_INITIALIZATION_PATCH.md](POPULATION_INITIALIZATION_PATCH.md)** ⭐ CENSUS-BASED INIT
  - Direct counts vs. relative weights
  - Global MSM share methodology
  - Conservation checks
  - Configuration examples

### Extended Age Groups
- **[PATCH_OPEN_ENDED_AGE_BINS.md](PATCH_OPEN_ENDED_AGE_BINS.md)** - Support for "65+", "70+", etc.

---

## ✅ Validation & Quality Assurance

### Validation System
- **[VALIDATION_PATCHES.md](VALIDATION_PATCHES.md)** ⭐ VALIDATION OVERVIEW
  - Transmission sanity checks
  - Global population mass balance
  - ART mass balance

### Detailed Validation
- **[MASS_BALANCE_ASSERTION.md](MASS_BALANCE_ASSERTION.md)** - Mass balance details
- **[VALIDATION_TEST_RESULTS.md](VALIDATION_TEST_RESULTS.md)** - Test results

---

## 🔧 Technical Documentation

### Model Review & Status
- **[MODEL_REVIEW_REPORT.md](MODEL_REVIEW_REPORT.md)** - Pre-calibration comprehensive review
- **[PHASE_4_STATUS.md](PHASE_4_STATUS.md)** - Development history

### Bug Fixes & Patches
- **[OUTPUT_FEATURES_FIX.md](OUTPUT_FEATURES_FIX.md)** - Output system fixes
- **[PREP_GRANULARITY_FIX.md](PREP_GRANULARITY_FIX.md)** - PrEP granularity improvements

---

## 📋 Quick Reference by Use Case

### I want to...

#### **Run my first simulation**
→ Start: [V5_QUICK_START.md](V5_QUICK_START.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) sections 1-11

#### **Use census data for initialization**
→ Guide: [POPULATION_INITIALIZATION_PATCH.md](POPULATION_INITIALIZATION_PATCH.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) section 19

#### **Model time-varying ART scale-up**
→ Guide: [V5_PATCH_SUMMARY.md](V5_PATCH_SUMMARY.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) section 16

#### **Compare multiple policy scenarios**
→ Guide: [V5_QUICK_START.md](V5_QUICK_START.md) section on scenarios
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) sections 17-18

#### **Add age groups beyond 65**
→ Guide: [PATCH_OPEN_ENDED_AGE_BINS.md](PATCH_OPEN_ENDED_AGE_BINS.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) section 20

#### **Understand validation errors**
→ Guide: [VALIDATION_PATCHES.md](VALIDATION_PATCHES.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) sections 21-22

#### **Model sexual debut from childhood**
→ Guide: [PATCH_SEXUAL_DEBUT_CONTINUOUS.md](PATCH_SEXUAL_DEBUT_CONTINUOUS.md)
→ Reference: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) sections 13-14

#### **Review model before calibration**
→ Start: [MODEL_REVIEW_REPORT.md](MODEL_REVIEW_REPORT.md)
→ Checklist: [README_MODEL_GUIDE.md](README_MODEL_GUIDE.md) Appendix G

---

## 📊 Documentation by Topic

### Configuration
| Topic | Primary Doc | Reference |
|-------|-------------|-----------|
| Time system | V5_QUICK_START.md | Guide section 2 |
| Population init | POPULATION_INITIALIZATION_PATCH.md | Guide section 19 |
| Age groups | PATCH_OPEN_ENDED_AGE_BINS.md | Guide sections 4, 20 |
| ART schedules | V5_PATCH_SUMMARY.md | Guide section 16 |
| PrEP schedules | V5_PATCH_SUMMARY.md | Guide section 16 |
| Scenarios | V5_QUICK_START.md | Guide sections 17-18 |
| Demography | PATCH_SEXUAL_DEBUT_CONTINUOUS.md | Guide sections 10, 12-14 |

### Validation
| Topic | Primary Doc | Reference |
|-------|-------------|-----------|
| Overview | VALIDATION_PATCHES.md | Guide section 21 |
| Mass balance | MASS_BALANCE_ASSERTION.md | Guide section 21.2 |
| Transmission | VALIDATION_PATCHES.md | Guide section 21.1 |
| ART balance | VALIDATION_PATCHES.md | Guide section 21.3 |
| Conservation | POPULATION_INITIALIZATION_PATCH.md | Guide section 22 |
| Test results | V5_VALIDATION_COMPLETE.md | Guide Appendix G |

### Features
| Feature | Primary Doc | Reference |
|---------|-------------|-----------|
| V5 overview | V5_PATCH_SUMMARY.md | Guide Part III |
| V4 overview | PHASE_4_STATUS.md | Guide Part II |
| Sexual debut | PATCH_SEXUAL_DEBUT_CONTINUOUS.md | Guide section 13 |
| Childhood pool | PATCH_SEXUAL_DEBUT_CONTINUOUS.md | Guide section 14 |
| Flush-on-aging | PATCH_V4.2_FLUSH_ON_AGING.md | Guide section 15 |
| Time schedules | V5_PATCH_SUMMARY.md | Guide section 16 |
| Multi-scenario | V5_PATCH_SUMMARY.md | Guide section 17 |
| Policy analysis | V5_PATCH_SUMMARY.md | Guide section 18 |

---

## 📁 All Documentation Files

### Main Documentation (Read These)
```
✅ README.md                                  # Project overview
✅ README_MODEL_GUIDE.md                      # Complete reference (1,100+ lines)
✅ V5_QUICK_START.md                          # Tutorial
✅ V5_PATCH_SUMMARY.md                        # V5 features
✅ POPULATION_INITIALIZATION_PATCH.md         # Census-based init
✅ VALIDATION_PATCHES.md                      # Validation system
```

### Feature Documentation
```
📄 PATCH_OPEN_ENDED_AGE_BINS.md              # Extended age groups
📄 PATCH_SEXUAL_DEBUT_CONTINUOUS.md          # Sexual debut & childhood
📄 PATCH_V4.2_FLUSH_ON_AGING.md              # V4.2 flush-on-aging
📄 PATCH_V4.3_GUARDRAILS.md                  # V4.3 guardrails
```

### Validation & Testing
```
📊 V5_VALIDATION_COMPLETE.md                 # V5 test results
📊 VALIDATION_TEST_RESULTS.md                # Validation tests
📊 MASS_BALANCE_ASSERTION.md                 # Mass balance details
```

### Technical & Historical
```
🔧 MODEL_REVIEW_REPORT.md                    # Pre-calibration review
🔧 PHASE_4_STATUS.md                         # Development history
🔧 OUTPUT_FEATURES_FIX.md                    # Output fixes
🔧 PREP_GRANULARITY_FIX.md                   # PrEP granularity
```

### Meta
```
📋 DOCUMENTATION_INDEX.md                    # This file
```

---

## 🗺️ Documentation Map

```
HIV Model Documentation
│
├─ Entry Point
│  └─ README.md → Points to everything else
│
├─ Complete Reference
│  └─ README_MODEL_GUIDE.md → Everything in one place
│
├─ Getting Started
│  ├─ V5_QUICK_START.md → Hands-on tutorial
│  └─ V5_PATCH_SUMMARY.md → Feature overview
│
├─ Advanced Features
│  ├─ POPULATION_INITIALIZATION_PATCH.md → Census-based init
│  ├─ PATCH_OPEN_ENDED_AGE_BINS.md → Extended ages
│  ├─ PATCH_SEXUAL_DEBUT_CONTINUOUS.md → Sexual debut
│  └─ V4 Patches → Flush, guardrails
│
├─ Validation & QA
│  ├─ VALIDATION_PATCHES.md → System overview
│  ├─ V5_VALIDATION_COMPLETE.md → Test results
│  └─ MASS_BALANCE_ASSERTION.md → Details
│
└─ Technical & Historical
   ├─ MODEL_REVIEW_REPORT.md → Pre-calibration review
   ├─ PHASE_4_STATUS.md → Development history
   └─ Bug fixes & patches
```

---

## 📖 Reading Order Recommendations

### For First-Time Users (Academic Reviewers)
1. README.md - Overview
2. README_MODEL_GUIDE.md Part I (Core Model) - Understand basics
3. V5_QUICK_START.md - See it in action
4. README_MODEL_GUIDE.md Parts II-IV - Advanced features

### For Calibration Work
1. POPULATION_INITIALIZATION_PATCH.md - Census-based setup
2. V5_PATCH_SUMMARY.md - Time-varying interventions
3. VALIDATION_PATCHES.md - Ensure correctness
4. README_MODEL_GUIDE.md sections 16-18 - Policy scenarios

### For Model Validation / Review
1. MODEL_REVIEW_REPORT.md - Comprehensive pre-calibration review
2. VALIDATION_PATCHES.md - Validation system
3. V5_VALIDATION_COMPLETE.md - Test results
4. README_MODEL_GUIDE.md Appendix G - Validation checklist

### For Extending the Model
1. README_MODEL_GUIDE.md section 13 (Extensibility)
2. PHASE_4_STATUS.md - Development patterns
3. Relevant patch documentation for area of interest

---

## 🔍 Search Tips

**Looking for a specific feature?**
- Use your browser's search (Ctrl+F / Cmd+F) in README_MODEL_GUIDE.md
- Check the table of contents at the top

**Looking for configuration examples?**
- V5_QUICK_START.md has many examples
- README_MODEL_GUIDE.md has examples in every section
- Test config files (test_*.json) are documented examples

**Looking for error explanations?**
- VALIDATION_PATCHES.md has error message examples
- POPULATION_INITIALIZATION_PATCH.md section "Common Questions"

**Looking for version history?**
- README_MODEL_GUIDE.md Appendix H
- PHASE_4_STATUS.md for detailed development

---

## 📝 Documentation Maintenance

**Last Updated:** 2026-02-10

**Documentation Status:**
- ✅ Core model fully documented
- ✅ V4 features fully documented
- ✅ V5 features fully documented
- ✅ All validation patches documented
- ✅ All recent patches documented
- ✅ Configuration examples provided
- ✅ Validation checklists complete

**Coverage:** 100% of implemented features

---

## ❓ Still Can't Find What You Need?

1. **Start with README_MODEL_GUIDE.md** - It's comprehensive
2. **Check the table of contents** - Use Ctrl+F to search
3. **Look at test configs** - They demonstrate every feature
4. **Review error messages** - They're designed to be helpful

---

**Navigation:** [Back to README](../README.md) | [Model Guide](README_MODEL_GUIDE.md) | [Quick Start](V5_QUICK_START.md)

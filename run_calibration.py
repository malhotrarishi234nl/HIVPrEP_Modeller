"""
run_calibration.py — Demographic Calibration Runner

Standalone script that compares the model's demographic trajectory against
external reference data (HMD Netherlands population, migration reference data).

Produces: calibration_report_YYYYMMDD.xlsx  →  v11/outputs/

Five checks:
  1. Config Audit         — config year_specific vs migration reference
  2. Population Trajectory — model total pop vs HMD (1975–2020), tiered bands
  3. Age-Sex Structure    — Spearman ρ + chi-square at 1985, 1995, 2010
  4. Migration Coverage   — migration reference year coverage, gap flags
  5. Population Forecast  — model total pop vs CBS forecast 2026–2070, CI bands

Usage (standalone):
    python run_calibration.py
    python run_calibration.py --config path/to/config.json
    python run_calibration.py --output-dir path/to/outputs/

Called from run_v3.py when config contains:
    "calibration_report": {"value": true}

V12.5 — new module
"""

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from validation_data_loader import (
    load_hmd_population,
    load_wb_migration,
    load_cbs_forecasts,
    default_hmd_path,
    default_wb_path,
    default_cbs_path,
    _ADULT_BINS,
)
from calibration_output_writer import CalibrationReportWriter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Fallback window used only if config cannot be read
_DEFAULT_SIM_START = 1975
_DEFAULT_SIM_END   = 2020

# Forecast horizon for CBS population check (Check 5).
# The calibration engine is extended to this year so summary_df has post-2025
# rows for comparison against CBS projections — regardless of the config's
# sim_end. Check 2 (HMD trajectory) is unaffected: it emits N/A for years
# without HMD data. Hardcoded for now; see BACKLOG-24 for future flexibility.
_FORECAST_HORIZON = 2065

# The World Bank file currently carries 2025 NLD net migration as 104,681.
# For forward model calibration we anchor 2025 to the CBS population forecast
# central value: net migration incl. net administrative corrections = 100,815.
_MIGRATION_REFERENCE_OVERRIDES = {
    2025: 100_815.0,
}



# ─────────────────────────────────────────────────────────────────────────────
# Dynamic window helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_sim_window(config_path: str):
    """Read start_year and end_year from run_control in config. Falls back to defaults."""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        rc = cfg.get("run_control", {})
        def _ev(val, default):
            if isinstance(val, dict):
                return int(val.get("value", default))
            return int(val) if val is not None else default
        start = _ev(rc.get("start_year"), _DEFAULT_SIM_START)
        end   = _ev(rc.get("end_year"),   _DEFAULT_SIM_END)
        return start, end
    except Exception:
        return _DEFAULT_SIM_START, _DEFAULT_SIM_END


def _apply_migration_reference_overrides(wb_data: Dict[int, float]) -> Dict[int, float]:
    """Return calibration migration references with documented post-WB overrides."""
    ref_data = dict(wb_data)
    ref_data.update(_MIGRATION_REFERENCE_OVERRIDES)
    return ref_data


def _derive_reference_years(
    sim_start: int,
    sim_end: int,
    hmd_years: set,
    n_points: int = 3,
    min_warmup: int = 5,
) -> List[int]:
    """
    Derive evenly-spaced reference years within the simulation window.

    Picks n_points at the 25/50/75 quantiles of the window, snaps each
    to the nearest 5-year mark, ensures each falls within HMD data, and
    removes duplicates (can arise in short windows).
    """
    span = sim_end - sim_start
    fractions = [(i + 1) / (n_points + 1) for i in range(n_points)]
    candidates = []
    for frac in fractions:
        raw = sim_start + frac * span
        snapped = round(raw / 5) * 5
        snapped = max(snapped, sim_start + min_warmup)
        snapped = min(snapped, sim_end)
        # Find closest available HMD year within +/- 10 yr
        found = None
        for delta in [0, 5, -5, 10, -10]:
            alt = snapped + delta
            if alt in hmd_years and sim_start <= alt <= sim_end:
                found = alt
                break
        if found is not None:
            candidates.append(found)
    # Deduplicate while preserving order
    seen: set = set()
    result = []
    for yr in candidates:
        if yr not in seen:
            seen.add(yr)
            result.append(yr)
    return sorted(result)


# Age bin labels for the adult population tensor
# GroupSystem has 20 bins: 15-19 … 110+; bins 13+ all collapse to "80+"
_MODEL_ADULT_BIN_LABELS = (
    ["15-19", "20-24", "25-29", "30-34", "35-39",
     "40-44", "45-49", "50-54", "55-59", "60-64",
     "65-69", "70-74", "75-79"]
    + ["80+"] * 7   # tensor indices 13-19
)
# Unique labels in order for comparison (14 bins)
_COMPARE_BINS = _ADULT_BINS   # from validation_data_loader: 15-19 … 80+


# ─────────────────────────────────────────────────────────────────────────────
# Engine helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_engine_quietly(config_path: str, start_year: int, end_year: int, seed: int = 42):
    """
    Instantiate SimulationEngineV3 with start/end year override.
    Suppresses all stdout during init.
    """
    from run_v3 import SimulationEngineV3

    with open(config_path) as f:
        cfg = json.load(f)

    # Override simulation window via run_control (what ConfigReader actually reads)
    if "run_control" not in cfg:
        cfg["run_control"] = {}
    cfg["run_control"]["start_year"] = {"value": start_year, "notes": "Overridden by calibration runner"}
    cfg["run_control"]["end_year"]   = {"value": end_year,   "notes": "Overridden by calibration runner"}
    # Disable calibration_report flag to avoid recursion
    cfg.pop("calibration_report", None)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", dir="/tmp", delete=False
    ) as tf:
        json.dump(cfg, tf)
        tmp_path = tf.name

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        eng = SimulationEngineV3(tmp_path, seed=seed)

    os.unlink(tmp_path)
    return eng


def _run_engine_quietly(eng) -> "pd.DataFrame":
    """Run engine and return summary DataFrame, suppressing stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = eng.run(run_number=1)
    return result["summary"]


def _extract_age_sex_from_tensor(
    population: np.ndarray,
    orient_to_idx: dict,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Extract adult population by age bin (15+ only) and sex from the tensor.

    Returns:
        male_by_bin  : dict[bin_label -> count]
        female_by_bin: dict[bin_label -> count]
    """
    female_idx = orient_to_idx["Female"]
    male_idxs  = [orient_to_idx["Heterosexual_Male"], orient_to_idx["MSM"]]

    # Sum over all non-age/orient dimensions: (age, orient) from (age,orient,act,hiv,diag,art,prep)
    pop_2d = np.sum(population, axis=(2, 3, 4, 5, 6))  # shape (n_age, n_orient)

    female_by_age = pop_2d[:, female_idx]
    male_by_age   = np.sum(pop_2d[:, male_idxs], axis=1)

    # Collapse tensor indices to _COMPARE_BINS (14 unique bins)
    male_by_bin   = {b: 0 for b in _COMPARE_BINS}
    female_by_bin = {b: 0 for b in _COMPARE_BINS}

    for idx, label in enumerate(_MODEL_ADULT_BIN_LABELS):
        male_by_bin[label]   += int(male_by_age[idx])
        female_by_bin[label] += int(female_by_age[idx])

    return male_by_bin, female_by_bin


def _to_proportions(counts: Dict[str, int]) -> Dict[str, float]:
    """Convert counts dict to proportions (sum to 1.0)."""
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in counts}
    return {k: v / total for k, v in counts.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Check 1 — Config Audit
# ─────────────────────────────────────────────────────────────────────────────

def check_config_audit(
    config_path: str,
    wb_data: Dict[int, float],
    sim_start: Optional[int] = None,
    sim_end: Optional[int] = None,
) -> List[dict]:
    """
    Compare every config year_specific entry against the migration reference.
    Returns a list of dicts per year (sorted).

    Parameters
    ----------
    sim_start, sim_end : int, optional
        Simulation window bounds.  When supplied, each row receives an
        ``in_window`` flag so the report can distinguish pre/post-window
        config entries from in-window ones.
    """
    with open(config_path) as f:
        cfg = json.load(f)

    ys = cfg.get("demography", {}).get("migration", {}) \
           .get("quantity", {}).get("year_specific", {}).get("value", {})

    rows = []
    for yr_str, cfg_val in sorted(ys.items(), key=lambda x: int(x[0])):
        yr = int(yr_str)
        cfg_v = float(cfg_val)
        wb_v  = wb_data.get(yr)
        diff  = (cfg_v - wb_v) if wb_v is not None else None
        disc  = (abs(diff) > 1.0) if diff is not None else False

        in_win = True  # default: treated as in-window when bounds not supplied
        if sim_start is not None and sim_end is not None:
            in_win = sim_start <= yr <= sim_end

        rows.append({
            "year":         yr,
            "config_value": cfg_v,
            "wb_value":     wb_v,
            "diff":         diff,
            "discrepancy":  disc,
            "in_window":    in_win,
        })

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Check 2 — Population Trajectory
# ─────────────────────────────────────────────────────────────────────────────

def check_population_trajectory(
    summary_df,
    hmd_data: Dict[int, dict],
) -> List[dict]:
    """
    Compare modeled total population per year to HMD.
    Uses the last step of each calendar year from the summary DataFrame.
    """
    import pandas as pd

    df = summary_df.copy()
    df["year_int"] = df["year"].apply(lambda y: int(np.floor(y + 1e-6)))

    # Last step per calendar year
    last_per_year = df.groupby("year_int").last().reset_index()

    rows = []
    for _, row in last_per_year.iterrows():
        yr = int(row["year_int"])
        model_total = int(row["total_population"])
        if yr not in hmd_data:
            # No HMD reference for this year — emit N/A row
            rows.append({
                "year":        yr,
                "hmd_total":   None,
                "model_total": model_total,
                "pct_dev":     None,
            })
            continue
        hmd_total = hmd_data[yr]["total"]
        pct_dev   = (model_total - hmd_total) / hmd_total * 100.0
        rows.append({
            "year":        yr,
            "hmd_total":   hmd_total,
            "model_total": model_total,
            "pct_dev":     pct_dev,
        })

    return sorted(rows, key=lambda x: x["year"])


# ─────────────────────────────────────────────────────────────────────────────
# Check 3 — Age-Sex Structure
# ─────────────────────────────────────────────────────────────────────────────

def check_age_structure(
    config_path: str,
    hmd_data: Dict[int, dict],
    reference_years: List[int],
    sim_start: int = _DEFAULT_SIM_START,
) -> Dict[int, dict]:
    """
    For each reference year, run a short simulation ending at that year,
    extract the age-sex distribution, and compare to HMD via Spearman + chi-square.
    """
    from scipy.stats import spearmanr, chisquare

    results = {}

    for ref_year in reference_years:
        if ref_year not in hmd_data:
            logger.warning("HMD data missing for reference year %d — skipping", ref_year)
            continue

        eng = _build_engine_quietly(config_path, sim_start, ref_year)
        _run_engine_quietly(eng)

        male_by_bin, female_by_bin = _extract_age_sex_from_tensor(
            eng.state.population, eng.groups.orient_to_idx
        )

        model_male_props   = _to_proportions(male_by_bin)
        model_female_props = _to_proportions(female_by_bin)

        hmd_male_props   = _to_proportions({
            b: hmd_data[ref_year]["by_age_bin"].get(b, {}).get("male", 0)
            for b in _COMPARE_BINS
        })
        hmd_female_props = _to_proportions({
            b: hmd_data[ref_year]["by_age_bin"].get(b, {}).get("female", 0)
            for b in _COMPARE_BINS
        })

        # Vectors in bin order
        bins = _COMPARE_BINS
        mdl_m_vec = np.array([model_male_props[b]   for b in bins])
        hmd_m_vec = np.array([hmd_male_props[b]     for b in bins])
        mdl_f_vec = np.array([model_female_props[b] for b in bins])
        hmd_f_vec = np.array([hmd_female_props[b]   for b in bins])

        # Spearman rank correlation (shape test)
        rho_m, _ = spearmanr(mdl_m_vec, hmd_m_vec)
        rho_f, _ = spearmanr(mdl_f_vec, hmd_f_vec)

        # Chi-square goodness-of-fit (distributional test)
        # Scale observed to have same total as expected, avoid zero cells
        def _safe_chisq(observed_props, expected_props, total=10_000):
            obs = np.maximum(observed_props * total, 0.5)
            exp = np.maximum(expected_props * total, 0.5)
            # normalise
            obs = obs / obs.sum() * total
            exp = exp / exp.sum() * total
            stat, pval = chisquare(obs, f_exp=exp)
            return float(stat), float(pval)

        chi2_m, pval_m = _safe_chisq(mdl_m_vec, hmd_m_vec)
        chi2_f, pval_f = _safe_chisq(mdl_f_vec, hmd_f_vec)

        results[ref_year] = {
            "bins":              bins,
            "spearman_male":     float(rho_m),
            "spearman_female":   float(rho_f),
            "chi2_male":         chi2_m,
            "chi2_pval_male":    pval_m,
            "chi2_female":       chi2_f,
            "chi2_pval_female":  pval_f,
            "model_male_props":  model_male_props,
            "model_female_props":model_female_props,
            "hmd_male_props":    hmd_male_props,
            "hmd_female_props":  hmd_female_props,
        }

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Check 4 — Migration Coverage
# ─────────────────────────────────────────────────────────────────────────────

def check_migration_coverage(
    config_path: str,
    wb_data: Dict[int, float],
    sim_start: int = _DEFAULT_SIM_START,
    sim_end: int   = _DEFAULT_SIM_END,
) -> List[dict]:
    """
    Full migration-reference year coverage table with in-config and in-window flags.
    """
    with open(config_path) as f:
        cfg = json.load(f)

    ys = cfg.get("demography", {}).get("migration", {}) \
           .get("quantity", {}).get("year_specific", {}).get("value", {})
    config_years = {int(k) for k in ys.keys()}

    rows = []
    for yr in sorted(wb_data.keys()):
        wb_val   = wb_data[yr]
        in_cfg   = yr in config_years
        in_win   = sim_start <= yr <= sim_end
        abs_val  = abs(wb_val)

        flag = ""
        note = ""
        if yr > sim_end:
            # Post-window years are beyond the model scope — informational regardless of config
            note = f"Post-window — beyond simulation end year ({sim_end}); not used in run"
        elif not in_cfg:
            if in_win and abs_val > 5_000:
                flag = "WITHIN-WINDOW GAP"
                note = f"WB |net|={abs_val:,.0f} > 5,000 — missing from config"
            elif yr < sim_start and abs_val > 500:
                flag = "PRE-WINDOW"
                note = f"Informational — before simulation window ({sim_start}–{sim_end})"

        rows.append({
            "year":      yr,
            "wb_value":  wb_val,
            "in_config": in_cfg,
            "in_window": in_win,
            "flag":      flag,
            "note":      note,
        })

    return rows



# ─────────────────────────────────────────────────────────────────────────────
# Check 5 — Population Forecast vs CBS
# ─────────────────────────────────────────────────────────────────────────────

def check_pop_forecast(
    summary_df,
    cbs_data: dict,
) -> list:
    """
    Compare modelled forward population (years >= 2026) against CBS central
    forecast and confidence intervals.

    Converts model total population to millions for comparison with CBS figures.
    Only years present in both summary_df and cbs_data are included.

    Parameters
    ----------
    summary_df : pd.DataFrame
        Full-run summary DataFrame from the calibration simulation.
    cbs_data : dict
        Output of load_cbs_forecasts() — keyed by year (int), units in persons.

    Returns
    -------
    List of dicts, one per overlapping year, sorted ascending:
        {
          "year":                   int,
          "model_total":            int,       # raw model persons
          "model_millions":         float,     # model total / 1e6
          "cbs_central_millions":   float,
          "ci67_lo_millions":       float or None,
          "ci67_hi_millions":       float or None,
          "ci95_lo_millions":       float or None,
          "ci95_hi_millions":       float or None,
          "within_67":              bool,
          "within_95":              bool,
          "deviation_from_central": float,     # % deviation from CBS central
        }
    Returns [] if no overlapping years or cbs_data is empty.
    """
    if not cbs_data:
        return []

    df = summary_df.copy()
    df["year_int"] = df["year"].apply(lambda y: int(np.floor(y + 1e-6)))
    last_per_year = df.groupby("year_int").last().reset_index()

    rows = []
    for _, row in last_per_year.iterrows():
        yr = int(row["year_int"])
        if yr < 2026 or yr not in cbs_data:
            continue

        model_total = int(row["total_population"])
        model_m     = model_total / 1_000_000.0

        cbs = cbs_data[yr]
        central_m = cbs["central"] / 1_000_000.0

        def _m(v):
            return v / 1_000_000.0 if v is not None else None

        ci67_lo = _m(cbs["ci67_lo"])
        ci67_hi = _m(cbs["ci67_hi"])
        ci95_lo = _m(cbs["ci95_lo"])
        ci95_hi = _m(cbs["ci95_hi"])

        within_67 = (
            ci67_lo is not None and ci67_hi is not None
            and ci67_lo <= model_m <= ci67_hi
        )
        within_95 = (
            ci95_lo is not None and ci95_hi is not None
            and ci95_lo <= model_m <= ci95_hi
        )
        dev_pct = (model_total - cbs["central"]) / cbs["central"] * 100.0

        rows.append({
            "year":                   yr,
            "model_total":            model_total,
            "model_millions":         round(model_m, 3),
            "cbs_central_millions":   round(central_m, 3),
            "ci67_lo_millions":       round(ci67_lo, 3) if ci67_lo is not None else None,
            "ci67_hi_millions":       round(ci67_hi, 3) if ci67_hi is not None else None,
            "ci95_lo_millions":       round(ci95_lo, 3) if ci95_lo is not None else None,
            "ci95_hi_millions":       round(ci95_hi, 3) if ci95_hi is not None else None,
            "within_67":              within_67,
            "within_95":              within_95,
            "deviation_from_central": round(dev_pct, 2),
        })

    return sorted(rows, key=lambda x: x["year"])


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_all_checks(
    config_path: str,
    hmd_path: str,
    wb_path: str,
    output_dir: str,
    cbs_path: Optional[str] = None,
) -> dict:
    """
    Run all five calibration checks and produce the Excel report.
    Returns the results dict.
    """
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Use full sim window from config — years beyond HMD coverage appear as N/A in output
    sim_start, sim_end = _read_sim_window(config_path)
    sim_window_label   = f"{sim_start}\u2013{sim_end}"

    print("\n" + "=" * 65)
    print("DEMOGRAPHIC CALIBRATION RUNNER  (V12.5)")
    print("=" * 65)
    # Resolve CBS path
    if cbs_path is None:
        v11_root_for_cbs = os.path.dirname(config_path)
        cbs_path = default_cbs_path(v11_root_for_cbs)

    print(f"  Config:  {config_path}")
    print(f"  HMD:     {hmd_path}")
    print(f"  WB:      {wb_path}")
    print(f"  CBS:     {cbs_path}")
    print(f"  Window:  {sim_window_label}")
    print("=" * 65)

    # Load reference data — HMD loaded without year filter; N/A shown for years with no data
    print("\n[1/6] Loading reference data...")
    hmd_data = load_hmd_population(hmd_path)
    wb_raw   = load_wb_migration(wb_path)
    wb_data  = _apply_migration_reference_overrides(wb_raw)
    print(f"  HMD: {len(hmd_data)} years loaded ({min(hmd_data)}–{max(hmd_data)})")
    print(f"  WB:  {len(wb_raw)} years loaded ({min(wb_raw)}–{max(wb_raw)})")
    if _MIGRATION_REFERENCE_OVERRIDES:
        override_txt = ", ".join(
            f"{yr}={val:,.0f}" for yr, val in sorted(_MIGRATION_REFERENCE_OVERRIDES.items())
        )
        print(f"  Migration reference overrides: {override_txt} (CBS forecast anchor)")

    # Derive reference years from actual sim window
    reference_years = _derive_reference_years(sim_start, sim_end, set(hmd_data.keys()))
    print(f"  Reference years derived: {reference_years}")

    # Check 1 — Config Audit
    print("\n[2/6] Check 1 — Config Audit...")
    audit_rows = check_config_audit(config_path, wb_data, sim_start, sim_end)
    discrepancies = [r for r in audit_rows if r["discrepancy"]]
    print(f"  Config entries compared: {len(audit_rows)}")
    print(f"  Discrepancies found:     {len(discrepancies)}"
          + (" ← REVIEW" if discrepancies else "  ✓"))

    # Check 2 — Population Trajectory + Check 5 source run
    # Engine is extended to _FORECAST_HORIZON so summary_df covers 2026+
    # for the CBS comparison (Check 5). Check 2 is unaffected — it only
    # uses years where HMD reference data exists.
    engine_end = max(sim_end, _FORECAST_HORIZON)
    print(f"\n[3/6] Check 2 — Population Trajectory ({sim_window_label})...")
    print(f"  Running simulation to {engine_end} "
          f"(config window {sim_window_label} + CBS forecast extension)...")
    eng_full = _build_engine_quietly(config_path, sim_start, engine_end)
    summary_df = _run_engine_quietly(eng_full)
    traj_rows = check_population_trajectory(summary_df, hmd_data)
    _valid_traj = [r for r in traj_rows if r["pct_dev"] is not None]
    mape = sum(abs(r["pct_dev"]) for r in _valid_traj) / len(_valid_traj) if _valid_traj else 0
    red_count = sum(1 for r in _valid_traj if abs(r["pct_dev"]) > 10)
    print(f"  Years compared: {len(traj_rows)}  |  MAPE: {mape:.2f}%"
          + (f"  |  RED years: {red_count}" if red_count else "  ✓"))

    # Check 3 — Age-Sex Structure
    print(f"\n[4/6] Check 3 — Age-Sex Structure at {reference_years}...")
    age_results = check_age_structure(config_path, hmd_data, reference_years, sim_start)
    for yr, data in sorted(age_results.items()):
        rho_m = data["spearman_male"]
        rho_f = data["spearman_female"]
        status = "✓" if rho_m >= 0.80 and rho_f >= 0.80 else "← REVIEW"
        print(f"  {yr}: male ρ={rho_m:.4f}  female ρ={rho_f:.4f}  {status}")

    # Check 4 — Migration Coverage
    print("\n[5/6] Check 4 — Migration Coverage...")
    cov_rows = check_migration_coverage(config_path, wb_data, sim_start, sim_end)
    within_gaps = [r for r in cov_rows if r["flag"] == "WITHIN-WINDOW GAP"]
    pre_info    = [r for r in cov_rows if r["flag"] == "PRE-WINDOW"]
    print(f"  Within-window gaps: {len(within_gaps)}"
          + (" ← REVIEW" if within_gaps else "  ✓"))
    print(f"  Pre-window informational: {len(pre_info)}")

    # Check 5 — Population Forecast vs CBS
    print("\n[6/6] Check 5 — Population Forecast vs CBS...")
    cbs_data: dict = {}
    try:
        cbs_data = load_cbs_forecasts(cbs_path)
        print(f"  CBS: {len(cbs_data)} forecast years loaded ({min(cbs_data)}–{max(cbs_data)})")
    except FileNotFoundError:
        warnings.warn(
            f"CBS forecast file not found at '{cbs_path}' — Check 5 skipped.",
            UserWarning,
            stacklevel=2,
        )
        print(f"  CBS file not found — Check 5 skipped  ← REVIEW")
    except Exception as exc:
        warnings.warn(
            f"CBS forecast load failed: {exc} — Check 5 skipped.",
            UserWarning,
            stacklevel=2,
        )
        print(f"  CBS load error — Check 5 skipped  ← REVIEW")

    forecast_rows = check_pop_forecast(summary_df, cbs_data)
    within_95_count = sum(1 for r in forecast_rows if r["within_95"])
    total_forecast  = len(forecast_rows)
    if total_forecast > 0:
        pct_within_95 = within_95_count / total_forecast * 100
        print(f"  Forecast years compared: {total_forecast}  |  "
              f"Within CBS 95% CI: {within_95_count}/{total_forecast} "
              f"({pct_within_95:.0f}%)"
              + ("  ✓" if pct_within_95 >= 80 else "  ← REVIEW"))
    else:
        print(f"  No forecast years overlap with CBS data (sim ends before 2026)")

    results = {
        "meta": {
            "run_date":    run_date,
            "config_path": config_path,
            "hmd_path":    hmd_path,
            "wb_path":     wb_path,
            "cbs_path":    cbs_path,
            "sim_window":  sim_window_label,
            "sim_start":   sim_start,
            "sim_end":     sim_end,
        },
        "config_audit":       audit_rows,
        "pop_trajectory":     traj_rows,
        "age_structure":      age_results,
        "migration_coverage": cov_rows,
        "pop_forecast":       forecast_rows,
        "summary_df":         summary_df,
    }

    # Write report
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"calibration_report_{date_str}.xlsx")
    print(f"\nWriting report → {report_path}")
    writer = CalibrationReportWriter(results, report_path)
    writer.write()

    print("\n" + "=" * 65)
    print(f"  MAPE: {mape:.2f}%   |   Config discrepancies: {len(discrepancies)}"
          f"   |   Within-window gaps: {len(within_gaps)}")
    print(f"  Report: {report_path}")
    print("=" * 65 + "\n")

    return results


def run_calibration_report(config_path: str, output_dir: str):
    """
    Entry point called from run_v3.py post-run hook.
    Catches all exceptions to avoid crashing the main run.
    """
    try:
        v11_root = os.path.dirname(config_path)
        run_all_checks(
            config_path=config_path,
            hmd_path=default_hmd_path(v11_root),
            wb_path=default_wb_path(v11_root),
            output_dir=output_dir,
        )
    except FileNotFoundError as e:
        warnings.warn(
            f"Calibration report skipped — reference file not found: {e}",
            UserWarning,
            stacklevel=2,
        )
    except Exception as e:
        warnings.warn(
            f"Calibration report failed (non-fatal): {e}",
            UserWarning,
            stacklevel=2,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Demographic Calibration Runner — V12.5"
    )
    p.add_argument(
        "--config",
        default=os.path.join(ROOT, "v11_configurationMAIN.json"),
        help="Path to simulation config JSON (default: v11_configurationMAIN.json)",
    )
    p.add_argument(
        "--output-dir",
        default=os.path.join(ROOT, "outputs"),
        help="Directory for the calibration report (default: v11/outputs/)",
    )
    p.add_argument(
        "--hmd",
        default=None,
        help="Override HMD file path (default: Validation /HMD_NL_Pop.txt)",
    )
    p.add_argument(
        "--wb",
        default=None,
        help="Override WB migration file path (default: Validation /Migration_Net.csv)",
    )
    p.add_argument(
        "--cbs",
        default=None,
        help="Override CBS forecast file path (default: Validation /NL_Population_Forecasts.csv)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    v11_root = os.path.dirname(os.path.abspath(args.config))
    run_all_checks(
        config_path=os.path.abspath(args.config),
        hmd_path=args.hmd or default_hmd_path(v11_root),
        wb_path=args.wb  or default_wb_path(v11_root),
        cbs_path=args.cbs or default_cbs_path(v11_root),
        output_dir=args.output_dir,
    )

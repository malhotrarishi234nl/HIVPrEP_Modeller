"""Passive AIDS mechanism diagnostics, NOT validated surveillance targets.

Progression while undiagnosed is not an observed AIDS diagnosis. Diagnosis in
the AIDS compartment includes existing AIDS, not only same-step progression.
These counters do not establish reporting delays, clinical case definitions,
or whether an imported case was previously reported abroad.
"""
import numpy as np

MECHANISMS = (
    "aids_progression_while_undiagnosed",
    "aids_progression_while_diagnosed",
    "hiv_diagnoses_in_aids_stage",
)
DIAGNOSIS_STAGE_MECHANISMS = (
    "hiv_diagnoses_in_acute_stage",
    "hiv_diagnoses_in_chronic_stage",
    "hiv_diagnoses_in_aids_stage",
)
#: 2026-09-16 decomposition: diagnosed-stage progression split by ART status, with
#: the Chronic/diagnosed person-time at risk as denominators. Passive, like the rest.
ART_SPLIT_EVENTS = (
    "aids_progression_while_diagnosed_off_art",
    "aids_progression_while_diagnosed_on_art",
)
ART_SPLIT_PERSON_YEARS = (
    "chronic_diagnosed_off_art_person_years",
    "chronic_diagnosed_on_art_person_years",
)
AIDS_OBSERVATION_FIELDS = tuple(dict.fromkeys(
    mechanism + suffix
    for mechanism in MECHANISMS + DIAGNOSIS_STAGE_MECHANISMS +
    ART_SPLIT_EVENTS + ART_SPLIT_PERSON_YEARS
    for suffix in ("", "_msm", "_non_msm")
))


def _group_counts(events, name, msm_index):
    """Events have age, orientation, then remaining axes; never mutate them."""
    total = int(np.sum(events))
    msm = int(np.sum(events[:, msm_index])) if msm_index is not None else 0
    return {name: total, name + "_msm": msm, name + "_non_msm": total - msm}


def progression_observation_counts(off_art, on_art, msm_index, *,
                                   pool_off_art_after=None, pool_on_art_after=None,
                                   years_per_step=None):
    """Input axes: age, orientation, activity, diagnosis, PrEP.

    ``pool_*_after`` are the Chronic pools AFTER the progression draw was removed;
    the pre-draw population at risk is exactly ``pool_after + draw``.  Person-time is
    that at-risk stock times ``years_per_step`` (a step-start approximation, stated
    as such). Omitting the pools yields NaN person-time, never zero.
    """
    result = {}
    for diagnosis, name in enumerate(MECHANISMS[:2]):
        # Sum only recorded draws, without a new draw or population change.
        for key, value in _group_counts(off_art[:, :, :, diagnosis, :], name, msm_index).items():
            result[key] = value
        for key, value in _group_counts(on_art[:, :, :, diagnosis, :], name, msm_index).items():
            result[key] += value
    diagnosed = 1
    result.update(_group_counts(off_art[:, :, :, diagnosed, :], ART_SPLIT_EVENTS[0], msm_index))
    result.update(_group_counts(on_art[:, :, :, diagnosed, :], ART_SPLIT_EVENTS[1], msm_index))
    for draw, pool, name in ((off_art, pool_off_art_after, ART_SPLIT_PERSON_YEARS[0]),
                             (on_art, pool_on_art_after, ART_SPLIT_PERSON_YEARS[1])):
        if pool is None or years_per_step is None:
            result.update({name + s: np.nan for s in ("", "_msm", "_non_msm")})
            continue
        at_risk = np.asarray(pool)[:, :, :, diagnosed, :] + draw[:, :, :, diagnosed, :]
        counts = _group_counts(at_risk, name, msm_index)
        result.update({key: float(value) * float(years_per_step) for key, value in counts.items()})
    return result


def diagnosis_observation_counts(diagnosed, aids_stage_index, msm_index):
    """Diagnosis draw axes: age, orientation, activity, HIV excluding susceptible, ART, PrEP."""
    stage_indices = (0, 1, aids_stage_index - 1)
    if diagnosed.shape[3] <= max(stage_indices):
        raise ValueError("diagnosis draw lacks Acute, Chronic or AIDS stage axis")
    result = {}
    for stage_index, name in zip(stage_indices, DIAGNOSIS_STAGE_MECHANISMS):
        result.update(_group_counts(
            diagnosed[:, :, :, stage_index, :, :], name, msm_index,
        ))
    return result


# ---------------------------------------------------------------------------
# Observed-AIDS PROXY (approved for the 2026-09-16 MSM diagnostic wave only).
#
# ASSUMPTION, not a validated observation function: an RIVM "AIDS diagnosis by
# year of AIDS diagnosis" is approximated by a first AIDS recognition among
# people known to HIV care in the Netherlands, i.e.
#   (a) Chronic-to-AIDS progression while HIV-diagnosed, plus
#   (b) endogenous HIV diagnosis while already in the AIDS compartment.
# Progression while undiagnosed is excluded because that person is counted at
# (b) when diagnosed (adding it would double count). The model AIDS compartment
# is clinical AIDS: chronic_to_aids is sourced to CASCADE (2000; 2003), whose
# endpoint is the CLINICAL part of the 1993 CDC definition and explicitly
# excludes CD4 < 200 without an AIDS-defining illness. No clinical-recognition
# fraction is therefore applied. Not modelled: reporting delay, notification
# completeness, clinical AIDS at C-1 non-MSM entry (all enter Chronic, so the
# non-MSM proxy is structurally one-sided), migrants already reported abroad.
# ---------------------------------------------------------------------------
OBSERVED_AIDS_PROXY = "aids_first_diagnosis_proxy"
OBSERVED_AIDS_PROXY_COMPONENTS = (
    "aids_progression_while_diagnosed",
    "hiv_diagnoses_in_aids_stage",
)
OBSERVED_AIDS_PROXY_FIELDS = tuple(
    OBSERVED_AIDS_PROXY + suffix for suffix in ("", "_msm", "_non_msm")
)


def add_observed_aids_proxy(outputs):
    """Add annual proxy series to an extract_annual_outputs dict, in place-free form.

    A year is emitted only when every component is present and finite for that
    year; a missing component is never treated as zero. Returns a new dict.
    """
    result = dict(outputs)
    for suffix in ("", "_msm", "_non_msm"):
        component_series = [outputs.get(name + suffix) for name in OBSERVED_AIDS_PROXY_COMPONENTS]
        if any(series is None for series in component_series):
            continue
        years = set(component_series[0])
        for series in component_series[1:]:
            years &= set(series)
        proxy = {}
        for year in sorted(years):
            values = [float(series[year]) for series in component_series]
            if all(np.isfinite(value) for value in values):
                proxy[year] = float(sum(values))
        result[OBSERVED_AIDS_PROXY + suffix] = proxy
    return result

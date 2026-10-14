"""Experimental contact-schedule adapter; production simulator is unchanged.

Schedules scale the symmetric MSM-MSM contact block. They do not prescribe
infections, alter testing/progression, or draw random numbers. Only intended for
the bounded diagnostic runner, not production calibration or economic claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from run_v3 import SimulationEngineV3


@dataclass(frozen=True)
class ContactSchedule:
    years: tuple[float, ...]
    levels: tuple[float, ...]
    reference: float
    lower: float
    upper: float

    def __post_init__(self):
        values = (*self.years, *self.levels, self.reference, self.lower, self.upper)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('schedule values must be finite')
        if len(self.years) < 2 or len(self.years) != len(self.levels):
            raise ValueError('at least two paired schedule anchors required')
        if any(b <= a for a, b in zip(self.years, self.years[1:])):
            raise ValueError('schedule years must be strictly increasing')
        if not 0 < self.lower <= self.reference <= self.upper:
            raise ValueError('reference must be positive and inside existing bounds')
        if any(not self.lower <= v <= self.upper for v in self.levels):
            raise ValueError('schedule level outside existing bounds; no clipping')

    def factor(self, year: float) -> float:
        if not math.isfinite(year):
            raise ValueError('calendar year must be finite')
        # Explicit hold outside endpoints; linear interpolation between them.
        return float(np.interp(year, self.years, self.levels)) / self.reference

    def as_dict(self):
        return {k: getattr(self, k) for k in ('years', 'levels', 'reference', 'lower', 'upper')}


def build_profiles(reference, lower, upper, fraction=0.25):
    """Fixed engineering perturbations toward CURRENT bounds, not fitted priors.

    Dates are diagnostic windows based on existing target periods, not estimates
    of behavioural change dates. Reference anchors and all other parameters stay
    fixed. No new empirical parameter values are claimed.
    """
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError('fraction must be in (0, 1]')
    high = reference + fraction * (upper - reference)
    low = reference - fraction * (reference - lower)
    years = (1975., 1990., 2000., 2010., 2025.)
    shapes = {
        'constant': (reference,) * 5,
        'early_up': (high, high, reference, reference, reference),
        'middle_down': (reference, reference, low, reference, reference),
        'recent_down': (reference, reference, reference, low, low),
        'combined': (high, high, low, low, low),
    }
    return {name: ContactSchedule(years, tuple(levels), reference, lower, upper)
            for name, levels in shapes.items()}


def msm_contacts(contacts, msm_index, factor):
    """Do not mutate the input; neutral case uses original arithmetic unchanged."""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('contact factor must be finite and positive')
    if contacts.ndim != 4 or contacts.shape[0] != contacts.shape[2]:
        raise ValueError('expected orientation/activity/orientation/activity contacts')
    if not 0 <= msm_index < contacts.shape[0]:
        raise ValueError('invalid MSM index')
    if factor == 1.0:
        return contacts
    result = contacts.astype(float, copy=True)
    result[msm_index, :, msm_index, :] *= factor
    return result


def stock_summary(population, msm_index):
    """Joint HIV x diagnosis x ART stocks; no annual event counters are used."""
    p = np.asarray(population)
    if p.ndim != 7 or not np.isfinite(p).all() or (p < 0).any():
        raise ValueError('invalid seven-axis population stock')
    return {
        'adult_total': int(p.sum()),
        'all_orientation_hiv_dx_art': p.sum(axis=(0, 1, 2, 6)).astype(int).tolist(),
        'msm_hiv_dx_art': p[:, msm_index].sum(axis=(0, 1, 5)).astype(int).tolist(),
        'meaning': 'stock immediately before transitions in the labelled year',
    }


class HistoricalProbeEngine(SimulationEngineV3):
    """Isolated experimental subclass with passive 1990/2025 state capture."""

    def __init__(self, config, *, seed, contact_schedule):
        self.contact_schedule = contact_schedule
        self.probe_states = {}
        self.contact_trace = []
        super().__init__(config, seed=seed)
        from config_utils import extract_value
        configured = extract_value(self.config['groups']['orientation_contact_multipliers'])['MSM']
        if float(configured) != contact_schedule.reference:
            raise ValueError('schedule reference must equal the configured MSM multiplier')
        if self.deterministic:
            raise ValueError('probe requires stochastic engine')
        if self.msm_relative_diagnosis_schedule is None:
            raise ValueError('probe requires the existing MSM diagnosis path')

    def _step_contacts_and_mixing(self):
        contacts = super()._step_contacts_and_mixing()
        factor = self.contact_schedule.factor(self._current_year)
        self.contact_trace.append((float(self._current_year), factor))
        return msm_contacts(contacts, self.groups.orient_to_idx['MSM'], factor)

    def step_simulation(self, step):
        year = self.time.start_year + step * self.time.years_per_step
        for capture_year in (1990, 2025):
            if abs(year - capture_year) < 1e-9:
                p = self.state.population.copy()
                self.probe_states[str(capture_year)] = {
                    'population': p,
                    'childhood_population': self.demography.childhood.population.copy(),
                    'summary': stock_summary(p, self.groups.orient_to_idx['MSM']),
                }
        return super().step_simulation(step)


def check_initialisation_contract(contract):
    """1990 input readiness ONLY. Never manufactures an initial population."""
    required = ('adult_population_by_age_orientation_activity',
                'hiv_stock_by_stage_diagnosis_art', 'childhood_population',
                'residence_phase_distribution', 'calendar_and_seed_policy',
                'uncertainty_and_source_provenance')
    issues = []
    if contract.get('year') != 1990 or contract.get('timing') != 'start_of_year':
        issues.append('require an explicitly start-of-year 1990 stock')
    for name in required:
        item = contract.get(name)
        if not isinstance(item, dict) or not item.get('source') or not item.get('definition'):
            issues.append(f'{name}: missing source/assumption and definition')
        elif item.get('quantity_type') != 'stock_or_initialisation_rule':
            issues.append(f'{name}: annual event flows cannot initialise a population stock')
    return {'ready_for_initialisation_design': not issues, 'issues': issues,
            'simulation_ready': False,
            'note': 'Metadata readiness is not numerical validation or restart equivalence.'}

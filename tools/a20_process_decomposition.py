"""Opt-in process switches for the A-20 decomposition diagnostic.

Production exposes one global deterministic flag through several different
surfaces.  A-20 needs to force selected process families onto their existing
stochastic branch while leaving all other families deterministic.  This module
does that by temporarily wrapping the existing production methods; it copies no
model computation and restores every binding when its context exits.
"""

from __future__ import annotations

from collections import Counter
from contextlib import AbstractContextManager
from functools import wraps
from typing import Any, Iterable


PROCESS_FAMILIES = (
    "diagnosis",
    "art",
    "progression",
    "hiv_mortality",
    "background_mortality",
    "births",
    "migration",
    "ageing_sexual_debut",
    "prep",
    "transmission",
    "unscoped_event_draw",
    "adverse_events",
)

SWEEP_ARMS = {
    "diagnosis": frozenset({"diagnosis"}),
    "art": frozenset({"art"}),
    "diagnosis_art": frozenset({"diagnosis", "art"}),
    "disease_progression": frozenset({"progression"}),
    "hiv_mortality": frozenset({"hiv_mortality"}),
    "background_mortality": frozenset({"background_mortality"}),
    "births": frozenset({"births"}),
    "migration": frozenset({"migration"}),
    "ageing_sexual_debut": frozenset({"ageing_sexual_debut"}),
    "prep": frozenset({"prep"}),
}


def process_family(process_id: Any) -> str | None:
    """Map a production CRN process id to the A-20 family that owns it."""
    head = process_id[0] if isinstance(process_id, tuple) else process_id
    if not isinstance(head, str):
        return None
    if head.startswith("diagnosis_"):
        return "diagnosis"
    if head in {"art_initiation", "art_dropout"}:
        return "art"
    if head.startswith("progression_"):
        return "progression"
    if head == "hiv_mortality":
        return "hiv_mortality"
    if head in {"adult_background_mortality", "childhood_background_mortality"}:
        return "background_mortality"
    if head in {"birth_count", "birth_sex"}:
        return "births"
    if head == "migration":
        return "migration"
    if (
        head.startswith("sexual_debut_assignment")
        or head in {
            "continuous_sexual_debut",
            "childhood_residence_aging",
            "childhood_fractional_aging_legacy",
            "adult_fractional_aging_legacy",
        }
    ):
        return "ageing_sexual_debut"
    if head in {"prep_uptake", "prep_dropout"}:
        return "prep"
    if head == "transmission":
        return "transmission"
    if head == "unscoped_event_draw":
        return "unscoped_event_draw"
    if head == "adverse_events":
        return "adverse_events"
    return None


def _replace_argument(args, kwargs, index: int, name: str, value):
    args = list(args)
    kwargs = dict(kwargs)
    if len(args) > index:
        args[index] = value
    else:
        kwargs[name] = value
    return tuple(args), kwargs


class ProcessModeSwitch(AbstractContextManager):
    """Temporarily force named process families stochastic.

    The engine itself remains deterministic.  CRN streams are requested
    directly from ``_crn_factory`` for forced families because production's
    ``_process_rng`` intentionally returns the shared legacy RNG whenever the
    engine-global deterministic flag is true.
    """

    def __init__(self, forced_families: Iterable[str] = ()) -> None:
        forced = frozenset(forced_families)
        unknown = forced.difference(PROCESS_FAMILIES)
        if unknown:
            raise ValueError(f"unknown A-20 process families: {sorted(unknown)}")
        self.forced_families = forced
        self.coverage = Counter()
        self.rng_requests = Counter()
        self.surface_calls = Counter()
        self._bindings: list[tuple[Any, str, Any]] = []
        self._inside_named_draw = 0

    def _is_forced(self, family: str) -> bool:
        return family in self.forced_families

    def _bind(self, owner, name: str, replacement) -> None:
        original = getattr(owner, name)
        self._bindings.append((owner, name, original))
        setattr(owner, name, replacement)

    def _flag_wrapper(
        self, original, *, family: str, flag_index: int, surface: str
    ):
        context = self

        @wraps(original)
        def wrapped(*args, **kwargs):
            context.coverage[family] += 1
            context.surface_calls[surface] += 1
            if context._is_forced(family):
                args, kwargs = _replace_argument(
                    args, kwargs, flag_index, "deterministic", False
                )
            return original(*args, **kwargs)

        return wrapped

    def __enter__(self):
        import migration as migration_module
        from adverse_events import AdverseEventModule
        from config_utils import extract_value
        from demography import ChildhoodPool, DemographyModule
        from run_v3 import SimulationEngineV3
        from transmission_corrected import TransmissionEngine

        context = self

        original_process_rng = SimulationEngineV3._process_rng

        @wraps(original_process_rng)
        def patched_process_rng(engine, process_id):
            context.surface_calls["run_v3.py:_process_rng"] += 1
            family = process_family(process_id)
            context.rng_requests[family or "unmapped"] += 1
            if family is None or not context._is_forced(family) or not engine.deterministic:
                return original_process_rng(engine, process_id)
            if engine._current_step is None or engine._current_year is None:
                raise RuntimeError(
                    "A-20 forced stochastic stream requested outside a timestep"
                )
            return engine._crn_factory.for_process(
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=process_id,
            )

        self._bind(SimulationEngineV3, "_process_rng", patched_process_rng)

        original_draw_for = SimulationEngineV3._draw_events_for

        @wraps(original_draw_for)
        def patched_draw_for(engine, process_id, n, p):
            context.surface_calls["run_v3.py:_draw_events_for"] += 1
            family = process_family(process_id)
            context.coverage[family or "unmapped_named_event"] += 1
            if family is None or not context._is_forced(family) or not engine.deterministic:
                context._inside_named_draw += 1
                try:
                    return original_draw_for(engine, process_id, n, p)
                finally:
                    context._inside_named_draw -= 1
            if engine._current_step is None or engine._current_year is None:
                raise RuntimeError("A-20 named event draw requested outside a timestep")
            rng = engine._crn_factory.for_process(
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=process_id,
            )
            return rng.binomial(n, p)

        self._bind(SimulationEngineV3, "_draw_events_for", patched_draw_for)

        original_draw = SimulationEngineV3._draw_events

        @wraps(original_draw)
        def patched_draw(engine, n, p):
            context.surface_calls["run_v3.py:_draw_events"] += 1
            family = "unscoped_event_draw"
            if context._inside_named_draw == 0:
                context.coverage[family] += 1
            if (
                context._inside_named_draw > 0
                or not context._is_forced(family)
                or not engine.deterministic
            ):
                return original_draw(engine, n, p)
            if engine._current_step is None or engine._current_year is None:
                raise RuntimeError("A-20 unscoped draw requested outside a timestep")
            rng = engine._crn_factory.for_process(
                year=engine._current_year,
                timestep=engine._current_step,
                process_id=family,
            )
            return rng.binomial(n, p)

        self._bind(SimulationEngineV3, "_draw_events", patched_draw)

        # Direct run_v3 branch: this bypasses _draw_events_for.
        original_adult_legacy = SimulationEngineV3._apply_fractional_aging_adults_legacy

        @wraps(original_adult_legacy)
        def patched_adult_legacy(engine, *args, **kwargs):
            family = "ageing_sexual_debut"
            context.coverage[family] += 1
            context.surface_calls[
                "run_v3.py:_apply_fractional_aging_adults_legacy"
            ] += 1
            if not context._is_forced(family) or not engine.deterministic:
                return original_adult_legacy(engine, *args, **kwargs)
            old = engine.deterministic
            engine.deterministic = False
            try:
                return original_adult_legacy(engine, *args, **kwargs)
            finally:
                engine.deterministic = old

        self._bind(
            SimulationEngineV3,
            "_apply_fractional_aging_adults_legacy",
            patched_adult_legacy,
        )

        # Methods with a deterministic argument.  Indices include ``self``.
        original_child_mortality = ChildhoodPool.apply_mortality

        @wraps(original_child_mortality)
        def patched_child_mortality(*args, **kwargs):
            family = "background_mortality"
            context.coverage[family] += 1
            life_table_config = (
                args[4] if len(args) > 4 else kwargs.get("life_table_config")
            )
            life_table_enabled = bool(
                extract_value(
                    life_table_config.get("enabled", {"value": False})
                )
            ) if life_table_config else False
            line = 256 if life_table_enabled else 270
            context.surface_calls[f"demography.py:apply_mortality:{line}"] += 1
            if context._is_forced(family):
                args, kwargs = _replace_argument(
                    args, kwargs, 3, "deterministic", False
                )
            return original_child_mortality(*args, **kwargs)

        self._bind(ChildhoodPool, "apply_mortality", patched_child_mortality)

        method_specs = (
            (ChildhoodPool, "process_continuous_debut", "ageing_sexual_debut", 3),
            (ChildhoodPool, "apply_fractional_aging_legacy", "ageing_sexual_debut", 2),
            (DemographyModule, "calculate_births", "births", 4),
            (DemographyModule, "add_births_to_childhood", "births", 3),
            (DemographyModule, "process_sexual_debut", "ageing_sexual_debut", 5),
            (DemographyModule, "_distribute_to_activities", "ageing_sexual_debut", 4),
            (DemographyModule, "_calculate_constant_mortality", "background_mortality", 3),
            (DemographyModule, "_calculate_life_table_mortality", "background_mortality", 4),
            (TransmissionEngine, "_calc_msm_infections", "transmission", 6),
            (TransmissionEngine, "calculate_transmission", "transmission", 4),
        )
        for owner, name, family, flag_index in method_specs:
            original = getattr(owner, name)
            self._bind(
                owner,
                name,
                self._flag_wrapper(
                    original,
                    family=family,
                    flag_index=flag_index,
                    surface=f"{owner.__module__}.py:{name}",
                ),
            )

        original_distribute = migration_module._distribute_int
        self._bind(
            migration_module,
            "_distribute_int",
            self._flag_wrapper(
                original_distribute,
                family="migration",
                flag_index=3,
                surface="migration.py:_distribute_int",
            ),
        )

        # HTA-only surface.  Calibration should leave it dormant, but the
        # binding is still restored and covered by unit tests.
        original_ae = AdverseEventModule._item_state_machine_ongoing

        @wraps(original_ae)
        def patched_ae(module, *args, **kwargs):
            family = "adverse_events"
            context.coverage[family] += 1
            context.surface_calls[
                "adverse_events.py:_item_state_machine_ongoing"
            ] += 1
            if not context._is_forced(family) or not module.deterministic:
                return original_ae(module, *args, **kwargs)
            old = module.deterministic
            module.deterministic = False
            try:
                return original_ae(module, *args, **kwargs)
            finally:
                module.deterministic = old

        self._bind(AdverseEventModule, "_item_state_machine_ongoing", patched_ae)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for owner, name, original in reversed(self._bindings):
            setattr(owner, name, original)
        self._bindings.clear()
        return False

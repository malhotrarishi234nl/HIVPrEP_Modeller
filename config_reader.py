"""
ConfigReader — Native V10 config accessor.

Wraps a V10 config dict and provides accessor methods that return
V6-shaped section dicts. Each method encapsulates the V10 path knowledge
(nested structure, advanced blocks, key renames) so consuming modules
see the same flat dicts they always did.

Design principles:
  - `advanced` blocks are transparent — callers never need to know
    whether a field lives in the core block or in `advanced`.
  - Intervention key renames handled internally — callers use V6 names
    (Oral_PrEP, Lenacapavir); ConfigReader maps to V10 keys (oral_prep,
    lenacapavir).
  - ART schedule restructure handled — V10 `interventions.art.schedule`
    is exposed in the V6 `art_schedule` shape.
  - `disease.treatment` is reconstructed from scattered V10 sources.
  - extract_value() is NOT called here — callers still call it on the
    returned dicts as they always did (preserves identical behavior).

Only V10-format configs are accepted (must contain 'run_control' or
'transmission_model' at the top level). Passing any other format raises
ConfigFormatError immediately at construction time.

Usage:
    from config_reader import ConfigReader
    reader = ConfigReader(raw_v10_config)
    time_cfg = reader.time()           # {"unit": {...}, "step_size": {...}, ...}
    groups_cfg = reader.groups()       # {"orientations": [...], ...}
"""

import copy
from typing import Any, Dict, Optional


class ConfigFormatError(ValueError):
    """
    Raised when a config dict is not in the expected V10 format.

    V10 format requires at least one of: 'run_control', 'transmission_model'
    at the top level. Historic flat V6/V7 configs are not accepted.
    """
    pass


class ConfigReader:
    """
    Read-only accessor for V10 config format.

    Each method returns a V6-shaped dict for the corresponding config
    section.  The raw V10 dict is preserved for inspection / debugging.
    """

    # V10 intervention key → V6 display name
    _V10_TO_V6_INTERVENTION = {
        "oral_prep":   "Oral_PrEP",
        "lenacapavir": "Lenacapavir",
    }
    _V6_TO_V10_INTERVENTION = {v: k for k, v in _V10_TO_V6_INTERVENTION.items()}

    def __init__(self, config: Dict):
        """
        Parameters
        ----------
        config : dict
            A raw V10 config dict. Must contain 'run_control' or
            'transmission_model' at the top level. Raises ConfigFormatError
            for any other format (including historic V6/V7 flat configs).
        """
        if not self.is_v10(config):
            top_keys = list(config.keys())[:6]
            raise ConfigFormatError(
                f"ConfigReader requires V10 format (must contain 'run_control' "
                f"or 'transmission_model'). Got top-level keys: {top_keys}"
            )
        self.raw = config
        self._is_v10 = True
        self._cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Detection / version helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_v10(config: Dict) -> bool:
        """Return True if the config looks like V10 format."""
        return bool({"run_control", "transmission_model"} & set(config.keys()))

    def get_config_version(self) -> str:
        """Return the version string from meta, or 'unknown'."""
        meta = self.raw.get("meta", {})
        ver = meta.get("version", {})
        if isinstance(ver, dict):
            return str(ver.get("value", "unknown"))
        return str(ver)

    @staticmethod
    def _unwrap_value(value: Any) -> Any:
        """Return the payload from {"value": ...} wrappers without touching metadata."""
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    @staticmethod
    def _format_schedule_year(year: float) -> str:
        """Format schedule years without a trailing .0 when they are whole years."""
        if float(year).is_integer():
            return str(int(year))
        return str(year)

    def _first_nonzero_schedule_year(self, schedule: Dict) -> Optional[float]:
        """Return the first year whose unwrapped schedule value is positive."""
        candidates = []
        for raw_year, raw_value in schedule.items():
            try:
                value = float(self._unwrap_value(raw_value))
                year = float(raw_year)
            except (TypeError, ValueError):
                continue
            if value > 0:
                candidates.append(year)
        return min(candidates) if candidates else None

    # ------------------------------------------------------------------
    # Section accessors — each returns a V6-shaped dict
    # ------------------------------------------------------------------

    def meta(self) -> Dict:
        """meta block (without model_notes, which moves to notes())."""
        if "meta" not in self._cache:
            m = copy.deepcopy(self.raw.get("meta", {}))
            m.pop("model_notes", None)
            self._cache["meta"] = m
        return self._cache["meta"]

    def notes(self) -> Dict:
        """Top-level notes (from meta.model_notes)."""
        return self.raw.get("meta", {}).get("model_notes", {})

    def time(self) -> Dict:
        """
        V6 'time' section — extracted from run_control.
        Keys: unit, step_size, start_year, end_year.
        """
        if "time" not in self._cache:
            rc = self.raw.get("run_control", {})
            self._cache["time"] = {
                "unit":       rc.get("unit",       {"value": "months", "notes": "Base simulation unit"}),
                "step_size":  rc.get("step_size",  {"value": 12,       "notes": "Units per timestep"}),
                "start_year": rc.get("start_year", {"value": 1970,     "notes": "Simulation start year"}),
                "end_year":   rc.get("end_year",   {"value": 2020,     "notes": "Simulation end year"}),
            }
        return self._cache["time"]

    def simulation_controls(self) -> Dict:
        """
        V6 'simulation_controls' section — extracted from run_control.
        Keys: n_runs, seed, deterministic_mode.
        """
        if "simulation_controls" not in self._cache:
            rc = self.raw.get("run_control", {})
            self._cache["simulation_controls"] = {
                "n_runs":             rc.get("n_runs",             {"value": 1,    "notes": "Number of runs"}),
                "seed":               rc.get("seed",               {"value": 42,   "notes": "RNG seed"}),
                "deterministic_mode": rc.get("deterministic_mode", {"value": True, "notes": "Deterministic mode"}),
            }
        return self._cache["simulation_controls"]

    def output(self) -> Dict:
        """
        V6 'output' section — core + advanced fields flattened.
        Advanced fields (write_state_counts, summary_years, quantiles)
        are promoted to the top level.
        """
        if "output" not in self._cache:
            out_block = self.raw.get("output", {})
            result = {k: v for k, v in out_block.items() if k != "advanced"}
            advanced = out_block.get("advanced", {})
            for key in ("write_state_counts", "summary_years", "quantiles"):
                if key in advanced:
                    result[key] = advanced[key]
            self._cache["output"] = result
        return self._cache["output"]

    def population(self) -> Dict:
        """V6 'population' section — unchanged from V10."""
        if "population" not in self._cache:
            self._cache["population"] = copy.deepcopy(self.raw.get("population", {}))
        return self._cache["population"]

    def demography(self) -> Dict:
        """
        V6 'demography' section — core fields + advanced scalars promoted.
        Keys: enabled, mode, aging_method, background_mortality,
              fixed_net_addition.
        Sub-blocks (childhood, sexual_debut, etc.) are separate methods.
        """
        if "demography" not in self._cache:
            dem = self.raw.get("demography", {})
            adv = dem.get("advanced", {})
            result = {}
            for key in ("enabled", "mode"):
                if key in dem:
                    result[key] = dem[key]
            for key in ("aging_method", "background_mortality"):
                if key in adv:
                    result[key] = adv[key]
            if "fixed_net_addition" in adv:
                result["fixed_net_addition"] = adv["fixed_net_addition"]
            self._cache["demography"] = result
        return self._cache["demography"]

    def childhood(self) -> Dict:
        """V6 'childhood' section — from demography.childhood."""
        return self.raw.get("demography", {}).get("childhood", {})

    def sexual_debut(self) -> Dict:
        """V6 'sexual_debut' section — from demography.sexual_debut."""
        return self.raw.get("demography", {}).get("sexual_debut", {})

    def life_table(self) -> Dict:
        """
        V6 'life_table' section — from demography.life_table.
        Promotes advanced.source_notes to top level.
        """
        if "life_table" not in self._cache:
            lt = self.raw.get("demography", {}).get("life_table", {})
            result = {}
            for key in ("enabled", "survival_by_age_limit"):
                if key in lt:
                    result[key] = lt[key]
            adv = lt.get("advanced", {})
            if "source_notes" in adv:
                result["source_notes"] = adv["source_notes"]
            elif "source_notes" in lt:
                result["source_notes"] = lt["source_notes"]
            self._cache["life_table"] = result
        return self._cache["life_table"]

    def fertility(self) -> Dict:
        """
        V6 'fertility' section — from demography.fertility.
        Promotes advanced.year_multipliers to top level.
        """
        if "fertility" not in self._cache:
            fert = self.raw.get("demography", {}).get("fertility", {})
            result = {}
            for key in ("enabled", "fraction_male_newborns", "asfr_by_age_limit"):
                if key in fert:
                    result[key] = fert[key]
            adv = fert.get("advanced", {})
            if "year_multipliers" in adv:
                result["year_multipliers"] = adv["year_multipliers"]
            elif "year_multipliers" in fert:
                result["year_multipliers"] = fert["year_multipliers"]
            self._cache["fertility"] = result
        return self._cache["fertility"]

    def population_adjustments(self) -> Dict:
        """
        V6 'population_adjustments' section — from demography.advanced.
        """
        dem_adv = self.raw.get("demography", {}).get("advanced", {})
        return dem_adv.get(
            "population_adjustments",
            {"enabled": {"value": False, "notes": "Population adjustments not configured"}}
        )

    def migration(self) -> Dict:
        """
        V6 'migration' section — from demography.migration.
        Promotes quantity.advanced.{rate, flat} and
        profile.advanced.custom_weights to their parent levels.
        Preserves top-level and sub-level notes if present.
        """
        if "migration" not in self._cache:
            mig = self.raw.get("demography", {}).get("migration", {})
            if not mig:
                self._cache["migration"] = {}
                return self._cache["migration"]

            result = {}
            if "enabled" in mig:
                result["enabled"] = mig["enabled"]

            # quantity: promote advanced (rate, flat)
            if "quantity" in mig:
                q_src = mig["quantity"]
                q_dst = {}
                if "mode" in q_src:
                    q_dst["mode"] = q_src["mode"]
                if "year_specific" in q_src:
                    q_dst["year_specific"] = q_src["year_specific"]
                q_adv = q_src.get("advanced", {})
                for key in ("rate", "flat"):
                    if key in q_adv:
                        q_dst[key] = q_adv[key]
                    elif key in q_src:
                        q_dst[key] = q_src[key]
                result["quantity"] = q_dst

            # profile: promote advanced.custom_weights
            if "profile" in mig:
                p_src = mig["profile"]
                p_dst = {}
                if "mode" in p_src:
                    p_dst["mode"] = p_src["mode"]
                if "working_age" in p_src:
                    p_dst["working_age"] = p_src["working_age"]
                p_adv = p_src.get("advanced", {})
                if "custom_weights" in p_adv:
                    p_dst["custom_weights"] = p_adv["custom_weights"]
                elif "custom_weights" in p_src:
                    p_dst["custom_weights"] = p_src["custom_weights"]
                result["profile"] = p_dst

            if "migrant_hiv" in mig:
                result["migrant_hiv"] = copy.deepcopy(mig["migrant_hiv"])

            self._cache["migration"] = result
        return self._cache["migration"]

    def groups(self) -> Dict:
        """
        V6 'groups' section — from transmission_model.groups.
        Promotes advanced.orientation_contact_multipliers.
        """
        if "groups" not in self._cache:
            grp = self.raw.get("transmission_model", {}).get("groups", {})
            result = {}
            for key in ("orientations", "age_groups", "sexual_activity_groups", "hiv_stages"):
                if key in grp:
                    result[key] = grp[key]
            adv = grp.get("advanced", {})
            if "orientation_contact_multipliers" in adv:
                result["orientation_contact_multipliers"] = adv["orientation_contact_multipliers"]
            elif "orientation_contact_multipliers" in grp:
                result["orientation_contact_multipliers"] = grp["orientation_contact_multipliers"]
            self._cache["groups"] = result
        return self._cache["groups"]

    def mixing(self) -> Dict:
        """
        V6 'mixing' section — from transmission_model.mixing.
        Promotes advanced.condom_usage.

        V11+: passes through epsilon_hetero and epsilon_msm scalars (Garnett 1994
        parameterisation).  When present these replace the legacy matrix dicts.

        V10 and earlier: still passes through heterosexual / msm matrix dicts for
        backward compatibility.  The engine (_step_contacts_and_mixing) will emit a
        DeprecationWarning and fall back to matrix-mode when it detects the absence
        of epsilon keys.
        """
        if "mixing" not in self._cache:
            mix = self.raw.get("transmission_model", {}).get("mixing", {})
            result = {}
            # V11+ epsilon keys (preferred)
            for key in ("epsilon_hetero", "epsilon_msm"):
                if key in mix:
                    result[key] = mix[key]
            # Legacy matrix keys — passed through for backward compat with V10 configs
            for key in ("heterosexual", "msm"):
                if key in mix:
                    result[key] = mix[key]
            adv = mix.get("advanced", {})
            if "condom_usage" in adv:
                result["condom_usage"] = adv["condom_usage"]
            elif "condom_usage" in mix:
                result["condom_usage"] = mix["condom_usage"]
            self._cache["mixing"] = result
        return self._cache["mixing"]

    def disease(self) -> Dict:
        """
        V6 'disease' section — from transmission_model.disease.
        Reconstructs disease.treatment from advanced block.
        """
        if "disease" not in self._cache:
            dis = self.raw.get("transmission_model", {}).get("disease", {})
            result = {}
            # C-1: exogenous_non_msm must be in this list. It is an allowlist, so a
            # block that is present, well-formed and validated in the raw config is
            # silently DISCARDED if its key is missing here -- the engine then sees
            # no block and disables the mechanism with no error. That is the same
            # silent-default failure mode as A-25 and C-4: a plausible default
            # (here, "absent") standing in for a missing key. Adding a disease-level
            # block without adding it here is a no-op that looks like a working
            # feature. See tests/test_c1_exogenous_non_msm.py.
            for key in ("transmission", "progression", "mortality", "diagnosis",
                        "exogenous_non_msm"):
                if key in dis:
                    result[key] = dis[key]

            # Reconstruct treatment from V10 advanced
            adv = dis.get("advanced", {})
            treatment: Dict = {}
            for key in ("art_transmission_reduction", "art_mortality_reduction"):
                if key in adv:
                    treatment[key] = adv[key]
            # Note: art_initiation_rate and art_dropout_rate are intentionally NOT
            # added here. _step_art_transitions reads rates exclusively from
            # ScheduleManager (when art_schedule is present) or uses 0.0 (when absent).
            # Fabricating flat-rate fallback values here would be dead config keys that
            # create a false impression of being authoritative model parameters.
            result["treatment"] = treatment
            self._cache["disease"] = result
        return self._cache["disease"]

    def art_schedule(self) -> Optional[Dict]:
        """
        V6 'art_schedule' section — built from interventions.art.schedule.
        Preserves top-level notes if present.
        Returns None if no ART block exists.
        """
        if "art_schedule" not in self._cache:
            art_block = self.raw.get("interventions", {}).get("art", {})
            if not art_block:
                self._cache["art_schedule"] = None
                return None

            sched = art_block.get("schedule", {})
            result: Dict = {}

            if "interpolation" in sched:
                result["interpolation"] = sched["interpolation"]
            else:
                result["interpolation"] = {"value": "stepwise", "notes": "Default interpolation"}

            # initiation: V10 value → V6 schedule
            if "initiation" in sched:
                init_block = sched["initiation"]
                init_v6: Dict = {}
                if "notes" in init_block:
                    init_v6["notes"] = init_block["notes"]
                if "value" in init_block:
                    init_v6["schedule"] = init_block["value"]
                result["initiation"] = init_v6

            # discontinuation: prefer an explicit schedule when supplied. Otherwise,
            # bridge the V10 flat advanced.art_dropout_rate into the schedule shape
            # consumed by ScheduleManager. The flat rate starts when ART first has a
            # nonzero initiation schedule, not at model start.
            if "discontinuation" in sched:
                disc_block = sched["discontinuation"]
                disc_v6: Dict = {}
                if "notes" in disc_block:
                    disc_v6["notes"] = disc_block["notes"]
                if "value" in disc_block:
                    disc_v6["schedule"] = disc_block["value"]
                elif "schedule" in disc_block:
                    disc_v6["schedule"] = disc_block["schedule"]
                result["discontinuation"] = disc_v6
            else:
                dropout_raw = art_block.get("advanced", {}).get("art_dropout_rate")
                dropout_value = self._unwrap_value(dropout_raw)
                try:
                    dropout_rate = float(dropout_value)
                except (TypeError, ValueError):
                    dropout_rate = 0.0

                init_schedule = result.get("initiation", {}).get("schedule", {})
                first_art_year = self._first_nonzero_schedule_year(init_schedule)
                if dropout_rate > 0 and first_art_year is not None:
                    result["discontinuation"] = {
                        "schedule": {
                            self._format_schedule_year(first_art_year): dropout_rate
                        },
                        "notes": (
                            "Flat ART dropout rate bridged from "
                            "interventions.art.advanced.art_dropout_rate."
                        ),
                    }

            self._cache["art_schedule"] = result
        return self._cache["art_schedule"]

    def interventions(self) -> Dict:
        """
        V6 'interventions' section — oral_prep → Oral_PrEP, lenacapavir → Lenacapavir.
        Advanced fields promoted to top level within each intervention.
        """
        if "interventions" not in self._cache:
            raw_interventions = self.raw.get("interventions", {})
            result = {}
            for v10_key, v6_key in self._V10_TO_V6_INTERVENTION.items():
                src = raw_interventions.get(v10_key)
                if src is None:
                    continue
                dst = {}
                for key in ("enabled", "uptake_rate", "dropout_rate",
                            "eligible_orientations", "eligible_age_range",
                            "eligible_activity_classes",
                            "waning_efficacy"):          # V12.2: must survive V10→V6 translation
                    if key in src:
                        dst[key] = src[key]
                adv = src.get("advanced", {})
                for key in ("efficacy", "dosing_interval", "uptake_modifier_by_activity"):
                    if key in adv:
                        dst[key] = adv[key]
                    elif key in src:
                        dst[key] = src[key]
                result[v6_key] = dst
            self._cache["interventions"] = result
        return self._cache["interventions"]

    # ------------------------------------------------------------------
    # Convenience: build the full V6-compatible dict (for testing only)
    # ------------------------------------------------------------------

    def as_v6_dict(self) -> Dict:
        """
        Return a complete V6-compatible dict translated from the V10 config.

        Equivalent to what config_adapter.normalise_config() produced before
        that module was retired. All callers receive the same flat-key structure
        that the engine and schedule manager expect.
        """
        out: Dict[str, Any] = {}
        out["meta"] = self.meta()
        out["time"] = self.time()
        out["simulation_controls"] = self.simulation_controls()
        out["output"] = self.output()
        out["population"] = self.population()
        out["demography"] = self.demography()
        out["childhood"] = self.childhood()
        out["sexual_debut"] = self.sexual_debut()
        out["life_table"] = self.life_table()
        out["fertility"] = self.fertility()
        out["population_adjustments"] = self.population_adjustments()
        out["migration"] = self.migration()
        out["groups"] = self.groups()
        out["mixing"] = self.mixing()
        out["disease"] = self.disease()
        art = self.art_schedule()
        if art is not None:
            out["art_schedule"] = art
        # Forward prep_schedules if present in raw config (used by ScheduleManager
        # to provide time-varying PrEP initiation/discontinuation rates; without this
        # the ScheduleManager always returns 0 for PrEP even if uptake_rate is set).
        if "prep_schedules" in self.raw:
            out["prep_schedules"] = copy.deepcopy(self.raw["prep_schedules"])
        out["interventions"] = self.interventions()
        out["notes"] = self.notes()
        return out

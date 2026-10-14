"""A-25 / C-4 prep: a missing name or a missing schedule default must never resolve silently.

Two defects of the same class motivate this file.

* **A-25.** ``eligible_age_range.max`` was ``"64-69"``. No such age bin exists -- it is
  ``"65-69"`` -- and ``age_to_idx.get(name, n_age_groups - 1)`` silently resolved it to the
  LAST bin, so PrEP eligibility spanned 15 to 110+ instead of 15-69, in all ten configs in
  ``configs/``, undetected, affecting the submitted thesis.
* **C-4.** ``TimeSchedule`` defaulted ``default_before`` to 0.0. That is correct for an
  *availability* schedule and wrong for a *multiplier*, whose neutral value is 1.0. The MSM
  relative-diagnosis multiplier silently returned 0.0 for 1975-1995, so no MSM was ever
  diagnosed in the first 21 years of the epidemic.

Both were invisible because a plausible default stood in for a missing key. These tests
exist so neither pattern can be reintroduced.
"""

import ast
import json
from pathlib import Path

import pytest

from config_utils import GroupNameError, require_idx
from schedule_utils import ScheduleManager, TimeSchedule

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "configs"


# ----------------------------------------------------------------- require_idx


def test_require_idx_returns_the_index_for_a_valid_name():
    assert require_idx({"15-19": 0, "65-69": 10}, "65-69", "age bin") == 10


def test_require_idx_raises_on_the_exact_a25_typo():
    with pytest.raises(GroupNameError) as excinfo:
        require_idx({"15-19": 0, "65-69": 10}, "64-69", "PrEP eligible_age_range.max")
    message = str(excinfo.value)
    assert "64-69" in message
    assert "PrEP eligible_age_range.max" in message
    # The available names must be listed, or the next person guesses again.
    assert "65-69" in message


def test_require_idx_never_substitutes_a_plausible_index():
    """The whole defect: a miss must not come back as a number."""
    age_map = {f"{a}-{a + 4}": i for i, a in enumerate(range(15, 111, 5))}
    for missing in ("64-69", "49-54", "", "110", None, 10):
        with pytest.raises((GroupNameError, TypeError)):
            require_idx(age_map, missing, "age bin")


def test_require_idx_is_a_keyerror_so_existing_handlers_still_behave():
    assert issubclass(GroupNameError, KeyError)


# ------------------------------------------------------- TimeSchedule.default_before


def test_time_schedule_requires_default_before():
    """It must be impossible to construct a schedule without stating this."""
    with pytest.raises(TypeError):
        TimeSchedule({"1996": 0.59})


def test_time_schedule_default_before_is_keyword_only():
    """Positional passing would let it be supplied by accident."""
    with pytest.raises(TypeError):
        TimeSchedule({"1996": 0.59}, "stepwise", 1.0)


@pytest.mark.parametrize("default_before", [0.0, 1.0, 0.5906550484268714])
def test_time_schedule_returns_the_stated_default_before_the_first_anchor(default_before):
    schedule = TimeSchedule({"1996": 0.59, "2004": 0.25}, interpolation="stepwise",
                            default_before=default_before)
    assert schedule.get_value(1975.0) == default_before
    assert schedule.get_value(1995.99) == default_before
    assert schedule.get_value(1996.0) == pytest.approx(0.59)


def test_every_production_time_schedule_states_its_default_before():
    """C-4's regression guard: a new construction site cannot inherit a silent 0.0.

    The signature already enforces this at runtime, but only on code paths that execute.
    This reads the source so an unexercised branch cannot slip through.
    """
    offenders = []
    for path in sorted(ROOT.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "TimeSchedule":
                if not any(kw.arg == "default_before" for kw in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "TimeSchedule constructed without an explicit default_before at "
        f"{offenders}. An availability schedule wants 0.0 and a multiplier wants 1.0; "
        "C-4 is what happens when a multiplier inherits the availability default."
    )


# --------------------------------------------------------------- the configs themselves


def _eligible_age_ranges(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "eligible_age_range":
                inner = value.get("value", value) if isinstance(value, dict) else value
                if isinstance(inner, dict):
                    yield inner
            yield from _eligible_age_ranges(value)
    elif isinstance(node, list):
        for item in node:
            yield from _eligible_age_ranges(item)


def _age_bin_names(config):
    for node in (config,):
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if "age_groups" in current:
                    groups = current["age_groups"]
                    groups = groups.get("value", groups) if isinstance(groups, dict) else groups
                    if isinstance(groups, list) and groups and isinstance(groups[0], dict):
                        return {g["name"] for g in groups}
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return set()


@pytest.mark.parametrize("config_path", sorted(CONFIG_DIR.glob("*.json")), ids=lambda p: p.name)
def test_no_config_names_an_age_bin_that_does_not_exist(config_path):
    """A-25's regression guard, across every config rather than just the base case."""
    config = json.loads(config_path.read_text())
    valid = _age_bin_names(config)
    if not valid:
        pytest.skip(f"{config_path.name} defines no age_groups")
    for entry in _eligible_age_ranges(config):
        for bound in ("min", "max"):
            name = entry.get(bound)
            if name is None:
                continue
            assert name in valid, (
                f"{config_path.name}: eligible_age_range.{bound} = {name!r} is not an age "
                f"bin in this config. This is A-25 exactly: the name resolved silently to "
                f"the last bin and PrEP eligibility ran to 110+. Valid names: "
                f"{sorted(valid)}"
            )


# ------------------------------------------------- corridor start_year / anchor guard


def _base_config():
    return json.loads((CONFIG_DIR / "V13.22_20260604_nearfit_rank001_basecase.json").read_text())


def test_shipped_corridor_configuration_is_accepted():
    ScheduleManager(_base_config()).validate_sanity()


def test_corridor_start_year_before_the_first_anchor_is_refused():
    """Otherwise the ceiling silently falls through to 0 and caps PrEP stock at zero."""
    config = _base_config()
    corridor = config["prep_schedules"]["Oral_PrEP"]["advanced"]["historical_stock_corridor"]
    corridor["start_year"] = 2015
    with pytest.raises(ValueError, match="precedes the first"):
        ScheduleManager(config).validate_sanity()


# ------------------------------------------------------------------------ C-4


def test_c4_msm_multiplier_defaults_to_the_neutral_value_not_zero():
    """C-4's regression guard.

    The MSM relative-diagnosis schedule is a MULTIPLIER on the base diagnosis rate. A
    ``default_before`` of 0.0 makes MSM undiagnosable for every year before the first
    anchor (1996 in every current config), which is what C-4 was. The neutral value of a
    multiplier is 1.0.
    """
    import json

    from run_v3 import SimulationEngineV3

    raw = json.loads(
        (CONFIG_DIR / "V13.22_20260604_nearfit_rank001_basecase.json").read_text()
    )
    # The builder reads the ConfigReader-translated shape (disease.diagnosis), while the
    # file on disk nests it under transmission_model. Take the real block, placed where the
    # builder looks for it, so this tests the production code path on production data.
    diagnosis = raw["transmission_model"]["disease"]["diagnosis"]
    assert "msm_relative_diagnosis" in diagnosis
    engine = SimulationEngineV3.__new__(SimulationEngineV3)
    engine.config = {"disease": {"diagnosis": diagnosis}}
    schedule = SimulationEngineV3._build_msm_relative_diagnosis_schedule(engine)
    assert schedule is not None, "basecase should enable msm_relative_diagnosis"
    assert schedule.default_before == 1.0, (
        "the MSM relative-diagnosis MULTIPLIER must default to 1.0 before its first "
        "anchor. 0.0 means 'MSM are undiagnosable', which is C-4."
    )
    # Before the first anchor the multiplier must be neutral, not zero.
    assert schedule.get_value(1990.0) == 1.0
    assert schedule.get_value(1995.99) == 1.0
    # At and after the first anchor the configured values take over.
    assert schedule.get_value(1996.0) == pytest.approx(0.5906550484268714)

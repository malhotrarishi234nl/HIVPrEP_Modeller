from types import SimpleNamespace
import pandas as pd
import pytest
from run_focused_confirmation_20260917 import target_diagnostics
from run_hm_confirmation import aggregate_replicates


def test_same_seed_agreement_does_not_mix_different_trajectories():
    raw = pd.DataFrame({'source_design_id': [600, 600],
                        'confirmation_seed': [1, 2],
                        'a_2000': [100., 400.], 'a_2001': [400., 100.]})
    selected = pd.DataFrame({'design_id': [600]})
    means = aggregate_replicates(raw, selected, [])
    targets = [SimpleNamespace(model_output='a', year=y, z=100., key=f'a@{y}')
               for y in [2000, 2001]]
    result = target_diagnostics(raw, means, targets)
    assert list(result.fraction_within_20pct) == [.5, .5, 0.]
    assert means['mcvar__a_2000'].iloc[0] == pytest.approx(45000.)


def test_missing_trajectory_cannot_look_like_agreement():
    raw = pd.DataFrame({'source_design_id': [600, 600], 'a_2000': [100., float('nan')]})
    means = pd.DataFrame({'design_id': [600], 'a_2000': [100.]})
    with pytest.raises(ValueError, match='Nonfinite'):
        target_diagnostics(raw, means, [SimpleNamespace(model_output='a', year=2000, z=100., key='a@2000')])

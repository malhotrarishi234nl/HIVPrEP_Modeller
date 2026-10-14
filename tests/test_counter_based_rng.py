import numpy as np
from scipy.stats import binom, poisson

from counter_based_rng import CounterBasedRNG, CounterBasedRNGFactory, _key_words


def _rng(process="mortality", step=200, year=2025.0, seed=42):
    return CounterBasedRNG(seed, year, step, process)


def test_same_full_key_reproduces_draws_exactly():
    population = np.arange(1, 13, dtype=np.int64).reshape(3, 4)
    first = _rng().binomial(population, 0.2)
    second = _rng().binomial(population, 0.2)
    np.testing.assert_array_equal(first, second)


def test_ordered_key_encoding_has_a_fixed_cross_run_fingerprint():
    words = _key_words(42, 2025.0, 200, "transmission", ((1, 2), "stage"))
    assert words.tolist() == [
        3413794660,
        2210356247,
        1464145061,
        77112412,
        2948368811,
        3123864638,
        702193523,
        1901248919,
    ]


def test_process_order_cannot_shift_another_process_stream():
    factory = CounterBasedRNGFactory(42)
    transmission_first = factory.for_process(
        year=2025.0, timestep=200, process_id="transmission"
    ).binomial(1000, 0.05)
    factory.for_process(
        year=2025.0, timestep=200, process_id="births"
    ).poisson(100.0)
    transmission_after = factory.for_process(
        year=2025.0, timestep=200, process_id="transmission"
    ).binomial(1000, 0.05)
    assert transmission_first == transmission_after


def test_array_strata_do_not_desynchronise_when_one_population_changes():
    baseline = np.array([100, 200, 300], dtype=np.int64)
    intervention = np.array([100, 999, 300], dtype=np.int64)
    draw_a = _rng().binomial(baseline, 0.15)
    draw_b = _rng().binomial(intervention, 0.15)
    assert draw_a[0] == draw_b[0]
    assert draw_a[2] == draw_b[2]


def test_explicit_stratum_keys_are_stable_across_branch_order():
    first = _rng().for_stratum(("age", 4, "female")).binomial(500, 0.1)
    _rng().for_stratum(("age", 9, "male")).binomial(10, 0.9)
    second = _rng().for_stratum(("age", 4, "female")).binomial(500, 0.1)
    assert first == second


def test_seed_time_process_and_stratum_all_enter_the_key():
    reference = _rng().for_stratum("a").random(size=8)
    alternatives = [
        _rng(seed=43).for_stratum("a").random(size=8),
        _rng(step=201).for_stratum("a").random(size=8),
        _rng(year=2025.25).for_stratum("a").random(size=8),
        _rng(process="births").for_stratum("a").random(size=8),
        _rng().for_stratum("b").random(size=8),
    ]
    assert all(not np.array_equal(reference, candidate) for candidate in alternatives)


def test_array_strata_use_coordinates_not_flat_offsets_when_shape_expands():
    smaller = _rng().binomial(np.full((2, 3), 100), 0.25)
    expanded = _rng().binomial(np.full((2, 4), 100), 0.25)
    np.testing.assert_array_equal(smaller, expanded[:, :3])


def test_repeated_draws_within_one_stratum_use_distinct_counters():
    rng = _rng().for_stratum("same-cell")
    first = rng.random(size=16)
    second = rng.random(size=16)
    assert not np.array_equal(first, second)


def test_multinomial_is_exact_total_and_reproducible():
    first = _rng().for_stratum("migration").multinomial(1000, [0.2, 0.3, 0.5])
    second = _rng().for_stratum("migration").multinomial(1000, [0.2, 0.3, 0.5])
    assert int(first.sum()) == 1000
    np.testing.assert_array_equal(first, second)


def test_factory_uses_philox_generator():
    rng = _rng()
    generator = rng._generator(("test", 0))
    assert isinstance(generator.bit_generator, np.random.Philox)


def test_binomial_boundary_probabilities_are_exact():
    counts = np.arange(20, dtype=np.int64)
    np.testing.assert_array_equal(_rng().binomial(counts, 0.0), np.zeros(20, dtype=int))
    np.testing.assert_array_equal(_rng().binomial(counts, 1.0), counts)


def test_binomial_inverse_transform_has_expected_mean_and_variance():
    draws = _rng().binomial(np.full(10_000, 100, dtype=np.int64), 0.2)
    assert abs(float(draws.mean()) - 20.0) < 0.15
    assert abs(float(draws.var(ddof=1)) - 16.0) < 0.5


def test_low_level_discrete_quantiles_match_scipy_reference():
    n = np.arange(1, 501, dtype=np.int64)
    p = np.linspace(0.001, 0.999, n.size)

    binomial_probe = _rng(process="binomial-quantile")
    call_key = binomial_probe._reserve_call("binomial", None)
    uniforms = binomial_probe._uniforms(n.shape, call_key)
    expected_binomial = binom.ppf(uniforms, n, p).astype(np.int64)
    actual_binomial = _rng(process="binomial-quantile").binomial(n, p)
    np.testing.assert_array_equal(actual_binomial, expected_binomial)

    lam = np.linspace(0.0, 1_000.0, n.size)
    poisson_probe = _rng(process="poisson-quantile")
    call_key = poisson_probe._reserve_call("poisson", None)
    uniforms = poisson_probe._uniforms(lam.shape, call_key)
    expected_poisson = poisson.ppf(uniforms, lam).astype(np.int64)
    actual_poisson = _rng(process="poisson-quantile").poisson(lam)
    np.testing.assert_array_equal(actual_poisson, expected_poisson)

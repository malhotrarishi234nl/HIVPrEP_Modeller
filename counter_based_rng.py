"""Counter-based random-number streams for paired simulation comparisons.

Each stochastic process receives a stream keyed by the model seed, calendar
year, absolute timestep, process identifier, and stratum identifier.  One
``numpy.random.Philox`` engine is retained per process facade and reset to the
explicit counter for each draw, so a draw in one process or cell cannot shift
the random numbers used elsewhere.

The class intentionally implements only the small ``RandomState`` surface used
by the model (binomial, poisson, multinomial, and random).  Array-valued draws
are evaluated in explicit C order.  This makes the mapping from a tensor cell
to a counter stable and reviewable instead of depending on numpy's internal
vectorised-distribution draw consumption.

**Every sampler here must be a monotone function of its keyed uniforms.**  That
is what makes common random numbers work: perturb a parameter slightly between
two arms and the draw must move slightly, not onto a fresh random path.  Two
couplings enforce it (A-28, promoted from A-24's measured counterfactuals):

* ``multinomial`` is sequential conditional binomials through the same keyed
  inverse CDF used by ``binomial``.  NumPy's ``Generator.multinomial`` uses
  rejection sampling and is *not* monotone in ``pvals``; a one-person change in
  the migration weights moved the allocation by up to 188 people (A-24).
* Adult background mortality is drawn at the *group* level and split across
  tensor cells by a sequential hypergeometric inverse CDF, so people whom an
  intervention merely relocates between cells are not re-randomised.

Both preserve the exact joint distribution; the proof is a permanent test,
``tests/test_a28_coupled_samplers.py``.  Deterministic mode never constructs
this class.
"""

from __future__ import annotations

import hashlib
import pickle
from typing import Any

import numpy as np
from scipy.special import bdtrik, gammaln, pdtrik


_KEY_VERSION = "hiv-prep-crn-v1"

_TINY = np.nextafter(0.0, 1.0)

#: Process whose cellwise binomial draws are replaced by the group-level
#: coupling of A-28 §3.2.  This is the process that the intervention moves
#: people *between cells* of, without changing anything about those people.
_GROUPED_BINOMIAL_PROCESS = "adult_background_mortality"

# Frozen stream labels.  These strings enter the Philox counter, so renaming any
# of them changes every affected draw in the project and invalidates the
# reproduction of A-24's counterfactuals required by A-28 §4.4.  They keep their
# original A-24 spelling deliberately.
_COUPLED_MULTINOMIAL_LABEL = "a24_coupled"
_GROUP_TOTAL_LABEL = "a24_group_total"
_GROUP_CELL_LABEL = "a24_cell"


def _key_words(*parts: Any) -> np.ndarray:
    """Return stable uint32 words for the model's ordered primitive key tuples.

    Pickle protocol 5 has a specified binary representation for the Python
    primitives and ordered tuples used by every model call site.  Serialising
    the complete tuple in C is materially faster than recursively assembling
    millions of transmission keys in Python, while remaining independent of
    Python's process-randomised ``hash()``.
    """
    payload = pickle.dumps(
        (_KEY_VERSION, *parts), protocol=5, fix_imports=False
    )
    digest = hashlib.blake2b(payload, digest_size=32).digest()
    return np.frombuffer(digest, dtype="<u4").copy()


def _binomial_inverse(uniforms, n, p) -> np.ndarray:
    """Exact discrete binomial quantile evaluated on already-drawn uniforms.

    ``ceil(bdtrik(...))`` is the discrete binomial quantile used by
    ``scipy.stats.binom.ppf``.  Calling the underlying vectorised ufunc avoids
    the large high-level scipy.stats overhead in scalar strata.  Degenerate
    distributions are handled explicitly because bdtrik returns NaN for n == 0
    and p == 0.

    This is the single inverse-CDF binomial path in this module: ``binomial``'s
    array branch and the coupled multinomial's conditional binomials both go
    through it, so they share one monotone mapping from uniform to count.
    """
    n_arr, p_arr = np.broadcast_arrays(np.asarray(n), np.asarray(p, dtype=float))
    uniforms = np.broadcast_to(
        np.maximum(np.asarray(uniforms, dtype=float), _TINY), n_arr.shape
    )
    result = np.empty(n_arr.shape, dtype=np.int64)
    zero = (n_arr == 0) | (p_arr == 0)
    certain = (~zero) & (p_arr == 1)
    regular = ~(zero | certain)
    result[zero] = 0
    result[certain] = n_arr[certain]
    if np.any(regular):
        result[regular] = np.ceil(
            bdtrik(uniforms[regular], n_arr[regular], p_arr[regular])
        ).astype(np.int64)
    return result


def _coupled_multinomial(n: int, pvals: np.ndarray, uniforms) -> np.ndarray:
    """Multinomial(n, pvals) as sequential conditional binomials (A-28 §3.1).

    One uniform per category, each consumed by the exact inverse CDF in
    ``_binomial_inverse``.  The distribution is identical to
    ``Multinomial(n, pvals)``; unlike NumPy's rejection sampler, the result is
    monotone in ``pvals``, so a small between-arm weight perturbation produces a
    small allocation change instead of a fresh draw.  The total is exactly ``n``
    by construction: the last category absorbs whatever remains.
    """
    p = np.asarray(pvals, dtype=float)
    n_categories = len(p)
    counts = np.zeros(n_categories, dtype=np.int64)
    remaining_n = int(n)
    if remaining_n < 0:
        # ``Generator.multinomial`` raised here; without this the last category
        # would silently absorb a negative count.
        raise ValueError(f"n must be non-negative, got {remaining_n}")
    remaining_p = 1.0
    for index in range(n_categories - 1):
        if remaining_n <= 0:
            break
        conditional_p = (
            0.0
            if remaining_p <= 0
            else min(max(p[index] / remaining_p, 0.0), 1.0)
        )
        drawn = int(
            _binomial_inverse(
                np.asarray(uniforms[index]),
                np.asarray(remaining_n),
                np.asarray(conditional_p),
            )
        )
        counts[index] = drawn
        remaining_n -= drawn
        remaining_p -= p[index]
    counts[n_categories - 1] += remaining_n
    return counts


def _hypergeometric_inverse(u: float, M: int, n: int, N: int) -> int:
    """Inverse CDF of Hypergeometric(M population, n marked, N draws) at ``u``.

    The pmf is evaluated in log space so that the population sizes carried by
    this model (tens of millions) do not overflow.
    """
    low, high = max(0, n + N - M), min(n, N)
    if low == high:
        return low
    support = np.arange(low, high + 1)
    log_pmf = (
        gammaln(n + 1)
        - gammaln(support + 1)
        - gammaln(n - support + 1)
        + gammaln(M - n + 1)
        - gammaln(N - support + 1)
        - gammaln(M - n - N + support + 1)
    )
    pmf = np.exp(log_pmf - log_pmf.max())
    cdf = np.cumsum(pmf)
    index = int(np.searchsorted(cdf, u * cdf[-1], side="left"))
    return int(support[min(index, len(support) - 1)])


class CounterBasedRNG:
    """Small Philox-backed RNG facade scoped to one process and timestep."""

    def __init__(
        self,
        seed: int,
        year: float,
        timestep: int,
        process_id: Any,
        stratum_prefix: Any = (),
        _stream_bank: dict[str, Any] | None = None,
    ) -> None:
        self.seed = int(seed)
        self.year = float(year)
        self.timestep = int(timestep)
        self.process_id = process_id
        self.stratum_prefix = stratum_prefix
        self._call_index = 0
        if _stream_bank is None:
            # One Philox key identifies the process stream.  Individual draws
            # reset the four-word Philox counter below, which is substantially
            # faster than constructing a new bit generator for every stratum.
            process_words = _key_words(seed, year, timestep, process_id)
            process_key = process_words.view("<u8")[:2]
            bit_generator = np.random.Philox(key=process_key)
            _stream_bank = {
                "bit_generator": bit_generator,
                "generator": np.random.Generator(bit_generator),
                "state": bit_generator.state,
            }
        self._stream_bank = _stream_bank

    def for_stratum(self, stratum_id: Any) -> "CounterBasedRNG":
        """Return an independently keyed child stream for a named stratum."""
        return CounterBasedRNG(
            seed=self.seed,
            year=self.year,
            timestep=self.timestep,
            process_id=self.process_id,
            stratum_prefix=(*self.stratum_prefix, stratum_id),
            _stream_bank=self._stream_bank,
        )

    def _reserve_call(self, distribution: str, stratum_id: Any = None) -> tuple:
        call_index = self._call_index
        self._call_index += 1
        return (distribution, call_index, stratum_id)

    def _generator(self, draw_key: Any) -> np.random.Generator:
        """Reset and return the shared Philox engine at ``draw_key``'s counter."""
        counter = _key_words(self.stratum_prefix, draw_key).view("<u8")
        bit_generator = self._stream_bank["bit_generator"]
        state = self._stream_bank["state"]
        np.copyto(state["state"]["counter"], counter)
        state["buffer"].fill(0)
        state["buffer_pos"] = 4
        state["has_uint32"] = 0
        state["uinteger"] = 0
        bit_generator.state = state
        return self._stream_bank["generator"]

    @staticmethod
    def _scalar_or_array(values: np.ndarray):
        return values.item() if values.ndim == 0 else values

    def _uniforms(self, shape: tuple, call_key: Any) -> np.ndarray:
        """Generate one stable uniform per coordinate, vectorising common axes.

        HTA arms can differ in the final PrEP dimension (for example one versus
        two states).  Keying each final-axis slice separately keeps every shared
        coordinate aligned when that dimension expands, while generating the
        common leading tensor in one fast Philox call.  Distribution inversion
        below then consumes exactly one uniform per stratum, independent of the
        population count or event probability in that stratum.
        """
        if not shape:
            return np.asarray(self._generator((call_key, ())).random())
        if len(shape) == 1:
            return self._generator((call_key, "vector")).random(shape)

        result = np.empty(shape, dtype=float)
        leading_shape = shape[:-1]
        for last_index in range(shape[-1]):
            result[..., last_index] = self._generator(
                (call_key, ("last_axis", last_index))
            ).random(leading_shape)
        return result

    def random(self, size=None, stratum_id: Any = None):
        call_key = self._reserve_call("random", stratum_id)
        if size is None:
            return float(self._generator((call_key, ())).random())
        return self._uniforms(tuple(np.empty(size).shape), call_key)

    def _grouped_binomial(self, call_key: Any, n: np.ndarray, p: float) -> np.ndarray:
        """Group-level coupling of independent Binomial(n_cell, p) (A-28 §3.2).

        Same joint law as drawing each cell independently: the group total
        ``D ~ Binomial(sum(n), p)`` is drawn by inverse CDF on one keyed
        uniform, then split across cells in C order by sequential hypergeometric
        inverse CDF on per-cell keyed uniforms.  Per-cell keying uses the same
        final-axis slicing as ``_uniforms``, so cells shared between two arms
        share their uniform.

        Keying the *cells* alone, as the per-cell binomial did, re-randomises
        everybody the intervention relocates between cells even though nothing
        about those people changed.  Coupling the total removes that channel.
        """
        total = int(n.sum())
        group_uniform = max(
            float(self._generator((call_key, (_GROUP_TOTAL_LABEL,))).random()), _TINY
        )
        if total == 0 or p == 0.0:
            return np.zeros(n.shape, dtype=np.int64)
        deaths = int(np.ceil(bdtrik(group_uniform, total, p))) if p < 1.0 else total

        cell_uniforms = np.asarray(
            self._uniforms(n.shape, (call_key, _GROUP_CELL_LABEL))
        )
        out = np.zeros(n.shape, dtype=np.int64)
        flat_n = n.ravel()
        flat_u = np.maximum(cell_uniforms.ravel(), _TINY)
        flat_out = out.reshape(-1)
        remaining_pop, remaining_deaths = total, deaths
        for index in np.flatnonzero(flat_n):
            if remaining_deaths == 0:
                break
            cell_n = int(flat_n[index])
            if remaining_pop == cell_n:
                # Last populated cell: it must absorb every remaining death.
                flat_out[index] = remaining_deaths
                remaining_deaths = 0
                break
            drawn = _hypergeometric_inverse(
                float(flat_u[index]), remaining_pop, cell_n, remaining_deaths
            )
            flat_out[index] = drawn
            remaining_deaths -= drawn
            remaining_pop -= cell_n
        if remaining_deaths != 0:
            raise AssertionError(
                "grouped background-mortality coupling failed to allocate all deaths"
            )
        return out

    def binomial(self, n, p, size=None, stratum_id: Any = None):
        if (
            self.process_id == _GROUPED_BINOMIAL_PROCESS
            and size is None
            and np.ndim(n) >= 2
            and np.isscalar(p)
        ):
            call_key = self._reserve_call("binomial", stratum_id)
            return self._grouped_binomial(
                call_key, np.asarray(n, dtype=np.int64), float(p)
            )

        call_key = self._reserve_call("binomial", stratum_id)
        if size is None and np.isscalar(n) and np.isscalar(p):
            n_value = int(n)
            p_value = float(p)
            if n_value == 0 or p_value == 0.0:
                return 0
            if p_value == 1.0:
                return n_value
            uniform = max(
                float(self._generator((call_key, ())).random()),
                np.nextafter(0.0, 1.0),
            )
            return int(np.ceil(bdtrik(uniform, n_value, p_value)))

        if size is not None:
            n_arr, p_arr = np.broadcast_arrays(
                np.broadcast_to(n, size),
                np.broadcast_to(p, size),
            )
        else:
            n_arr, p_arr = np.broadcast_arrays(np.asarray(n), np.asarray(p))

        if n_arr.ndim == 0:
            n_value = int(n_arr)
            p_value = float(p_arr)
            if n_value == 0 or p_value == 0.0:
                return 0
            if p_value == 1.0:
                return n_value
            uniform = max(
                float(self._uniforms((), call_key)), np.nextafter(0.0, 1.0)
            )
            return int(np.ceil(bdtrik(uniform, n_value, p_value)))

        uniforms = np.maximum(
            self._uniforms(n_arr.shape, call_key), np.nextafter(0.0, 1.0)
        )
        return self._scalar_or_array(_binomial_inverse(uniforms, n_arr, p_arr))

    def poisson(self, lam=1.0, size=None, stratum_id: Any = None):
        call_key = self._reserve_call("poisson", stratum_id)
        if size is None and np.isscalar(lam):
            lam_value = float(lam)
            uniform = max(
                float(self._generator((call_key, ())).random()),
                np.nextafter(0.0, 1.0),
            )
            return int(np.ceil(pdtrik(uniform, lam_value)))

        if size is not None:
            lam_arr = np.broadcast_to(lam, size)
        else:
            lam_arr = np.asarray(lam)

        if lam_arr.ndim == 0:
            lam_value = float(lam_arr)
            uniform = max(
                float(self._uniforms((), call_key)), np.nextafter(0.0, 1.0)
            )
            return int(np.ceil(pdtrik(uniform, lam_value)))

        uniforms = np.maximum(
            self._uniforms(lam_arr.shape, call_key), np.nextafter(0.0, 1.0)
        )
        result = np.ceil(pdtrik(uniforms, lam_arr)).astype(np.int64)
        return self._scalar_or_array(result)

    def multinomial(self, n, pvals, size=None, stratum_id: Any = None):
        """Coupled inverse-CDF multinomial (A-28 §3.1).

        ``Generator.multinomial`` draws sequential conditional binomials by
        *rejection*, so its output is not monotone in ``pvals`` and the arms
        desynchronise on a hair-width weight difference.  This uses the same
        sequential-conditional-binomial decomposition through the keyed inverse
        CDF instead: same keys, same uniforms, same distribution, monotone.
        """
        call_key = self._reserve_call("multinomial", stratum_id)
        if size is not None:
            raise NotImplementedError(
                "size != None multinomial is not used by the model"
            )
        p = np.asarray(pvals, dtype=float)
        uniforms = self._generator(
            (call_key, (_COUPLED_MULTINOMIAL_LABEL,))
        ).random(len(p))
        return _coupled_multinomial(int(n), p, uniforms)


class CounterBasedRNGFactory:
    """Construct process-scoped CRN streams for one simulation seed."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)

    def for_process(
        self,
        *,
        year: float,
        timestep: int,
        process_id: Any,
    ) -> CounterBasedRNG:
        return CounterBasedRNG(
            seed=self.seed,
            year=year,
            timestep=timestep,
            process_id=process_id,
        )

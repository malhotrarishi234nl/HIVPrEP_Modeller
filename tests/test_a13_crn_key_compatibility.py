"""Compatibility pins for the A-4 counter-based RNG key contract.

These are deliberately golden-vector tests, not statistical tests.  A Python,
NumPy, or implementation upgrade that changes any vector must stop the suite:
accepting the new vector would change every downstream stochastic draw and
therefore requires an explicit reproducibility decision and evidence rerun.
"""

import hashlib
import pickle

import numpy as np

from counter_based_rng import CounterBasedRNG, _key_words


KEY_PARTS = (42, 2025.0, 200, "transmission", ((1, 2), "stage"))
KEY_TUPLE = ("hiv-prep-crn-v1", *KEY_PARTS)
PICKLE5_HEX = (
    "8005954200000000000000288c0f6869762d707265702d63726e2d7631944b2a"
    "47409fa400000000004bc88c0c7472616e736d697373696f6e944b014b0286948c"
    "05737461676594869474942e"
)
BLAKE2B_256_HEX = (
    "645f7acb175cbf83a51445575ca49804ab89bcaf3e6432ba739fda2997c15271"
)
KEY_WORDS_U32_LE = [
    3413794660,
    2210356247,
    1464145061,
    77112412,
    2948368811,
    3123864638,
    702193523,
    1901248919,
]
FIRST_UNIFORMS_F64_BITS = [
    0x3FD7F2551010F18A,
    0x3FEA3314F04E207D,
    0x3FDD22EAAD2D5DD8,
    0x3FD4147ECF977A04,
    0x3FE0C5C3EDA65711,
    0x3FD2CCAB9620650A,
    0x3FE72EA129023C85,
    0x3FED8A4A81886AC8,
]


def test_pickle_protocol_5_key_payload_is_pinned():
    payload = pickle.dumps(KEY_TUPLE, protocol=5, fix_imports=False)
    assert payload.hex() == PICKLE5_HEX, (
        "The protocol-5 CRN key payload changed. This would change every keyed "
        "draw; do not update the golden vector without an explicit compatibility "
        "decision and rerunning all stochastic evidence."
    )


def test_blake2b_256_key_digest_is_pinned():
    payload = bytes.fromhex(PICKLE5_HEX)
    digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
    assert digest == BLAKE2B_256_HEX, (
        "The BLAKE2b-256 CRN key digest changed. This is a stochastic-stream "
        "compatibility break, not a baseline to refresh mechanically."
    )


def test_production_key_words_pin_digest_and_little_endian_conversion():
    assert _key_words(*KEY_PARTS).tolist() == KEY_WORDS_U32_LE, (
        "Production CRN key words no longer match the protocol-5/BLAKE2b-256 "
        "golden vector. Every stochastic stream must be treated as changed."
    )


def test_numpy_philox_uniform_stream_is_pinned_at_the_key_boundary():
    draws = CounterBasedRNG(42, 2025.0, 200, "transmission").random(size=8)
    actual_bits = draws.astype("<f8", copy=False).view("<u8").tolist()
    assert actual_bits == FIRST_UNIFORMS_F64_BITS, (
        "NumPy no longer maps the pinned key/counter to the same float64 uniform "
        "stream. This changes downstream draws and requires a full stochastic "
        "evidence rerun before acceptance."
    )

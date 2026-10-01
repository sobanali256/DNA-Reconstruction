"""Statistical tests of the IDS channel (gate G6.7): observed rates match the configured ones.

Fixed seeds, so the tests are deterministic. One error type at a time, so each rate is
measured exactly (no alignment): tolerance is about 5 standard errors.
"""

import math
from collections import Counter

import numpy as np
from scipy.stats import chisquare

from dnarecon.simulator import ids_read, random_strand, shorten_homopolymers, simulate

N_READS, LENGTH, P = 2000, 110, 0.04


def reads(p_ins=0.0, p_del=0.0, p_sub=0.0, strand=None):
    rng = np.random.default_rng(7)
    strand = strand or random_strand(rng, LENGTH)
    return strand, [ids_read(strand, rng, p_ins, p_del, p_sub) for _ in range(N_READS)]


def close(observed, p, slots):
    return abs(observed - p) < 5 * math.sqrt(p * (1 - p) / slots)


def test_substitution_rate_and_uniform_other_base():
    strand, out = reads(p_sub=P, strand="A" * LENGTH)
    assert all(len(r) == LENGTH for r in out)
    subs = Counter(b for r in out for b in r if b != "A")
    assert close(sum(subs.values()) / (N_READS * LENGTH), P, N_READS * LENGTH)
    assert set(subs) == set("CGT") and chisquare(list(subs.values())).pvalue > 0.001


def test_deletion_rate():
    strand, out = reads(p_del=P)
    deleted = sum(LENGTH - len(r) for r in out)
    assert close(deleted / (N_READS * LENGTH), P, N_READS * LENGTH)


def test_insertion_rate_and_uniform_base():
    strand, out = reads(p_ins=P, strand="A" * LENGTH)
    slots = N_READS * (LENGTH + 1)
    assert close(sum(len(r) - LENGTH for r in out) / slots, P, slots)
    inserted = Counter(b for r in out for b in r if b != "A")  # 3/4 of insertions are visible
    assert chisquare(list(inserted.values())).pvalue > 0.001


def test_no_errors_copies_the_strand():
    strand, out = reads()
    assert set(out) == {strand}


def test_homopolymer_shortening_rate():
    rng = np.random.default_rng(3)
    strand = "ACGTTTTACGAAAAAC"  # two runs of >= 4 (TTTT, AAAAA)
    out = [shorten_homopolymers(strand, 0.5, rng) for _ in range(N_READS)]
    shortened = sum("TTTT" not in r for r in out) + sum("AAAAA" not in r for r in out)
    assert close(shortened / (2 * N_READS), 0.5, 2 * N_READS)
    assert all(len(strand) - len(r) in (0, 1, 2) for r in out)
    assert shorten_homopolymers("ACCCGT", 1.0, rng) == "ACCCGT"  # runs < 4 never change


def test_simulate_is_reproducible_with_exact_coverage_and_separate_splits():
    args = dict(n_clusters=5, coverage=7, length=50, p_ins=0.02, p_del=0.02, p_sub=0.02,
                hp_shorten=0.0, seed=1)
    dev = simulate("c", "dev", **args)
    assert dev == simulate("c", "dev", **args)
    assert all(r.coverage == 7 and len(r.original_sequence) == 50 for r in dev)
    assert [r.cluster_id for r in dev][:2] == ["syn-c-dev-0001", "syn-c-dev-0002"]
    assert dev[0].dataset_id == "synthetic_c" and dev[0].error_profile["p_sub"] == 0.02
    test = simulate("c", "test", **args)
    assert {r.original_sequence for r in dev}.isdisjoint(r.original_sequence for r in test)

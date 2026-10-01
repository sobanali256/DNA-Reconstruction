"""Synthetic clusters from an IDS (insertion/deletion/substitution) channel.

Frozen channel spec (configs/dataset_synthetic.yaml holds the numbers):
  * Original strand: `length` uniform i.i.d. bases from ACGT. Every cluster has exactly
    `coverage` reads, each drawn independently from the original.
  * Homopolymer step (only when hp_shorten > 0), applied first: every run of >= HP_MIN_RUN
    equal bases loses one base with probability hp_shorten, independently per run and read.
  * IDS channel, left to right over the (possibly shortened) strand: before each base,
    insert a uniform random base with probability p_ins; then delete the base with
    probability p_del, or substitute it with a uniformly chosen different base with
    probability p_sub, or copy it (deletion and substitution are mutually exclusive).
    One more insertion slot follows the last base, so a read has length + 1 slots.
This is the channel of the ITR paper's simulator (Sabary et al. 2024) except that there a
deleted base can still be substituted; here each probability is the actual event rate.
Ground truth (the original strand) is stored for evaluation only.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

from dnarecon.models import ClusterRecord

BASES = np.array(list("ACGT"))
HP_MIN_RUN = 4
_RUNS = re.compile(r"A+|C+|G+|T+")


def random_strand(rng: np.random.Generator, length: int) -> str:
    return "".join(rng.choice(BASES, length))


def shorten_homopolymers(strand: str, q: float, rng: np.random.Generator) -> str:
    """Drop one base from each run of >= HP_MIN_RUN equal bases with probability q."""
    return "".join(run[:-1] if len(run) >= HP_MIN_RUN and rng.random() < q else run
                   for run in (m.group() for m in _RUNS.finditer(strand)))


def ids_read(strand: str, rng: np.random.Generator, p_ins: float, p_del: float, p_sub: float) -> str:
    """One read of `strand` through the IDS channel (spec in the module docstring)."""
    n = len(strand)
    inserted = np.where(rng.random(n + 1) < p_ins, rng.choice(BASES, n + 1), "")
    u = rng.random(n)
    source = np.array(list(strand))
    # A substitute is source + 1..3 positions around ACGT: uniform over the other 3 bases.
    shifted = BASES[(np.searchsorted(BASES, source) + rng.integers(1, 4, n)) % 4]
    kept = np.where(u < p_del, "", np.where(u < p_del + p_sub, shifted, source))
    return "".join(np.stack([inserted[:n], kept], axis=1).ravel()) + inserted[n]


def simulate(condition: str, split: str, n_clusters: int, coverage: int, length: int,
             p_ins: float, p_del: float, p_sub: float, hp_shorten: float, seed: int) -> list[ClusterRecord]:
    """`n_clusters` clusters of one condition and split, from their own random stream."""
    rng = np.random.default_rng([zlib.crc32(f"{condition}/{split}".encode()), seed])
    profile = {"p_ins": p_ins, "p_del": p_del, "p_sub": p_sub, "hp_shorten": hp_shorten}
    records = []
    for i in range(n_clusters):
        strand = random_strand(rng, length)
        reads = tuple(ids_read(shorten_homopolymers(strand, hp_shorten, rng) if hp_shorten else strand,
                               rng, p_ins, p_del, p_sub) for _ in range(coverage))
        records.append(ClusterRecord(f"syn-{condition}-{split}-{i + 1:04d}", reads, length,
                                     f"synthetic_{condition}", strand, error_profile=profile))
    return records

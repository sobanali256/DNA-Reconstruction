"""Dataset loading, validation, eligibility and the dev/test split.

Eligibility rule (pre-registered, see docs/data_policy.md):
  * Every cluster in the source dataset is eligible for evaluation and stays in the
    denominator of every method.
  * An empty cluster (no reads) is not sent to BBS or ITR. It is recorded as a failure
    for every method with failure_reason "empty_cluster", and the count is reported.
  * Clusters are never filtered on anything derived from ground truth. Malformed
    clusters (see the Microsoft README note of 8/12/2024) are therefore kept as they are.
  * A read with characters outside A/C/G/T is a data error: loading stops with a
    message rather than silently dropping or repairing the read.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dnarecon.formats import read_centers, read_microsoft_clusters
from dnarecon.models import ClusterRecord

DNA_ALPHABET = frozenset("ACGT")

EMPTY_CLUSTER = "empty_cluster"
SPLIT_DEV = "dev"
SPLIT_TEST = "test"


def validate_cluster(record: ClusterRecord) -> list[str]:
    """Return a list of problems with a cluster record; an empty list means valid.

    An empty cluster is valid here: it is a property of the data handled by the
    eligibility rule, not a formatting error.
    """
    problems = []
    if not record.cluster_id:
        problems.append("missing cluster_id")
    if record.expected_length <= 0:
        problems.append(f"expected_length must be > 0, got {record.expected_length}")
    for i, read in enumerate(record.reads):
        if not read:
            problems.append(f"read {i} is empty")
        elif not set(read) <= DNA_ALPHABET:
            bad = "".join(sorted(set(read) - DNA_ALPHABET))
            problems.append(f"read {i} has characters outside ACGT: {bad!r}")
    if record.original_sequence is not None and not set(record.original_sequence) <= DNA_ALPHABET:
        problems.append("original_sequence has characters outside ACGT")
    return problems


def microsoft_cluster_id(index: int) -> str:
    """Stable ID for the cluster at 0-based position `index` (1-based in the ID)."""
    return f"cnr-{index + 1:05d}"


def load_microsoft(
    clusters_path: str | Path,
    centers_path: str | Path,
    expected_length: int,
    dataset_id: str = "microsoft_cnr",
) -> list[ClusterRecord]:
    """Load the Microsoft CNR dataset as validated ClusterRecords, in file order."""
    clusters = read_microsoft_clusters(clusters_path)
    centers = read_centers(centers_path)
    if len(clusters) != len(centers):
        raise ValueError(f"{len(clusters)} clusters but {len(centers)} centers")

    records = []
    problems = []
    for index, (reads, center) in enumerate(zip(clusters, centers)):
        record = ClusterRecord(
            cluster_id=microsoft_cluster_id(index),
            reads=tuple(reads),
            expected_length=expected_length,
            dataset_id=dataset_id,
            original_sequence=center,
        )
        problems.extend(f"{record.cluster_id}: {p}" for p in validate_cluster(record))
        records.append(record)

    ids = [r.cluster_id for r in records]
    if len(set(ids)) != len(ids):
        problems.append("duplicate cluster IDs")
    if problems:
        shown = "\n  ".join(problems[:20])
        raise ValueError(f"{len(problems)} dataset problems, first ones:\n  {shown}")
    return records


def assign_split(cluster_ids: list[str], dev_fraction: float, seed: int) -> dict[str, str]:
    """Randomly assign each cluster to dev or test with a fixed seed.

    The dev set has round(dev_fraction * N) clusters. The result depends only on the
    list of IDs (in order), dev_fraction and seed.
    """
    if not 0 < dev_fraction < 1:
        raise ValueError(f"dev_fraction must be in (0, 1), got {dev_fraction}")
    n_dev = round(dev_fraction * len(cluster_ids))
    order = np.random.default_rng(seed).permutation(len(cluster_ids))
    dev_positions = set(order[:n_dev].tolist())
    return {
        cid: SPLIT_DEV if i in dev_positions else SPLIT_TEST
        for i, cid in enumerate(cluster_ids)
    }


@dataclass(frozen=True)
class SplitRow:
    cluster_id: str
    split: str
    n_reads: int
    eligible: bool
    engine_input: bool
    note: str


SPLIT_COLUMNS = ["cluster_id", "split", "n_reads", "eligible", "engine_input", "note"]


def build_split_table(records: list[ClusterRecord], dev_fraction: float, seed: int) -> list[SplitRow]:
    """Apply the eligibility rule and the split to every record."""
    splits = assign_split([r.cluster_id for r in records], dev_fraction, seed)
    return [
        SplitRow(
            cluster_id=r.cluster_id,
            split=splits[r.cluster_id],
            n_reads=r.coverage,
            eligible=True,
            engine_input=not r.is_empty,
            note=EMPTY_CLUSTER if r.is_empty else "",
        )
        for r in records
    ]


def write_split_table(rows: list[SplitRow], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SPLIT_COLUMNS)
        for row in rows:
            writer.writerow([row.cluster_id, row.split, row.n_reads,
                             int(row.eligible), int(row.engine_input), row.note])


def read_split_table(path: str | Path) -> dict[str, str]:
    """Return {cluster_id: split} from a committed split file."""
    with open(path, newline="") as handle:
        return {row["cluster_id"]: row["split"] for row in csv.DictReader(handle)}


# Normalized cluster records as JSON Lines: one ClusterRecord per line. Used for fixtures
# and for any dataset converted to our own format.
def save_records_jsonl(records: list[ClusterRecord], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="ascii") as handle:
        for r in records:
            handle.write(json.dumps({
                "cluster_id": r.cluster_id,
                "dataset_id": r.dataset_id,
                "expected_length": r.expected_length,
                "reads": list(r.reads),
                "original_sequence": r.original_sequence,
            }) + "\n")


def load_records_jsonl(path: str | Path) -> list[ClusterRecord]:
    """Load and validate records written by save_records_jsonl."""
    records, problems = [], []
    with open(path, encoding="ascii") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            record = ClusterRecord(
                cluster_id=d["cluster_id"],
                reads=tuple(d["reads"]),
                expected_length=int(d["expected_length"]),
                dataset_id=d["dataset_id"],
                original_sequence=d.get("original_sequence"),
            )
            problems.extend(f"{record.cluster_id}: {p}" for p in validate_cluster(record))
            records.append(record)
    if len({r.cluster_id for r in records}) != len(records):
        problems.append("duplicate cluster IDs")
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems[:20]))
    return records

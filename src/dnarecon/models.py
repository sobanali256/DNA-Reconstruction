"""Core data records shared by the dataset loader, adapters and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClusterRecord:
    """One cluster of noisy reads, normalized across datasets (design doc v3, section 3.1).

    `original_sequence` is ground truth. It is used only by the metrics code and must
    never be passed to BBS, ITR, the router or the final selector.
    """

    cluster_id: str
    reads: tuple[str, ...]
    expected_length: int
    dataset_id: str
    original_sequence: str | None = None
    error_profile: dict[str, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def coverage(self) -> int:
        """Number of reads in the cluster."""
        return len(self.reads)

    @property
    def is_empty(self) -> bool:
        return not self.reads


@dataclass(frozen=True)
class ResultRecord:
    """One row of results/<run_id>/per_cluster.csv (design doc v3, section 3.4, plus extras).

    Built from engine outputs only (dnarecon.results.build_record); the quality columns stay
    None until dnarecon.results.attach_metrics adds them after reconstruction. The true
    strand itself is never stored here. None is written as a blank cell.

    method: bbs_only | itr_only | adaptive. Scheduler, worker count and threshold are
    run-level settings and live in the run manifest.
    bbs_status: ok | empty_cluster | shard_failed | not_run.
    itr_status: the ITR adapter's status (ok | single_read | error | timeout | crashed),
    or task_failed (the whole ITR task failed) | empty_cluster | not_routed | not_run.
    status: ok when there is a final sequence, failed otherwise (see failure_reason).
    """

    run_id: str
    split: str
    cluster_id: str
    dataset_id: str
    eligible_for_eval: bool
    method: str
    coverage: int
    expected_length: int
    error_profile: dict[str, float] | None
    # BBS
    bbs_status: str
    bbs_shard_id: str
    bbs_sequence: str | None
    bbs_k: int | None
    bbs_path_weight: float | None
    bbs_confidence: float | None
    # ITR
    routed_to_itr: bool
    itr_status: str
    itr_task_id: str
    worker_id: int | None  # ITR worker that ran the cluster
    itr_sequence: str | None
    itr_failed: bool | None  # None when ITR did not run on this cluster
    itr_reads_used: int | None
    itr_runtime_ms: float | None
    # Final result
    final_sequence: str | None
    final_algorithm: str  # bbs | itr | none
    total_cluster_runtime_ms: float | None  # only when measurable: ITR-only runs
    status: str
    failure_reason: str
    # Quality (ground-truth based, added last)
    exact_match: bool | None = None
    edit_distance: int | None = None
    normalized_edit_distance: float | None = None
    hamming_distance: int | None = None
    bbs_exact_match: bool | None = None
    bbs_edit_distance: int | None = None
    bbs_hamming_distance: int | None = None
    itr_exact_match: bool | None = None
    itr_edit_distance: int | None = None
    itr_hamming_distance: int | None = None

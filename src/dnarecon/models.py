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

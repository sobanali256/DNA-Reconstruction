"""Confidence router for the adaptive pipeline (design doc v3, Phase 3; gates G3.1-G3.3).

A cluster is routed to ITR iff its BBS confidence is below τ, or BBS gave it no output
(failed BBS shard), which counts as confidence 0, the least confident possible (decided
2 Oct 2026: the fallback covers primary failures, as BBS covers ITR failures). Empty
clusters have no reads and are never given to the router. No learned routing.
The router sees only cluster IDs and BBS confidences: it imports nothing that carries
reads or ground truth, so it cannot use them (G3.2 by construction).
"""

from __future__ import annotations

from typing import Mapping


def route(confidences: Mapping[str, float | None], tau: float) -> list[str]:
    """IDs to send to ITR, in input order: confidence < tau, with None (no BBS output) as 0.

    The caller passes non-empty clusters only. τ = 0 routes nothing (not even clusters
    without BBS output: τ = 0 is the BBS-only policy); τ = 1 routes everything below 1.
    """
    if not 0 <= tau <= 1:
        raise ValueError(f"tau must be in [0, 1], got {tau}")
    return [cid for cid, conf in confidences.items() if (0.0 if conf is None else conf) < tau]

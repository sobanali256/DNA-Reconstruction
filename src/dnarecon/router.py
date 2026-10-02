"""Confidence router for the adaptive pipeline (design doc v3, Phase 3; gates G3.1-G3.3).

A cluster is routed to ITR iff its BBS confidence is below τ. No learned routing.
The router sees only cluster IDs and BBS confidences: it imports nothing that carries
reads or ground truth, so it cannot use them (G3.2 by construction).
"""

from __future__ import annotations

from typing import Mapping


def route(confidences: Mapping[str, float | None], tau: float) -> list[str]:
    """IDs to send to ITR, in input order: confidence < tau.

    None (no BBS output: empty cluster or failed BBS shard) is never routed, since there is
    no confidence to compare. τ = 0 routes nothing; τ = 1 routes everything below 1.
    """
    if not 0 <= tau <= 1:
        raise ValueError(f"tau must be in [0, 1], got {tau}")
    return [cid for cid, conf in confidences.items() if conf is not None and conf < tau]

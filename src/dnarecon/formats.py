"""Readers and writers for the on-disk cluster formats.

Microsoft CNR format (Clusters.txt): each cluster starts with a separator line of
'=' characters, followed by one read per line. Clusters are in the same order as the
strands in Centers.txt, and a cluster may be empty (two separators in a row).
"""

from __future__ import annotations

from pathlib import Path

# Same rule as BBS (`-s` default): a line starting with "===" starts a new cluster.
MICROSOFT_SEPARATOR = "==="


def read_microsoft_clusters(path: str | Path) -> list[list[str]]:
    """Return the reads of every cluster, in file order, keeping empty clusters.

    Blank lines are ignored and reads are stripped of surrounding whitespace. Lines
    before the first separator are an error, because they belong to no cluster.
    """
    clusters: list[list[str]] = []
    current: list[str] | None = None
    with open(path, encoding="ascii") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if text.startswith(MICROSOFT_SEPARATOR):
                if current is not None:
                    clusters.append(current)
                current = []
            elif text:
                if current is None:
                    raise ValueError(f"{path}:{line_no}: read before the first cluster separator")
                current.append(text)
    if current is not None:
        clusters.append(current)
    return clusters


def read_centers(path: str | Path) -> list[str]:
    """Return the ground-truth strands, one per non-blank line."""
    with open(path, encoding="ascii") as handle:
        return [line.strip() for line in handle if line.strip()]

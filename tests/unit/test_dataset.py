"""Unit tests for the Microsoft format parser, validation, eligibility and split."""

from pathlib import Path

import pytest

from dnarecon.dataset import (
    EMPTY_CLUSTER,
    assign_split,
    build_split_table,
    load_microsoft,
    microsoft_cluster_id,
    validate_cluster,
)
from dnarecon.formats import read_microsoft_clusters
from dnarecon.models import ClusterRecord

SEP = "=" * 31


def write(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def test_parser_keeps_empty_clusters_in_order(tmp_path):
    path = write(tmp_path, "c.txt", [SEP, "ACGT", "ACG", SEP, SEP, "", "TTTT", SEP])
    assert read_microsoft_clusters(path) == [["ACGT", "ACG"], [], ["TTTT"], []]


def test_parser_rejects_reads_before_first_separator(tmp_path):
    path = write(tmp_path, "c.txt", ["ACGT", SEP, "ACGT"])
    with pytest.raises(ValueError, match="before the first cluster separator"):
        read_microsoft_clusters(path)


def test_parser_separator_needs_three_equals(tmp_path):
    # Same rule as BBS: "===" starts a cluster. The real file uses 31 '='.
    path = write(tmp_path, "c.txt", ["===", "ACGT", "===", "GG"])
    assert read_microsoft_clusters(path) == [["ACGT"], ["GG"]]


def record(reads, length=4, original=None, cid="c1"):
    return ClusterRecord(cid, tuple(reads), length, "test", original_sequence=original)


def test_validate_accepts_good_and_empty_clusters():
    assert validate_cluster(record(["ACGT", "ACG"], original="ACGT")) == []
    assert validate_cluster(record([])) == []


@pytest.mark.parametrize(
    "rec, message",
    [
        (record(["ACNT"]), "outside ACGT"),
        (record(["acgt"]), "outside ACGT"),
        (record([""]), "is empty"),
        (record(["ACGT"], length=0), "expected_length"),
        (record(["ACGT"], cid=""), "missing cluster_id"),
        (record(["ACGT"], original="ACGU"), "original_sequence"),
    ],
)
def test_validate_rejects_malformed_records(rec, message):
    problems = validate_cluster(rec)
    assert any(message in p for p in problems), problems


def test_load_microsoft_ids_and_ground_truth(tmp_path):
    clusters = write(tmp_path, "Clusters.txt", [SEP, "ACGT", SEP, SEP, "ACCT"])
    centers = write(tmp_path, "Centers.txt", ["ACGT", "GGGG", "ACCT"])
    records = load_microsoft(clusters, centers, expected_length=4)
    assert [r.cluster_id for r in records] == ["cnr-00001", "cnr-00002", "cnr-00003"]
    assert [r.coverage for r in records] == [1, 0, 1]
    assert records[1].original_sequence == "GGGG"


def test_load_microsoft_count_mismatch(tmp_path):
    clusters = write(tmp_path, "Clusters.txt", [SEP, "ACGT"])
    centers = write(tmp_path, "Centers.txt", ["ACGT", "GGGG"])
    with pytest.raises(ValueError, match="1 clusters but 2 centers"):
        load_microsoft(clusters, centers, expected_length=4)


def test_load_microsoft_bad_read_stops_loading(tmp_path):
    clusters = write(tmp_path, "Clusters.txt", [SEP, "ACXT"])
    centers = write(tmp_path, "Centers.txt", ["ACGT"])
    with pytest.raises(ValueError, match="cnr-00001: read 0 has characters outside ACGT"):
        load_microsoft(clusters, centers, expected_length=4)


def test_split_is_deterministic_and_sized():
    ids = [microsoft_cluster_id(i) for i in range(1000)]
    a = assign_split(ids, 0.3, seed=7)
    assert a == assign_split(ids, 0.3, seed=7)
    assert sum(v == "dev" for v in a.values()) == 300
    assert a != assign_split(ids, 0.3, seed=8)


def test_split_rejects_bad_fraction():
    with pytest.raises(ValueError):
        assign_split(["a", "b"], 1.0, seed=1)


def test_empty_clusters_stay_eligible_but_skip_engines():
    records = [record(["ACGT"], cid="a"), record([], cid="b")]
    rows = {r.cluster_id: r for r in build_split_table(records, 0.5, seed=1)}
    assert rows["a"].eligible and rows["a"].engine_input and rows["a"].note == ""
    assert rows["b"].eligible and not rows["b"].engine_input and rows["b"].note == EMPTY_CLUSTER


def test_jsonl_round_trip_and_validation(tmp_path):
    from dnarecon.dataset import load_records_jsonl, save_records_jsonl
    records = [ClusterRecord("a", ("ACGT", "ACG"), 4, "t", "ACGT"), ClusterRecord("b", (), 4, "t", "CCCC")]
    path = tmp_path / "r.jsonl"
    save_records_jsonl(records, path)
    assert load_records_jsonl(path) == records
    path.write_text(path.read_text().replace('"ACG"', '"ACGN"'))
    with pytest.raises(ValueError, match="outside ACGT"):
        load_records_jsonl(path)

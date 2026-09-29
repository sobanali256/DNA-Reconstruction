"""Unit tests for the BBS adapter: input writing, command, CSV parsing and row mapping."""

from pathlib import Path

import pytest

from dnarecon.bbs_adapter import (
    BbsRunError,
    BbsSettings,
    build_command,
    parse_output,
    run_shard,
    write_microsoft_input,
)
from dnarecon.formats import read_microsoft_clusters
from dnarecon.models import ClusterRecord

HEADER = "read_id,reconstruction_result,k,path_weight,confidence"


def record(cid: str, reads: tuple[str, ...], truth: str | None = None) -> ClusterRecord:
    return ClusterRecord(cid, reads, expected_length=4, dataset_id="t", original_sequence=truth)


def write_csv(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "out.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_input_round_trips_and_has_no_ground_truth(tmp_path):
    records = [record("a", ("ACGT", "ACG"), truth="GGGG"), record("b", ("TTTT",), truth="CCCC")]
    path = tmp_path / "in.txt"
    write_microsoft_input(records, path)
    assert read_microsoft_clusters(path) == [["ACGT", "ACG"], ["TTTT"]]
    text = path.read_text()
    assert "GGGG" not in text and "CCCC" not in text


def test_command_always_passes_threads_and_algorithm_flags():
    cmd = build_command(BbsSettings(Path("bbs")), Path("in"), Path("out"), length=110, threads=2)
    pairs = dict(zip(cmd[2::2], cmd[3::2]))  # cmd = [binary, input, flag, value, ...]
    assert cmd[:2] == ["bbs", "in"]
    assert pairs == {"--format": "microsoft", "-l": "110", "-b": "20", "-k": "4", "-K": "62",
                     "-a": "1", "-t": "2", "-o": "out"}


def test_parse_maps_rows_by_position(tmp_path):
    path = write_csv(tmp_path, [HEADER, "1,ACGT,5,-1.2345,0.999000", "2,,4,0.0000,0.000000"])
    res = parse_output(path, ["x", "y"])
    assert [r.cluster_id for r in res] == ["x", "y"]
    assert res[0].sequence == "ACGT" and res[0].k == 5 and res[0].confidence == pytest.approx(0.999)
    assert res[1].sequence == ""  # BBS found no candidate: kept, scored as a failure later


@pytest.mark.parametrize("lines, message", [
    ([HEADER, "1,ACGT,5,0,1"], "1 rows for 2 clusters"),
    (["id,seq", "1,A", "2,C"], "unexpected header"),
    ([HEADER, "1,ACGT,5,0,1", "3,ACGT,5,0,1"], "out of order"),
])
def test_parse_rejects_mismatches(tmp_path, lines, message):
    with pytest.raises(BbsRunError, match=message):
        parse_output(write_csv(tmp_path, lines), ["x", "y"])


def test_run_shard_rejects_empty_clusters_and_bad_threads(tmp_path):
    settings = BbsSettings(Path("bbs"))
    with pytest.raises(ValueError, match="empty clusters"):
        run_shard([record("a", ())], settings, length=4, threads=1, workdir=tmp_path, shard_id="s")
    with pytest.raises(ValueError, match="threads"):
        run_shard([record("a", ("ACGT",))], settings, length=4, threads=0, workdir=tmp_path, shard_id="s")

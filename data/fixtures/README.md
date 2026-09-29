# Test fixtures

`microsoft_dev10.jsonl`: 10 clusters from the **dev** split of the Microsoft Clustered
Nanopore Reads dataset (https://github.com/microsoft/clustered-nanopore-reads-dataset,
MIT License, Copyright (c) Microsoft Corporation), one `ClusterRecord` per line
(`dnarecon.dataset.load_records_jsonl`). Built by `scripts/make_fixture.py`, which
selects by read count only: one 1-read cluster, one 2-read cluster, the two largest
clusters (> 25 reads) and six ordinary ones. `original_sequence` is for evaluation only.

# Data policy: eligibility and the dev/test split

Pre-registered on 28 Sep 2026, before any reconstruction results were looked at.
Implemented in `src/dnarecon/dataset.py`; configuration in `configs/dataset_microsoft.yaml`.

## Microsoft CNR dataset (as downloaded)

| Property | Value |
|---|---|
| Clusters | 10,000 (one per strand in `Centers.txt`, same order) |
| Strand length | 110 for every strand |
| Reads | 269,709; all use only A/C/G/T |
| Empty clusters | 16 (`cnr-09643` … `cnr-09991`, all in the last 400 positions) |
| Clusters with one read | 12 |
| Reads per cluster | median 21, mean 27, max 164; 3,931 clusters have more than 25 |

Cluster IDs are `cnr-00001` … `cnr-10000`: the 1-based position in the file.

**Known caveat** (dataset README, note of 8/12/2024): the 10,000 strands were not truly
uniformly random because of a generation error, so the clustering step may have produced
some malformed clusters (for example reads that belong to a different strand). This makes
reconstruction harder for those clusters. We report this caveat and do not try to repair it.

## Eligibility rule

1. **Every cluster is eligible.** All 10,000 clusters are in the denominator of every
   method's exact-match rate.
2. **Empty clusters** are not sent to BBS or ITR. Each one is recorded as a failure for
   every method (`failure_reason = empty_cluster`), and the count is reported separately
   (dev 7, test 9).
3. **No filtering on anything derived from ground truth.** Detecting malformed clusters
   would need the true strand, which would be an oracle, so malformed clusters are kept
   as they are.
4. **Invalid characters** (anything outside A/C/G/T) stop loading with an error instead of
   being dropped or repaired. None occur in this dataset.
5. **Read limits are the engines' own defaults.** BBS uses every read. Upstream ITR uses
   the first 25 reads of a cluster in file order (`maxCopies = 25`). This affects the 3,931
   clusters with more than 25 reads, and is reported as part of the ITR baseline.

## Split

- 30% dev / 70% test, uniform random, `numpy.random.default_rng(20260928)`.
  Result: 3,000 dev / 7,000 test clusters.
- Committed as `data/splits/microsoft_cnr_split.csv`
  (`cluster_id, split, n_reads, eligible, engine_input, note`).
  `scripts/make_split.py configs/dataset_microsoft.yaml --check` confirms it reproduces
  byte for byte.
- The pilot (1,000 clusters) is drawn from **dev only**. τ is chosen on **dev only**.
  Test-split results may be cached, but no analysis that picks τ or any other setting may
  read them.

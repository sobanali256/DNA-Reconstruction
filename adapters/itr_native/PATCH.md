# ITR wrapper: what differs from upstream

`itr_cli.cpp` replaces upstream `DNA.cpp`'s `main()` and links every other upstream object
file (`Clone`, `Cluster2`, `CommonSubstring2`, `DividerBMA`, `EditDistance`, `LCS2`,
`LongestPath`) unchanged, built with upstream's flags. Upstream source is never committed
(licence TBA); `scripts/setup_external.sh` builds the wrapper against the pinned clone.

## Unchanged (the algorithm)

- The reconstruction call: `Cluster2::TestBest` with upstream's parameters
  (`delPatternLen = 3`, sub/del/ins priorities `0`, `maxReps = 2`).
- Read cap: the first `maxCopies = 25` reads in input order (`GetCaseWithCopiesLimit`).
- A cluster with exactly one read returns that read unchanged.

## Changed (I/O and randomness only)

1. **No ground truth in the input.** Upstream reads the true strand and uses it only for
   its length (`FinalGuess(..., original.size())`; see `docs/upstream_notes.md`). The
   wrapper receives `expected_length` and passes a placeholder of that many `A`s.
2. **Per-cluster seeding.** Upstream seeds one `mt19937` from the clock and shares it
   across all clusters in a file. The wrapper creates a new generator for every cluster:
   `mixed = FNV-1a-64(cluster_id) XOR seed`, then
   `mt19937(seed_seq{low 32 bits of mixed, high 32 bits of mixed})`. `seed` comes from
   `configs/itr.yaml`. Each cluster's result is therefore reproducible and independent of
   batch size, batch order and which worker runs it.
3. **Nothing is computed against the original after reconstruction.** Upstream draws from
   the shared generator while comparing each guess with the true strand, so one cluster's
   ground truth shifts the random state of the next. The wrapper has no such step.
4. **Empty clusters** (not handled upstream) return status `empty_cluster` instead of
   running the algorithm. The pipeline never sends them anyway (`docs/data_policy.md`).
5. **I/O format**: batch input on stdin or a file, one TSV row per cluster with status and
   per-cluster runtime (`steady_clock`, reconstruction only). See the header of
   `itr_cli.cpp`.

## Verification

`scripts/check_itr_wrapper.py` builds a copy of upstream `DNA.cpp` whose only change is
that the clock seed is replaced by the wrapper's per-cluster seed, runs each sampled dev
cluster through it alone, and checks:

- wrapper output == upstream output given the **true** original (shows change 1 does not
  alter results: gate G2.3),
- upstream output is unchanged when the original is replaced by random letters of the same
  length (G2.3 from the upstream side),
- wrapper output is unchanged by batch order and batch size.

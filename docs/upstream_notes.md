# Upstream code audit

What we found by reading the pinned BBS and ITR sources (28 Sep 2026). Line numbers refer
to the pinned commits in `SOURCE_VERSIONS.md`.

## ITR (`omersabary/Reconstruction`, folder `Iterative/`)

### Entry point: `DNA.cpp`

- `main(argc, argv)`: `argv[1]` = input file, `argv[2]` = output directory. It counts
  clusters by lines starting with `*` and then calls `TestFromFileCaseRange(..., 0, testNum,
  150, maxCopies = 25, delPatternLen = 3, sub/del/insPriority = 0, maxReps = 2)`.
- **The hard-coded `150`** is the `strandLen` parameter. `TestFromFileCaseRange` never uses
  it (it uses `original.length()` instead), so it has no effect.
- Input per cluster: line 1 = original strand, line 2 = `*****` (skipped), then reads, and
  the cluster ends after **two** blank lines.
- Only the **first 25 reads in file order** are kept (`GetCaseWithCopiesLimit`). The
  length-based `BestNCopies` selector exists but is not called.
- A cluster with exactly one read returns that read unchanged. A cluster with zero reads
  is not handled (untested; `TestBest` would work on an empty clone list), so our wrapper
  must reject empty clusters.
- Upstream prints "Success rate" as `exact / (N + 1)` because of the `startCase = 0`
  convention. We never use upstream statistics; our metrics are computed in Python.

### Does ITR use the original strand to reconstruct? (gate G2.3)

`Cluster2(orig, copies)` stores the original strand, and each `Clone` stores it too.
Every use was traced:

| Use | Where | Affects the reconstruction? |
|---|---|---|
| `original.size()`, passed to `FinalGuess` as `correctSize` | `Cluster2::TestBest` (line 1848) | **Yes: length only.** The final guess prefers candidates of exactly this length. |
| `Clone::UpdateInsertDelete` (LCS vs original → `insertNum`, `deleteNum`) | `Clone.cpp:81` | No. Those counts are read only by `TestInsertGuesses`/`TestDeletionGuesses`, which are diagnostics and never called from `TestBest`. |
| `Stats4(..., original, ...)` | `TestFixAll*` variants | No. Not on the `TestBest` path. |
| Edit distance to the original, histograms | `DNA.cpp`, after reconstruction | No (and it draws nothing from the generator; see Randomness). |
| `ComputeEditDistancePriority(original, clone)` | `Cluster2::TestSubstitutionGuesses` (`Cluster2.cpp:1072`) | No. Diagnostic with no callers. |

**Conclusion:** the reconstruction depends on the original only through its **length**.
A placeholder of the expected length (for example 110 × `A`) is enough, and the length is
already an allowed input (BBS needs it too; see the known-length assumption in the design
doc). The wrapper passes only the expected length. **Confirmed on 29 Sep 2026**
(`scripts/check_itr_wrapper.py`): on 45 dev clusters, upstream gives the same output
with the true strand and with random letters of the same length, and the wrapper
(placeholder of `A`s) matches both.

### Randomness (corrected 29 Sep 2026)

- One `mt19937` generator is seeded from the clock once per run and passed down through
  the whole reconstruction.
- **With upstream's settings it is never drawn from.** The only draws on the
  reconstruction path are in `ComputeEditDistancePriority` when `priority == 6`
  (`BacktrackEditDistanceRandom`, `shuffle(priorities)`, `EditDistance.cpp`). Upstream
  `main` fixes sub/del/ins priorities to `0`, which select the deterministic
  `BacktrackEditDistancePriority`. The other draws (`MakeStrand`, `CopyStrand`, the
  `shuffle` at `Cluster2.cpp:29`) are in synthetic-data code that the file path never runs.
- So **upstream ITR is deterministic** on file input. Measured: on 150 dev clusters the
  wrapper's output is identical under two different seeds.
- Our Day 1 note that upstream leaks one cluster's ground truth into the next through the
  generator was **wrong**: the post-cluster `ComputeEditDistancePriority(finalGuess,
  original, 0, generator)` call uses priority `0` and draws nothing.
- The wrapper still creates a per-cluster generator from `(seed, cluster ID)`. It costs
  nothing and guarantees reproducibility and batch independence (gate G5.7) even if a
  non-zero priority is ever used. It must not be described as removing a leak.

### Build

`g++ -std=c++0x -O3 -g3 -Wall`, all `.cpp` files. `DividerBMA.cpp` gives 31 warnings;
upstream's `makefile` forgets to link `DividerBMA.o`, so we link the objects directly.

### Speed on this laptop

Smoke run, first 20 Microsoft clusters, upstream binary: about 0.9 s per cluster
(18 s total, one core). The pilot will measure this properly.

## BBS (`GZHoffie/bbs`)

- CLI: `bbs <files> -l <length> -o out.csv -t <threads>`; `-b` beam width (default 20),
  `-k/-K` k range (4/62), `-s` separator (default `===`), `--format microsoft`.
- **Threads:** `-t` defaults to all logical CPUs. The pipeline spawns `-t` worker threads
  (`consensus.rs`, `run_pipeline`). Always pass `-t`.
- **Empty clusters in CSV mode produce no row, and `read_id` counts only non-empty
  clusters** (`consensus.rs`, `run_pipeline`). A trailing empty cluster is dropped
  silently. So `read_id` is **not** the cluster's position in the file whenever an earlier
  cluster is empty. The adapter must write its own input containing only non-empty
  clusters and map CSV rows back by order, checking that the row count matches.
- Results are reordered to input order before writing, so the order is deterministic
  whatever the thread count.
- Confidence values from the pinned commit differ from the README example (cluster 1:
  0.999959 now vs 0.909951 in the README). The README example was probably produced by an
  older version (the Rust rewrite and k_max fix came later); not investigated further.

### BBS is not fully deterministic (found 29 Sep 2026)

`find_consensus` (`markov_chain.rs`) keeps candidate scores in a `std::collections::HashMap`,
whose iteration order is randomised per process. When two or more candidates tie for the
best score, `max_by` picks whichever comes last in that order, so the **sequence** can
change between identical runs. The score set does not change, so **`k`, `path_weight` and
`confidence` are identical** across runs.

Measured on the 2,993 non-empty dev clusters, 4 runs (adapter at `-t 4` twice, `-t 1`,
and BBS directly on the raw file at `-t 4`): 15 clusters (0.5%) changed sequence, all with
confidence ≤ 0.5 (exactly 0.5 or 0.25 = a 2- or 4-way tie). Exact matches: 2,879–2,882.
Thread count is not the cause; the process's hash seed is.

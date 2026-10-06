# Figure captions (draft)

Regenerate every figure: `.venv/bin/python scripts/make_figures.py configs/figures.yaml`.
Each figure reads only the CSVs named in `configs/figures.yaml` (`results/summary/`).
Colours and marker shapes follow the entity in every figure (BBS only: blue circle; full ITR:
orange square; primary cascade: green triangle; length-check cascade: yellow diamond), so the
figures also read in grayscale. Notes in brackets are for the author, not for the caption.

**Fig. f1 — Adaptive reconstruction pipeline.** (A) BBS reconstructs every cluster in batches
and reports a confidence c. (B) The router sends clusters with c < τ to ITR; the rest keep
their BBS result. (C) Routed clusters are processed in micro-batches of five on a pool of
worker processes (serial, static or dynamic scheduling). The selector keeps a successful ITR
result (length-check variant: only if its length equals the designed length) and otherwise
the BBS result. Ground truth is used only after the final selection, for evaluation. Blue
borders: BBS; orange: ITR.

**Fig. f2 — Exact-match rate per condition (test split).** Rows: Microsoft CNR (real Nanopore
reads), the pooled synthetic grid, and each synthetic condition labelled by total error rate ·
coverage (reads per cluster); "hp" = homopolymer bias (each run of ≥ 4 equal bases loses one
base with probability 0.5 per read). The grey bar spans the methods' range. Primary cascade
τ = 0.8, length check τ = 0.99 (both chosen on the dev split); on Microsoft the frozen τ is 0,
so the cascade equals BBS only and is not drawn separately. Synthetic: 700 clusters per
condition; Microsoft: 7,000 clusters (9 empty clusters counted as failures).

**Fig. f3 — Rescued and harmed clusters relative to BBS only (test split).** For each method,
the number of clusters it reconstructs exactly where BBS fails (rescued, right) and fails
where BBS is exact (harmed, left). Symmetric log scale, linear below 10 clusters. (a) Full ITR
on every cluster (this is the per-cluster BBS/ITR complementarity); (b) primary cascade, τ 0.8
(on Microsoft τ = 0 routes nothing); (c) length-check cascade, τ 0.99 (not run on Microsoft).
Rows as in Fig. f2. [Counts: test table `rescued_vs_bbs` / `harmed_vs_bbs`; cascades paired
with each live run's own BBS results.]

**Fig. f4 — How well BBS confidence predicts BBS failure (test split).** AUROC of
(1 − confidence) for "BBS output is not exact", with 95% bootstrap (BCa, 2,000 resamples)
confidence intervals. Not computable where BBS never fails; the 6%·10× condition has only six
failures, too few for an interval. The pooled synthetic value is high partly because confidence
separates easy from hard conditions; within hard conditions (coverage 5, 12% error) it is
much lower.

**Fig. f5 — Accuracy versus share of clusters routed to ITR.** Lines: the τ grid
{0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1} on the dev split, for both selectors; dashed lines:
BBS only and full ITR on dev. Hollow markers: the τ frozen on dev, applied to the test split
(synthetic: 0.8 primary, 0.99 length check; Microsoft: 0, i.e. BBS only). On Microsoft every
τ > 0 lowers the primary cascade's accuracy (ITR harms more clusters than it rescues).

**Fig. f6 — Parallel scaling of the adaptive pipeline (synthetic test split, τ 0.8, 2,813
clusters routed to ITR).** (a) Speedup of the whole run (BBS + routing + ITR) over the serial
run; (b) efficiency = speedup / workers. Lines: median of three repetitions; whiskers: min–max
(mostly hidden by the markers). The static curve's one-worker point is the serial run. Shaded:
more workers than the laptop's 4 physical cores (hyper-threading). Intel i5-10210U (15 W),
WSL2; processes not pinned to cores.

**Fig. f7 — Static versus dynamic scheduling of the routed ITR work.** (a) Makespan and
(b) load imbalance (maximum / mean worker busy time; 1.0 = perfectly balanced) for each
repetition (dots) with the median (bar). Static = seeded shuffle of micro-batches cut into
equal contiguous blocks; static LPT = longest-first by a label-free cost estimate; dynamic =
shared queue. Same workload as Fig. f6.

**Fig. f8 — Contention probe: per-process slowdown when N identical processes run at once.**
Median per-process time relative to one process (three repetitions). Compute kernel: integer
loop with a tiny memory footprint (slowed only by clock speed or by sharing a core); memory
kernel: repeated sums over a 256 MB array; ITR: the reconstruction wrapper on three
coverage-20 synthetic clusters. A slowdown equal to the number of copies means no throughput
gain. Diagnostic only, not a timing result. [Interpretation in the text is an inference: WSL
reports no clock frequencies and processes were not pinned.]

**Fig. f9 — Wall time versus accuracy on the synthetic test split (9,100 clusters).** Filled
markers: one worker (serial); hollow: four workers (dynamic). BBS only: the BBS stage of the
adaptive runs. Full ITR was timed on a different day than the cascades; the same routed
clusters ran about 6–7% slower that day, so the measured full-ITR/cascade ratio (9.5× serial,
9.7× on four workers) is about 9× after correction. The length-check cascade was not timed:
its point is BBS stage time plus the summed controlled ITR time of its 5,472 routed clusters,
taken from the serial full-ITR runs and rescaled to the cascade day by the primary cascade's
measured ratio (307.4 s measured vs 324.6 s for the same clusters on the full-ITR day), an
estimate of ≈ 1,050 s. Medians of three repetitions.

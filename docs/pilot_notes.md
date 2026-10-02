# Pilot notes (Microsoft CNR, 1,000 dev clusters)

Pilot sample: `data/splits/microsoft_pilot1000.csv` (1,000 random dev clusters, seed
20260930, drawn from the split file only; 2 empty clusters). Test split untouched.
Summary table: `results/summary/pilot_summary.csv`, produced by

```bash
.venv/bin/python scripts/run_experiment.py configs/pilot_bbs.yaml
.venv/bin/python scripts/run_experiment.py configs/pilot_itr.yaml
.venv/bin/python analysis/pilot_summary.py results/pilot_bbs-20260930-191013-r* \
    --itr results/pilot_itr-20260930-212355-r1
```

## Day 3 (30 Sep 2026): sanity check

Runs (code `9debb52`, clean tree, i5-10210U laptop on mains power, "Best performance"):
- BBS-only: `pilot_bbs-20260930-191013-r1` … `-r6` (4 threads, one shard). **r1 is the
  canonical cached run**; r2–r6 measure run-to-run variability.
- ITR-only: `pilot_itr-20260930-212355-r1` (one worker, micro-batches of 25, 60 s timeout).

All runs cover the same 1,000 cluster IDs (gate G3.5). The 2 empty clusters are
failures for both methods and stay in every denominator.

### Quality vs the BBS paper (Table 2, Microsoft CNR, all 10,000 clusters)

| | BBS (ours) | BBS (paper) | ITR (ours) | ITR (paper) |
|---|---|---|---|---|
| Exact match, all clusters | **96.70%** | 94.77% | **88.50%** | 87.58% |
| Mean edit distance, clusters with output | 0.115 | 0.168 | 0.189 | 0.232 |
| Mean Hamming, clusters with output | 1.08 | 1.62 | 4.43 | 4.80 |
| Mean edit distance, all clusters (failures = 110) | 0.335 | – | 0.409 | – |

**Verdict: matches.** The ranking (BBS clearly ahead of ITR), the size of the gap
(about 8 points exact match) and the Hamming pattern (ITR's errors are shift-type,
large Hamming for small edit distance) all reproduce. Our numbers are somewhat better
than the paper's for both tools. Likely reasons, none verified:
1. **Different cluster set.** The paper uses all 10,000 clusters; we use 1,000 dev
   clusters. The last 1,000 clusters of the file are smaller and hold all 16 empty
   clusters, so the full set is harder on average. The full dev split gave 95.97% for BBS
   (Day 2), also above 94.77%.
2. **Sampling noise.** With 1,000 clusters, the standard error of an exact-match rate
   near 96% is about 0.6 points, and near 88% about 1 point. ITR's difference (+0.9) is
   within that; BBS's (+1.9) is not.
3. **BBS version.** We pin one bug-fix commit after v0.2.0 (`SOURCE_VERSIONS.md`);
   the paper's exact version is not stated.
4. **Empty clusters.** The paper does not say how it counts them (at most 0.16 points).

Checking reason 1 directly would mean scoring BBS on the test clusters, which the
protocol forbids before τ is frozen, so it is not done.

### ITR time per cluster (this laptop, 1 worker)

| | Value |
|---|---|
| Mean / median / 95th percentile / max | 1.04 / 1.12 / 1.78 / 5.01 s |
| Stage wall time, 998 clusters | 1,041 s (17.3 min) |
| Timeouts / crashes / errors / failed tasks | 0 / 0 / 0 / 0 |
| Paper (i9-13900H) | 0.74 s/cluster |

The sum of per-cluster times (1,040.6 s) is within 0.4 s of the stage wall time, so
launching the wrapper once per micro-batch costs almost nothing.
The slowest cluster took 5 s, far below the 60 s timeout.
**Estimate for all 10,000 clusters:** about 2.9 h serial, about 45 min at 4 workers if
the speed-up were ideal (it will not be; the Week 3 scaling runs measure it).

### BBS time and run-to-run variability (6 runs)

- Stage wall time 1.4–3.2 s per 1,000 clusters (median 2.6 s) on 4 threads. The paper
  needed 20 s for 10,000 (about 2.0 s per 1,000) on an i9.
- **3 of 1,000 clusters (0.3%)** returned a different sequence in at least one repeat;
  all had confidence ≤ 0.27. k, path weight and confidence never changed. Exact matches
  ranged from 966 to 967. This agrees with Day 2 (0.5% on the full dev split) and does
  not affect any conclusion at this scale.

### Decisions / follow-ups
- Keep the ITR timeout at 60 s (max observed 5 s).
- Day 4: 2×2 table (does ITR fix the clusters BBS gets wrong?), confidence and
  path-weight AUROC, fallback benefit/harm at a range of τ computed from these cached
  runs, then go/no-go.

## Day 4 (1 Oct 2026): does ITR fix what BBS gets wrong? Go/no-go

Computed from the cached Day 3 runs only (no reruns, dev only), code `bad480b`:

```bash
.venv/bin/python analysis/cascade_from_cache.py configs/pilot_cascade.yaml          # ITR fallback
.venv/bin/python scripts/run_experiment.py configs/pilot_bbs_beam100.yaml           # wider-beam check
.venv/bin/python analysis/cascade_from_cache.py configs/pilot_cascade_beam100.yaml
```

Tables: `results/summary/pilot_cascade.csv`, `pilot_tau_sweep.csv`,
`pilot_beam100_cascade.csv`, `pilot_beam100_tau_sweep.csv`.

### 2×2 table (exact match, all 1,000 clusters)

| | ITR right | ITR wrong |
|---|---|---|
| **BBS right** | 881 | 86 |
| **BBS wrong** | **4 (rescuable)** | 29 (2 empty clusters) |

- **Rescuable share: 4/33 = 12.1%** (Wilson 95% CI 4.8–27.3%), below the PLAN.md
  guideline of about 15%, but not zero.
- On the 33 BBS failures ITR gets closer to the truth more often than not (edit distance
  lower in 19, equal in 10, higher in 4), but rarely exact.
- Oracle (pick whichever engine is right, using the truth): 97.1% vs BBS 96.7%. That is
  the most any selector could gain here: +0.4 points.

### Does BBS confidence find its own failures? (998 clusters with output, 31 failures)

| Score | AUROC | 95% CI (bootstrap BCa, 2,000) |
|---|---|---|
| Confidence | **0.907** | 0.787–0.958 |
| Path weight | 0.889 | 0.804–0.940 |

The BBS paper reports 0.85–0.87 on one dataset. The router signal works.

### τ sweep (route to ITR iff confidence < τ)

| τ | Routed | Exact | Rescued | Harmed | ITR time (s) |
|---|---|---|---|---|---|
| 0 (BBS only) | 0 | 96.7% | 0 | 0 | 0 |
| 0.5 | 12 | 96.6% | 2 | 3 | 1 |
| 0.8 | 58 | 95.1% | 2 | 18 | 29 |
| 0.9 | 87 | 94.3% | 2 | 26 | 45 |
| 0.99 | 310 | 90.7% | 4 | 64 | 251 |
| 1.0 | 996 | 88.5% | 4 | 86 | 1,039 |

With the default selection (ITR's answer replaces BBS's), **the cascade never beats BBS
alone**: at every τ > 0 ITR breaks more correct BBS answers than it rescues. The
label-free length-consistency selector (keep BBS when ITR's output length differs from
the designed length) removes most of the harm. It ends level with BBS for most τ and
is +1 cluster at best (τ = 0.5: 96.8%), which is within noise.

### Wider-beam BBS as the fallback (beam 100 vs default 20)

`pilot_bbs_beam100-20261001-193709-r1`: 966/1,000 exact. As a fallback it rescues
**0/33** BBS failures (30 unchanged). The one cluster it changes from right to wrong is
consistent with BBS's random tie-breaking (Day 2/3). A wider beam is not a useful fallback.

### Decision (1 Oct 2026): GO with ITR, framed around *when* the cascade helps

- Keep ITR as the fallback. Build the full Microsoft cache and the synthetic grid as
  planned.
- Report the Microsoft result honestly as a negative finding: confidence predicts BBS
  failure well (AUROC 0.91), but ITR rescues only 12% of failures and harms more than
  it rescues.
- The synthetic grid (3–12% error, coverage 5/10/20) tests whether ITR helps at higher
  error rates or lower coverage. Report the length-consistency selector alongside the
  default.
- The PDC study is unaffected: ITR's uneven per-cluster cost is the workload the
  scheduler needs.
- Not chosen: CPL (would need a new adapter; can be revisited if the synthetic grid also
  shows no rescue).

## Day 4 follow-up (1 Oct 2026): why ITR breaks correct clusters and rarely rescues

Exploratory, pilot only, same cached runs. Ground truth used for evaluation only:

```bash
.venv/bin/python analysis/itr_failure_modes.py configs/pilot_cascade.yaml
```

Table: `results/summary/pilot_itr_failure_modes.csv`. Clusters with reads (998) are
grouped by exact match. ITR errors are located by aligning ITR's output to the true strand
(edlib, global). A "long run" is a homopolymer of ≥ 4 equal bases; only **4.7%** of
true-strand positions lie in one.

| Group | n | Median reads | ITR wrong length | ITR one base short | ITR errors in long runs |
|---|---|---|---|---|---|
| BBS right, ITR right | 881 | 22 | 0% | 0 | – |
| **BBS right, ITR wrong (harmed)** | 86 | 19 | **93%** | **73** | **78%** |
| BBS wrong, ITR right (rescued) | 4 | 9 | 0% | 0 | – |
| BBS wrong, ITR wrong | 27 | **7** | 74% | 11 | 36% |

### Why ITR breaks clusters BBS gets right: homopolymer under-calling

- In the harmed clusters ITR is almost always off by one base (median edit distance 1).
  73 of 86 outputs are 109 bases instead of 110. The errors are mostly deletions (81 of
  103), and 78% of them sit in long homopolymer runs, about 17× the background rate.
- Reads at that run (67 harmed clusters where ITR's only error is one deletion in a long
  run):

  | Reads at the run | Harmed clusters (run where ITR drops a base) | Both right (first long run, 611 clusters) |
  |---|---|---|
  | Run shortened | **70%** | 25% |
  | Run correct | 27% | 70% |
  | Run lengthened | 3% | 5% |
  | Clusters where most reads shorten the run | **100%** | 7% |

- **Explanation:** Nanopore reads under-call long homopolymers. ITR assumes independent
  insertion/deletion/substitution errors and effectively follows the majority of reads,
  so when most reads drop a base in a run, so does ITR. BBS is given the designed length
  (`-l 110`), and none of its outputs in these clusters has the wrong length. Coverage is
  not the cause (median 19 reads), and neither is ITR's 25-read cap (it also happens at
  10–14 reads).
- **Consequence:** the harm is detectable without ground truth. 93% of harmed outputs
  have the wrong length, which is why the length-consistency selector removes most of
  the harm (Day 4 τ sweep).

### Why ITR rarely rescues BBS failures: too few reads

- The clusters both engines get wrong have a median of **7 reads** (vs 22 when both are
  right). 5 of the 27 have only 1–2 reads (the 2 empty clusters are already excluded).
  With so little information, neither engine can be expected to be exact.
- ITR often gets closer than BBS (lower edit distance on 19 of the 33 BBS failures, Day 4)
  but rarely exact: median edit distance 2, 74% with the wrong length. Its errors are
  enriched in long runs too (36% vs 4.7% background), so the homopolymer bias adds to
  the low coverage here.
- The 4 rescues have lower coverage than usual (median 9) and no length errors.

### What this means for the research

1. The cascade's failure on Microsoft data has a specific, testable cause:
   **sequencing-specific homopolymer bias, which violates ITR's independent-error
   assumption.** It is not a general weakness of the cascade idea.
2. **Hypothesis for the synthetic grid:** with independent, balanced IDS errors (the
   planned grid has no homopolymer bias) ITR should harm far less and may rescue at
   low coverage. Adding one synthetic condition with homopolymer-deletion bias would test
   the explanation directly (to decide in the Days 5–7 plan).
3. The length-consistency selector is the principled, label-free mitigation; report it
   alongside the default selector. ITR's algorithm is not modified (CLAUDE.md).
4. Limits: 1,000-cluster pilot only, 86 harmed / 27 both-wrong clusters; the read-level
   check covers 67 of 86 harmed clusters. Re-run on the full dev split when it is cached.

## Day 7 (2 Oct 2026): full Microsoft dev split (3,000 clusters)

The Day 4 analyses repeated on the full dev cache (no reruns, dev only):

```bash
.venv/bin/python analysis/cascade_from_cache.py configs/microsoft_dev_cascade.yaml
.venv/bin/python analysis/itr_failure_modes.py configs/microsoft_dev_cascade.yaml
```

Runs: `microsoft_dev_bbs-20261001-213122-r1` (BBS, 96.00% exact) and
`microsoft_dev_itr-20261001-213129-r1` (ITR, 87.83%), both code `21fee11`, clean tree.
Tables: `results/summary/microsoft_dev_cascade.csv`, `microsoft_dev_tau_sweep.csv`,
`microsoft_dev_itr_failure_modes.csv`. 7 empty clusters count as wrong for both.

Run checks (2 Oct): all 8 cache runs (Microsoft and synthetic, dev and test, BBS and ITR)
are complete, have one row per cluster of their split, and only empty clusters failed
(no timeouts or crashes; slowest ITR cluster 8.8 s). The two interrupted runs
(`microsoft_test_itr-20261001-232952-r1`, `synthetic_test_itr-20261002-142437-r1`) are
marked failed and not used.

### 2×2 table (exact match, all 3,000 clusters)

| | ITR right | ITR wrong |
|---|---|---|
| **BBS right** | 2,619 | 261 |
| **BBS wrong** | **16 (rescuable)** | 104 (7 empty clusters) |

- **Rescuable share: 16/120 = 13.3%** (Wilson 95% CI 8.4–20.6%), pilot 12.1%. Still
  below the ~15% guideline; the CI now excludes values above about 21%.
- On the 120 BBS failures ITR's edit distance is lower in 69, equal in 28, higher in 23.
- Oracle 96.53% vs BBS 96.00%: at most +0.5 points for any selector.

### Does BBS confidence find its own failures? (2,993 clusters with output, 113 failures)

| Score | AUROC | 95% CI (bootstrap BCa, 2,000) | Pilot |
|---|---|---|---|
| Confidence | **0.958** | 0.925–0.974 | 0.907 |
| Path weight | 0.882 | 0.839–0.915 | 0.889 |

Confidence is now clearly the better signal (the CIs no longer overlap much); it stays
the router score.

### τ sweep (route to ITR iff confidence < τ)

| τ | Routed | Default: exact | Rescued / harmed | Length check: exact | Rescued / harmed |
|---|---|---|---|---|---|
| 0 (BBS only) | 0 | 96.00% | 0 / 0 | 96.00% | 0 / 0 |
| 0.5 | 55 | 95.83% | 3 / 8 | 96.03% | 3 / 2 |
| 0.6 | 107 | 95.57% | 9 / 22 | **96.13%** | 9 / 5 |
| 0.7 | 144 | 95.10% | 10 / 37 | **96.13%** | 10 / 6 |
| 0.8 | 189 | 94.63% | 10 / 51 | 96.00% | 10 / 10 |
| 0.9 | 288 | 93.77% | 12 / 79 | 95.93% | 12 / 14 |
| 0.99 | 922 | 90.20% | 15 / 189 | 95.90% | 15 / 18 |
| 1.0 | 2,986 | 87.83% | 16 / 261 | 95.80% | 16 / 22 |

- **Default selector: the cascade never beats BBS alone** (same as the pilot).
- **Length-consistency selector: small gain at τ = 0.6–0.7** (+4 clusters, +0.13 points,
  routing 4–5% of clusters to ITR). This is the best the cascade does on Microsoft data;
  4 clusters out of 3,000 is within the size of BBS's own run-to-run variation (~0.5% of
  clusters change sequence), so treat it as "no harm", not as a real improvement.
- ITR seconds in the sweep CSV come from the cache run (mixed laptop speed, 1 Oct): cost
  estimate only, not a timing result.

### Failure modes (2,993 clusters with reads)

| Group | n | Median reads | ITR wrong length | ITR one base short | ITR errors in long runs |
|---|---|---|---|---|---|
| Both right | 2,619 | 22 | 0% | 0 | – |
| **Harmed** (BBS right, ITR wrong) | 261 | 19 | **92%** | **217** | **72%** |
| Rescued (BBS wrong, ITR right) | 16 | 10 | 0% | 0 | – |
| Both wrong | 97 | **7** | 77% | 40 | 23% |

Background: 4.65% of true-strand positions lie in runs ≥ 4. Harmed errors are mostly
deletions (258 of 322). Reads at the run where ITR's only error is one deletion (194
harmed clusters): 70% of reads shorten the run, and in **97%** of these clusters most
reads do (vs 25% / 6% at the first long run of 1,853 both-right clusters).

**Verdict: the pilot findings hold on 3× the data.** ITR's harm on Microsoft data is
homopolymer under-calling that follows the reads' majority; its few rescues need more
than the ~7 reads of the clusters both engines miss. The synthetic dev cache supports
this from the other side (dev exact per condition, from the cache runs): with independent errors
ITR beats BBS at low coverage / high error, and adding homopolymer shortening
(`e06_c10_hp50`) drops ITR from 100% to 58% while BBS keeps 84%. The per-condition
cascade analysis of the synthetic grid is the first Week 2 task.

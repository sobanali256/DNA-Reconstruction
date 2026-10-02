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

## Day 8 (2 Oct 2026): synthetic grid, per condition (dev, 13 × 300 clusters)

Same analyses as Day 7, per `dataset_id` plus a pooled group "all" (config key `group_by`):

```bash
.venv/bin/python analysis/cascade_from_cache.py configs/synthetic_dev_cascade.yaml
.venv/bin/python analysis/itr_failure_modes.py configs/synthetic_dev_cascade.yaml
```

Runs `synthetic_dev_bbs-20261001-223625-r1`, `synthetic_dev_itr-20261001-223644-r1`. Tables:
`results/summary/synthetic_dev_{cascade,tau_sweep,itr_failure_modes}.csv`. The Microsoft and
pilot tables regenerate byte-identical with the grouped code. AUROC is not computed for a
condition with fewer than 5 BBS failures (5 of 13 conditions); for `e03_c05` (8 failures)
the BCa interval is degenerate and left empty.

### 2×2 and confidence per condition

| Condition | BBS exact | ITR exact | Rescued / BBS wrong | Harmed | Confidence AUROC |
|---|---|---|---|---|---|
| e03_c05 | 97.3% | 95.3% | 6 / 8 | 12 | 0.977 |
| e03_c10, e03_c20, e06_c20 | 100% | 100% | – | 0 | – |
| e06_c05 | 64.7% | 89.7% | 86 / 106 (81%) | 11 | 0.826 |
| e06_c10 | 99.0% | 100% | 3 / 3 | 0 | – |
| **e06_c10_hp50** | 84.0% | 58.0% | **5 / 48 (10%)** | **83** | 0.869 |
| e09_c05 | 18.3% | 70.7% | 168 / 245 (69%) | 9 | 0.763 |
| e09_c10 | 84.3% | 99.7% | 46 / 47 (98%) | 0 | 0.841 |
| e09_c20 | 99.7% | 100% | 1 / 1 | 0 | – |
| e12_c05 | 3.3% | 39.7% | 115 / 290 (40%) | 4 | 0.727 |
| e12_c10 | 41.0% | 98.7% | 175 / 177 (99%) | 0 | 0.811 |
| e12_c20 | 94.3% | 100% | 17 / 17 | 0 | 0.915 |
| **Pooled** | 75.8% | 88.6% | **622 / 942 (66%, CI 63–69%)** | 119 | **0.944** (0.934–0.953) |

- With independent IDS errors **ITR rescues most BBS failures** (66% pooled, vs 13% on
  Microsoft), and almost all of them once coverage is ≥ 10. Rescue is limited only at
  coverage 5 with high error, where neither engine has enough reads.
- **The homopolymer condition reproduces the Microsoft pattern**: rescue 10%, ITR harms 83
  clusters; 98% of harmed outputs have the wrong length, 99% of their errors lie in long
  runs, and in 97% of the read-checked harmed clusters most reads shorten the run (Microsoft
  full dev: 92%, 72%, 97%). This is direct evidence for the Day 4 explanation.
- Outside `hp50`, harm happens only at coverage 5 (36 clusters), and 89–100% of those ITR
  outputs have the wrong length.
- Confidence predicts BBS failure well pooled (0.944) and in every condition with enough
  failures (0.73–0.98); it is weakest where errors are high and reads few.

### τ sweep (pooled exact rate, 3,900 clusters)

| τ | 0 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 0.95 | 0.99 | 1.0 |
|---|---|---|---|---|---|---|---|---|---|
| Routed | 0% | 17% | 23% | 27% | 31% | 38% | 45% | 61% | 100% |
| Default | 75.8% | 85.0% | 87.0% | 88.4% | 88.8% | **89.0%** | 88.8% | 88.5% | 88.6% |
| Length check | 75.8% | 85.5% | 87.9% | 89.4% | 90.1% | 90.9% | 91.2% | 91.4% | **91.6%** |

- **The cascade beats both engines alone on synthetic data**: default selector 89.0% at
  τ = 0.9 with 38% of clusters routed, vs BBS 75.8% and ITR-only 88.6%. Oracle 91.8%.
- **The length check removes almost all harm** (3 harmed clusters at τ = 1 vs 119) and
  reaches 91.6%, close to the oracle; in `hp50` it holds 84–85% at every τ where the default
  falls to 58%.
- Per condition the best τ differs widely (e.g. `e12_c10` wants τ = 1, `hp50` wants τ = 0):
  a global τ is a compromise, which is why per-condition τ is reported only as an oracle.

## Day 8: τ selection on dev (provisional until the methodology freeze)

```bash
.venv/bin/python analysis/select_tau.py configs/tau_selection.yaml
```

Rule (decided 2 Oct): per dataset family, the **cheapest τ in the grid whose pooled dev
exact rate is within 0.25 points of the best τ**; cost = share of clusters routed to ITR.
Default selector primary, length check secondary. Only dev runs are accepted (the script
reads each run's manifest and stops on anything else, gate G7.5). Table:
`results/summary/tau_selection.csv`.

| Family | Selector | τ | Dev exact | Routed | Best τ in grid | BBS only |
|---|---|---|---|---|---|---|
| **Synthetic (primary)** | default | **0.8** | 88.77% | 31% | 0.9 (89.00%, 38%) | 75.85% |
| Synthetic | length check | 0.99 | 91.38% | 61% | 1.0 (91.56%) | 75.85% |
| **Microsoft (primary)** | default | **0** | 96.00% | 0% | 0 | 96.00% |
| Microsoft | length check | 0 | 96.00% | 0% | 0.6 (96.13%) | 96.00% |

- On Microsoft the rule selects τ = 0: the deployable policy is BBS alone. That is the
  honest outcome of the Day 7 finding, not a failure of the rule.
- Per-condition (oracle) τ ranges from 0 (`hp50`, low-error conditions) to 1 (`e06_c10`,
  `e09_c10`, `e12_c10`, `e12_c20`). With 300 clusters per condition, 0.25 points is less than
  one cluster, so the oracle rule picks each condition's best τ.

## Day 9 (2 Oct 2026): live adaptive pipeline and scheduler checks

**Adaptive run = cache prediction.** `configs/synthetic_dev_adaptive.yaml` (τ 0.8, default
selector, serial ITR) ran live on all 3,900 synthetic dev clusters
(`synthetic_dev_adaptive-20261002-201111-r1`, code `04f84ce`): 1,226 clusters routed and
3,462 exact, both identical to the cached τ sweep. BBS confidences and every routed ITR
output are identical to the cache runs; 45 BBS sequences differ, all at confidence ≤ 0.5
(BBS tie-breaking), and no cluster changes its exact-match outcome.

**Gates.** Unit and integration tests (`tests/unit/test_router.py`, `test_scheduler.py`,
`test_runner.py`, `tests/integration/test_adaptive_real.py`, `test_scheduler_real.py`):
G3.1–G3.5 (router boundary, τ = 0/1, no ground truth in the router, every row has
routing and final algorithm, same cluster IDs as BBS-only), G5.1 (serial = dynamic with 1
worker), G5.2 (every task once), G5.3/G5.6 (configured concurrency reached, never
exceeded; peak recorded in every manifest), G5.4 (repeated dynamic and static schedules
give identical routing and ITR output), G5.5 (an invalid cluster fails only its task, a
huge one times out; all others complete). G5.7 from Day 2.

**Smoke campaign** (`configs/scaling_smoke.yaml`: 130 dev clusters, 47 routed; 1 warm-up
+ 9 cells × 3 repetitions, all complete, no timeouts, peak concurrency = workers in every
run). Machinery check only: with 47 clusters (10 micro-batches) the slowest single
micro-batch bounds the makespan, so speedups plateau near 1.7× from 2 workers on. Table:
`results/summary/scaling_smoke_cells.csv` (invocation `20261002-210900`, code `7c6853e`
after both code reviews; earlier smoke invocations are kept but not used).

## Day 10 (2 Oct 2026): test analysis plan and expectations, written before opening test

Written and committed before any test-split accuracy was computed. The final runs exist
(`final_microsoft_test_adaptive-20261002-232255-r1`, `final_synthetic_test_adaptive-
20261002-232315-r1`, `final_synthetic_test_adaptive_lengthcheck-20261002-232904-r1`, code
`307c39b`) and passed an integrity check that read no accuracy column.

**Analysis plan (fixed now).**
- Headline adaptive numbers = the live final runs. Baselines (BBS only, ITR only), 2×2,
  rescuable share, AUROC and τ sweep = the test caches (`microsoft_test_{bbs,itr}`,
  `synthetic_test_{bbs,itr}`). A per-cluster check confirms the live runs equal the cache
  prediction at the frozen τ and selector; any difference is reported, and the live run stays
  the headline.
- Exact rate with a 95% Wilson CI; empty clusters stay in the denominator as failures.
- Adaptive vs BBS only: exact McNemar test (two-sided binomial on the discordant clusters,
  rescued vs harmed), per family and per synthetic condition; per-condition p-values are not
  corrected for multiple comparisons and are labeled so.
- Mean normalized edit distance (no output = full-length error), plus the mean over clusters
  with output only.
- The test τ sweep is reported as post hoc, never used to choose τ (τ stays frozen).
- Dev and test are shown side by side.

**Expectations (from dev).**

| Family | BBS only | ITR only | Adaptive primary | Adaptive length check |
|---|---|---|---|---|
| Microsoft (τ 0) | 96.0% | 87.8% | = BBS only (routes nothing) | = BBS only |
| Synthetic pooled | 75.8% | 88.7% | 88.8% (τ 0.8, 31% routed) | 91.4% (τ 0.99, 61% routed) |

- Synthetic: ITR rescues about two thirds of BBS failures; confidence AUROC ≈ 0.94.
  Adaptive ≫ BBS only at low coverage / high error (e09_c05, e12_c05, e12_c10, e06_c05);
  equal at coverage 20 and at 3% error.
- `e06_c10_hp50`: the primary cascade is worse than BBS only (dev 74.3 vs 84.0, harmed 32,
  rescued 3); the length check removes most of the harm (dev 85.3).
- Microsoft: confidence AUROC ≈ 0.96; ITR rescues ≈ 13% of BBS failures and harms more;
  harm = homopolymer under-calling (ITR outputs of the wrong length).

## Day 10 (3 Oct 2026): test results (frozen τ, opened after the plan above was committed)

Code `30778bf`. `analysis/final_results.py configs/final/test_report.yaml` →
`results/summary/final_test_table.csv`, `final_live_vs_cache.csv`; `analysis/cascade_from_cache.py`
and `itr_failure_modes.py` on `configs/{microsoft,synthetic}_test_cascade.yaml` →
`results/summary/{microsoft,synthetic}_test_*.csv`.

**Live = cache.** All three final runs: routing, BBS confidence and every ITR output identical
to the cache prediction; no exact-match outcome differs (Microsoft 6,687 = 6,687; synthetic
8,085 = 8,085; length check 8,304 = 8,304). BBS sequences differ on 14 / 106 / 95 clusters,
all at confidence ≤ 0.5 (tie-breaking); they change 14 / 0 / 20 final sequences but no
exact-match outcome.

**Headline (exact match, 95% Wilson CI; McNemar vs BBS only).**

| Family | BBS only | ITR only | Adaptive primary | Adaptive length check | Dev (BBS / ITR / primary / LC) |
|---|---|---|---|---|---|
| Microsoft (7,000; 9 empty) | 95.53 [95.0, 96.0] | 87.27 [86.5, 88.0] | 95.53 (τ 0 = BBS only) | — | 96.00 / 87.83 / 96.00 / — |
| Synthetic pooled (9,100) | 76.30 | 88.68 [88.0, 89.3] | **88.85** [88.2, 89.5], 30.9% routed | **91.25** [90.7, 91.8], 60.1% routed | 75.85 / 88.74 / 88.77 / 91.38 |

- Pooled test numbers reproduce dev within half a point: τ was not over-fitted to dev. Per
  condition (300 dev clusters) the gaps are larger, e.g. e06_c05 primary 88.0 dev vs 83.6
  test, length check 93.3 vs 90.3; e06_c10_hp50 primary 74.3 vs 78.4.
  Microsoft BBS 95.53% vs the BBS paper's 94.77% on all 10,000 (ITR 87.27 vs 87.58).
- Synthetic primary vs BBS only: rescued 1,268, harmed 126 (p ≈ 1e-237). It matches ITR only
  (88.68) while routing 31% of clusters. Length check: rescued 1,381, harmed 20.
- Per condition (test, BBS → primary → length check): large gains where reads are few or
  noisy (e06_c05 65.9 → 83.6 → 90.3; e09_c05 16.9 → 65.7 → 70.0; e12_c05 2.4 → 41.0 → 42.0;
  e12_c10 44.0 → 91.3 → 98.0; e09_c10 82.9 → 97.3 → 99.7). 100% for every method at 3%
  error with coverage ≥ 10 and at coverage 20 up to 9% error; e12_c20 is 96.6 → 99.1 → 99.9
  (ITR only 100).
- **Where the primary cascade trails ITR only**, the cause is BBS failures with confidence
  ≥ τ: they are never routed, so ITR cannot fix them. Clusters with BBS confidence ≥ 0.8 that
  BBS gets wrong and ITR gets right (test): e12_c10 51 (cascade 91.3 vs ITR only 98.6),
  e09_c05 25 (65.7 vs 68.9), e06_c05 21 (83.6 vs 84.6), e09_c10 18 (97.3 vs 99.7),
  e12_c05 7, e12_c20 6; 135 pooled (203 confident BBS failures in all). Partly offset where
  confident BBS calls are right and ITR is wrong (e06_c05). Confidence separates failures
  worst at coverage 5 (AUROC 0.60–0.79) and at e12_c10 (0.78).
- **`e06_c10_hp50`** (homopolymer bias): primary cascade *harms*: 78.4 vs BBS 86.0
  (rescued 5, harmed 58, p ≈ 1.7e-12); ITR only 62.0. The length check removes the harm:
  86.9 (rescued 8, harmed 2, p 0.11, not significant). Harmed ITR outputs: 98.9% wrong length,
  98.6% of their errors in runs ≥ 4. Same as dev.
- **2×2.** Microsoft: ITR rescues 51 / 313 BBS failures = 16.3% [12.6, 20.8] (dev 13.3%),
  harms 629. Synthetic pooled: 1,403 / 2,157 = 65.0% [63.0, 67.0] (dev 66.0%), harms 276.
  Microsoft harm is again homopolymer under-calling: 92.9% of harmed ITR outputs have the
  wrong length, 65% of their errors in runs ≥ 4.
- **Confidence AUROC** (BBS failure): Microsoft 0.933 [0.912, 0.949] (dev 0.958; path weight
  0.879); synthetic pooled 0.947 [0.941, 0.953] (dev 0.944); per condition 0.60 (e12_c05) to
  0.96 (e03_c05); lowest at coverage 5 / high error.
- **Edit distance** (mean normalized, no output = 1): synthetic BBS-only failures are far
  from the truth, so the length check (keeps BBS when ITR's length is wrong) has the best
  exact rate but a higher mean normalized edit distance (0.0043) than the primary cascade
  (0.0027) or ITR only (0.0017). Report both: exact rate is the primary metric.
- **Code review (3 Oct) of the test analysis.** Live rows are now paired with the live run's
  own BBS results (rescued / harmed / McNemar), so BBS tie-breaking between two BBS runs never
  counts as a cascade effect: pooled counts unchanged, ±1 in e06_c05, e09_c05 and e12_c10.
  The Microsoft τ 0 run is BBS only (0 rescued, 0 harmed); its mean NED differs from the
  cache BBS-only row in the 6th digit because of the 14 tie clusters. P-values too small for
  a float are reported as `mcnemar_log10_p` (length check pooled: p ≈ 10^-377). The script
  now stops on mismatched inputs (split, dataset, engines) or a live-vs-cache routing / BBS
  confidence / ITR output difference. The per-cluster simulation moved to
  `metrics.cascade_frame` (one copy of the rule); every pilot, dev and test summary CSV
  regenerated byte-identical.
- **Post hoc test τ sweep (not used to choose τ).** Synthetic primary: 0.8 is the best τ on
  test too (88.85; 0.9 → 88.81). Microsoft default selector: τ 0 is best (any routing loses).
  Microsoft **length check**: τ 0.7–0.9 gives 95.73–95.76 vs 95.53 BBS only (+0.2 points,
  rescued 30–45 vs harmed 16–29); on dev the same policy was within noise (+4 clusters), so
  the frozen rule chose BBS only. Report as a post hoc observation, not as a result.

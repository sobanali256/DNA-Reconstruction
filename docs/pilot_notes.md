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

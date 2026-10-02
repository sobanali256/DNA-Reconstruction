# Project Plan

Agreed at kickoff on 28 Sep 2026. Solo project. See `CLAUDE.md` for decisions and rules.

## Core idea: run everything once, cache it, then query

1. Run **BBS and ITR on every cluster once** and store both outputs, BBS
   confidence/path weight/k, ITR runtime and status in one per-cluster table.
2. The adaptive pipeline's accuracy at any τ, the rescue/harm counts, the τ sweep
   (0.5–0.9) and the accuracy–runtime curves are then **pandas queries on that table**.
   Nothing is rerun.
3. **Only the parallel-timing experiments** (serial vs static vs dynamic at 1/2/4/8 workers)
   need live runs, on this laptop.

## Step 1: go/no-go pilot (Week 1)

The unknown that decides the project: **does ITR fix the clusters BBS gets wrong?**
(On real Nanopore data BBS beat ITR overall.)

- 1,000 random clusters from the **dev split** of the Microsoft CNR dataset.
- Sanity check against the BBS paper, Table 2 (Microsoft CNR: BBS 94.77%, ITR 87.58%,
  CPL 94.93% exact match; verified 28 Sep). If we are far off, fix the setup first.
- Build the 2×2 table and AUROC:

|              | ITR correct | ITR wrong |
|--------------|-------------|-----------|
| BBS correct  |             |           |
| BBS wrong    | **rescuable** |         |

  plus the AUROC of BBS confidence (and path weight) for predicting BBS failure.

**Reading the result:**
- Rescuable is a meaningful share of BBS failures (about ≥15%): **proceed as proposed**.
- Near zero on real data but not on synthetic data: proceed, and frame the paper around
  *when* the cascade helps. Still publishable.
- Near zero everywhere: keep the pipeline, router and scheduling; **swap the fallback**
  (candidates: CPL, which beat ITR in the BBS paper and has public code; or BBS rerun with a
  wider beam). The PDC contribution survives either way.

Record the decision and numbers in `docs/pilot_notes.md`.

## Repository structure (target)

```
DNA-Reconstruction/
├── CLAUDE.md                  # context for Claude Code sessions
├── README.md                  # scope, claims / non-claims, how to reproduce
├── SOURCE_VERSIONS.md         # pinned BBS + ITR commit hashes, licences
├── LICENSE                    # MIT for our own code
├── requirements.txt
├── configs/                   # everything that varies
│   ├── pilot.yaml
│   ├── synthetic_grid.yaml
│   ├── cache_runs.yaml
│   └── scaling.yaml
├── scripts/
│   ├── setup_external.sh      # clone BBS+ITR at pinned commits, apply patch, build
│   ├── download_microsoft.sh
│   ├── validate_environment.py
│   ├── run_pilot.py
│   ├── build_cache.py
│   ├── run_scaling.py
│   └── make_figures.py
├── adapters/itr_native/       # our only C++: thin ITR CLI + seed patch (no ITR source)
├── src/dnarecon/
│   ├── models.py              # ClusterRecord, ResultRecord
│   ├── formats.py             # Microsoft / BBS / ITR file formats
│   ├── dataset.py             # loading, validation, eligibility, 30/70 split
│   ├── simulator.py           # IDS channel (week 2)
│   ├── bbs_adapter.py         # batch mode, -t cap, verbose CSV parser
│   ├── itr_adapter.py         # subprocess + timeout, micro-batches
│   ├── router.py              # τ rule + length-consistency variant
│   ├── cache.py               # per-cluster results table
│   ├── metrics.py             # exact, edit distance, 2×2, AUROC, rescue/harm
│   ├── scheduler.py           # serial / static / dynamic
│   └── provenance.py          # git hashes, hardware, config snapshot
├── analysis/
├── tests/{unit,integration,fixtures}/
├── data/          (gitignored except fixtures/ and splits/)
├── results/       (gitignored except small summary tables)
├── external/      (gitignored; cloned by setup_external.sh)
└── docs/
```

## Week 1 (28 Sep – 4 Oct)

| Day | Work | Done when |
|---|---|---|
| 1 | Repo skeleton, `.gitignore`, venv; `setup_external.sh` builds BBS + ITR at pinned commits; environment validator; download Microsoft CNR and read its README caveat about malformed clusters (2024); write the eligibility rule; 30/70 split with fixed seed. | Both binaries build; `data/splits/` committed |
| 2 | ITR wrapper (placeholder line 1, per-cluster reseed, one row per cluster); verify ITR ignores line 1 for decisions; BBS adapter; metrics with hand-checked unit tests. | Adapter tests pass on a 10-cluster fixture |
| 3 | BBS + ITR on 1,000 dev clusters; sanity check vs published numbers; measure ITR s/cluster on this laptop. | Numbers roughly match or discrepancy explained |
| 4 | 2×2 table, AUROC (confidence, path weight), go/no-go in `docs/pilot_notes.md`. | Decision recorded |
| 5–7 | If go: full Microsoft cache overnight; start IDS simulator. If no-go: evaluate CPL or wider-beam BBS. | Microsoft cache complete |

## Weeks 2–4

| Week | Engineering | Writing |
|---|---|---|
| 2 (5–11 Oct) | IDS simulator (frozen channel spec + statistical tests); router + selection rules; synthetic grid cache runs overnight | Introduction + methodology (pilot as preliminary evidence) |
| 3 (12–18 Oct) | Finish grid; τ sweep from cache; live scaling runs: serial vs static vs dynamic at 1/2/4/8 workers, ≥3 repetitions after warm-up, median + dispersion | Implementation + results sections |
| 4 (19–25 Oct) | Only fixes and reruns | Abstract, conclusion, figures, references; final paper |

If engineering spills into Week 4, **cut a grid condition** rather than writing time.

## Rough compute budget (laptop, to be confirmed by the pilot)

| Workload | Serial | 4 workers |
|---|---|---|
| ITR, Microsoft (~10k clusters, ~0.7 s each) | ~2 h | ~35–45 min |
| ITR, synthetic grid (12 × 1,000; cost ∝ coverage²) | ~40–60 min | ~15–25 min |
| BBS, everything | minutes | |

## Habits

- One config file per experiment; each figure reproducible from one command.
- Per-cluster logging: final algorithm, every runtime.
- Test split untouched until τ is frozen on dev.
- Timing only on this laptop: plugged in, Best performance mode, idle machine, warm-up,
  ≥3 repetitions.
- The bioinformatics paper reuses the same cached tables; no separate experiments.

## Methodology (frozen 2 Oct 2026, git tag `methodology-freeze`)

Everything below was decided on dev data only, before any test-split result was opened.
Frozen configs: `configs/final/`.

- **Router:** route a cluster to ITR iff BBS confidence < τ (`src/dnarecon/router.py`).
  A non-empty cluster without BBS output (failed BBS shard) counts as confidence 0, so it is
  routed whenever τ > 0: the fallback covers primary failures, as BBS covers ITR failures.
  τ = 0 routes nothing (BBS-only policy). Empty clusters (no reads) never go to any engine.
  A BBS failure stays visible: `bbs_status` shard_failed, the BBS stage and run are marked
  failed, the manifest counts `n_bbs_failed` / `n_routed_without_bbs`, and the paper
  reports the count. Timing summaries use only runs whose stages all succeeded (G8.6).
  No learned routing.
- **Selector:** primary = default (a successful ITR result replaces BBS; ITR failure or
  timeout keeps BBS). Secondary = length check (also keeps BBS when ITR's output length
  differs from the designed length). Neither uses ground truth.
- **τ rule** (`analysis/select_tau.py`, `configs/tau_selection.yaml`): per dataset family,
  the cheapest τ in {0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1} whose pooled dev exact rate
  is within 0.25 points of the best τ; cost = share routed to ITR. One global τ for all 13
  synthetic conditions; Microsoft separately. Per-condition τ is reported only as a
  labeled oracle analysis.
- **Frozen τ:** synthetic **0.8** (default selector; length check 0.99); Microsoft **0**
  (BBS only: no τ beats BBS by more than 0.25 points on dev).
- **Schedulers** (`src/dnarecon/scheduler.py`): serial; static (micro-batches shuffled with
  a fixed seed, then equal contiguous blocks per worker); static_lpt (labeled extra:
  longest first by min(coverage, 25)², to the least-loaded worker); dynamic (shared queue).
  Worker threads launch native processes. One native-concurrency cap for both stages:
  BBS `-t` = ITR workers.
- **Scaling campaign** (`configs/final/scaling.yaml`): the full synthetic test split
  (9,100 clusters) at τ = 0.8; cells serial-1, dynamic 1/2/4/8, static 2/4/8, static_lpt 4;
  micro-batch 5; one unmeasured warm-up; 3 repetitions with the cell order rotated each
  repetition; median, IQR and min/max. T1 = serial cell. 8 workers = hyper-threaded,
  reported separately. Laptop plugged in, "Best performance", idle.
- **Metrics:** makespan = whole adaptive run (BBS + route + ITR); BBS and ITR stage times
  reported separately; speedup S = T1/Tp, efficiency S/p, utilization = ITR busy time /
  (p × ITR stage time), imbalance = max/mean worker busy time
  (`analysis/scaling_summary.py`).

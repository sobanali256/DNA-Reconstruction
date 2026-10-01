# CLAUDE.md — project context for Claude Code

Read this first. It records every decision made at project kickoff (28 Sep 2026) so a new
session does not need to rediscover it. Detailed plan: `docs/PLAN.md`. Environment/setup:
`docs/SETUP.md`.

## What this project is

**Adaptive and parallel DNA trace reconstruction.** BBS (Bidirectional Beam Search, fast,
Rust) reconstructs every cluster of noisy reads and reports a confidence score. Clusters with
confidence < τ are escalated to ITR (Iterative Reconstruction, slow O(n²t²), C++). The
resulting uneven ITR workload is scheduled on a multicore worker pool (serial vs static vs
dynamic). A Python controller owns everything; BBS and ITR stay native executables.

One codebase, two course papers:
- **PDC paper**: speedup, efficiency, makespan, static vs dynamic scheduling at 1/2/4/8 workers.
- **Bioinformatics paper**: accuracy, BBS/ITR complementarity (2×2 table: does ITR fix the
  clusters BBS gets wrong?), confidence AUROC per condition, robustness across error
  rate/composition/coverage.

The user works **solo** (course documents list three group members; ignore any work split).

## Source documents (authoritative, on the Windows side)

`/mnt/c/Users/lenovo/OneDrive/Desktop/DNA Computing/`
- `DNA_Adaptive_Parallel_Reconstruction_Implementation_Design_v3_Final_Verified.docx`: build
  spec, data contracts, gates, metrics. **Wins on conflicts** (latest, audited). Its phase
  dates are obsolete; follow the 4-week plan in `docs/PLAN.md`.
- `DNA_Trace_Reconstruction_Ground_Truth_Research_Plan.docx`: RQs, hypotheses, novelty boundary.
- `BioInfo/Bioinformatics_Project_Proposal.docx`: bioinformatics RQs, synthetic design.
- `PDC Phases/PDC_Phase1_Literature_Review.docx`: gaps G1–G5.
- `Literatures/*.pdf`: the papers (BBS paper = iScience `1-s2.0-S2589004225020528-main.pdf`;
  ITR paper = Sci. Rep. `s41598-024-51730-3.pdf`).

To read a .docx: unzip `word/document.xml` and strip tags (python zipfile + regex works).

## Decisions made (do not re-litigate)

| Topic | Decision |
|---|---|
| Environment | WSL2 Ubuntu 24.04, disk file at `E:\WSL\Ubuntu-24.04` (HDD). C: SSD has little space; never put data/results there. |
| Repo | Public: https://github.com/sobanali256/DNA-Reconstruction, cloned at `~/projects/DNA-Reconstruction`. HTTPS remote, `gh` authenticated. |
| Toolchain | g++ 13.3, cargo 1.98, Python 3.12 (use a venv at `.venv/`). |
| Timing hardware | **This laptop only**: i5-10210U, 4 physical / 8 logical cores, 20 GB RAM. 1/2/4 workers = physical; 8 = hyper-threaded, label it so. |
| Colab Pro | Backup only (if ITR is too slow on the laptop or for reruns). Never report Colab runtimes. |
| Split | Microsoft dataset: 30% dev / 70% test, fixed random seed. Pilot (1,000 clusters) drawn from **dev only**. τ chosen on dev only. |
| ITR randomness | Upstream seeds `mt19937` from the clock but, with its default priorities (0), never draws from it: ITR is deterministic on file input. **Our wrapper still reseeds per cluster: `FNV-1a-64(cluster_id) XOR seed`** (seed in `configs/itr.yaml`), as a guarantee. No other change to the algorithm. Documented in `adapters/itr_native/PATCH.md`. |
| Empty clusters | Stay in the denominator as failures; report the count separately. |
| ITR timeout | Start at 60 s/cluster; adjust after the pilot. Timeout = ITR failure, so the cluster keeps its BBS result. |
| Caching test split | Allowed (running both tools does not leak); analysis code must filter to dev when choosing τ. |
| Parallel model | Controller threads/async subprocesses launching native processes (design doc v3), not Python multiprocessing. |
| Static baseline | Include static partitioning (needed for static-vs-dynamic). |
| Synthetic grid | 4 total error rates (3/6/9/12%) × coverage {5,10,20} = 12 conditions, balanced errors; the skewed compositions at one coverage only if time allows. |
| Length-consistency selector | Secondary variant, computed from the cache. |
| Fallback engine (Day 4 go/no-go, 1 Oct) | **Keep ITR**; frame the paper around *when* the cascade helps. Pilot: ITR rescues 12% of BBS failures but harms more; beam-100 BBS rescues 0. CPL not pursued unless the synthetic grid also shows no rescue. |
| BBS timing | Per batch/shard only; never invent per-cluster BBS runtimes. ITR timing per cluster. |
| BBS nondeterminism | BBS breaks score ties by random HashMap order (~0.5% of clusters, all confidence ≤ 0.5, confidence itself stable). Cache **one** run as canonical; measure and report run-to-run variability from ~5 repeats. No patch to BBS. |

## Facts found in the upstream source code

**ITR** (`omersabary/Reconstruction`, folder `Iterative/`, license "TBA"). Full audit:
`docs/upstream_notes.md`.
- Standard C++ only; build per the README: `g++ -std=c++0x -O3 ... *.cpp -o DNA`.
- `main(argc, argv)`: `argv[1]` input file, `argv[2]` output dir. Input per cluster: line 1 =
  original strand, line 2 = `*****`, then reads, cluster ends after **two** blank lines.
- **ITR uses the original strand only through its length** (`FinalGuess(..., original.size())`).
  So the wrapper passes a placeholder of the expected length (G2.3 confirmed 29 Sep).
- `maxCopies = 25`: keeps the **first 25 reads in file order** (keep this upstream default).
- The hard-coded `150` is an unused `strandLen` argument: no effect.
- The shared clock-seeded `mt19937` is passed around but **never drawn from** with upstream's
  priorities (0); random backtracking needs priority 6. ITR is deterministic on file input.
  `Cluster2.cpp:29` shuffle is only in the synthetic-data constructor (not used for files).
  (The Day 1 claim that upstream leaks ground truth across clusters via the RNG was wrong.)
- About 0.9 s/cluster on this laptop (20-cluster smoke run).

**BBS** (`GZHoffie/bbs`, MIT, Rust edition 2024, v0.2.0):
- `bbs <Clusters.txt> -l 110 -o out.csv` gives the verbose CSV
  `read_id,reconstruction_result,k,path_weight,confidence`.
- Input formats: `microsoft` (CNR dataset) and `dna_storage_toolkit`.
- **`-t` defaults to ALL logical CPUs** (spawns that many worker threads). Always pass `-t`
  equal to the worker cap, or scaling experiments silently oversubscribe.
- **Empty clusters produce no CSV row and `read_id` counts only non-empty clusters.** The
  adapter writes only non-empty clusters and maps rows back by order (check row count).
- Beam width flag, default 20 (the "wider beam" fallback candidate).

## Rules (from the design doc; mandatory)

- Ground truth is **evaluation-only**. It never reaches BBS, ITR, the router or the selector.
- Final quality numbers come from the held-out test split, opened only after τ is frozen.
- Every eligible cluster counts in the denominator; crashes/timeouts are failures.
- BBS runs in batch/shard mode, never one process per cluster (except debug fixtures).
- Never commit ITR source (license TBA). `external/` is cloned at pinned commits by a script;
  only our wrapper/patch is committed. Record upstream commit hashes in `SOURCE_VERSIONS.md`.
- Everything that varies lives in `configs/*.yaml`; a figure = one command.
- Log per cluster: which algorithm produced the final result, and every runtime.
- Never hand-edit raw results; failed runs are kept and marked invalid, never deleted.
- Do not claim confidence-guided fallback, parallel DNA processing or per-cluster switching
  is novel. The claim is scoped to this specific BBS→ITR cascade and its scheduling study.
- Report negative results (e.g. ITR rarely rescues BBS) honestly.

## Working with this user

- They are new to Linux/WSL. Explain shell steps plainly and give exact commands.
- They asked to discuss and understand before code is written; for new phases, outline the
  plan first when the design is not already settled here or in `docs/PLAN.md`.
- Do not bring up course deadlines.
- Admin-only steps (Windows PowerShell as Administrator) must be run by the user.
- `ext4.vhdx` once got attached to Windows as a RAW disk and locked WSL; the fix is in
  `docs/SETUP.md`. Never suggest `--set-sparse --allow-unsafe`.

## Current status

- 28 Sep 2026: environment and repo ready.
- 28 Sep 2026, Day 1 done: skeleton, venv (`requirements.lock`), `scripts/setup_external.sh`
  (BBS + ITR build at pins in `scripts/external_pins.env`), `scripts/download_microsoft.sh`,
  `scripts/validate_environment.py` (all PASS), eligibility rule (`docs/data_policy.md`),
  split `data/splits/microsoft_cnr_split.csv` (seed 20260928: 3,000 dev / 7,000 test;
  16 empty clusters).
- 29–30 Sep 2026, Day 2 mostly done (pushed, `3d1c569`): ITR wrapper `external/itr_cli`
  (identical to upstream, `scripts/check_itr_wrapper.py`); BBS adapter
  `src/dnarecon/bbs_adapter.py` (dev: 95.97% exact); ITR adapter `src/dnarecon/itr_adapter.py`
  (per-cluster timeout, crash containment); fixture `data/fixtures/microsoft_dev10.jsonl`.
  Gates G2.3, G2.4, G5.7 pass. Found: ITR is deterministic; BBS is not (ties, 0.5%).
  Metrics `src/dnarecon/metrics.py` (G2.5 pass): Hamming = BBS paper formula; no output
  = empty prediction (ED = Hamming = length); summaries also give with-output-only means.
  ResultRecord (`models.py`) + `src/dnarecon/results.py`: `build_record` (engine outputs
  only; ITR success wins, else BBS, else failed; whole-task failures via
  `bbs_shard_failed`/`itr_task_failed`) → `attach_metrics` (ground truth, last step; final,
  BBS and ITR scored separately for the 2×2) → `results/<run_id>/per_cluster.csv` +
  `timing.csv` (writers never overwrite). `total_cluster_runtime_ms` only in ITR-only runs.
  **Day 2 complete.**
- 30 Sep 2026, Day 3 done: pilot sample `data/splits/microsoft_pilot1000.csv` (1,000 dev,
  seed 20260930); runner `scripts/run_experiment.py <config>` (one `results/<run_id>/`
  per repetition: manifest.json, per_cluster.csv, timing.csv, raw/; refuses a dirty tree);
  `analysis/pilot_summary.py`. Pilot (`docs/pilot_notes.md`): BBS 96.70% exact, ITR 88.50%
  (paper 94.77 / 87.58; ranking and gap reproduce); ITR 1.04 s/cluster (full set ≈ 2.9 h
  serial); BBS 3/1,000 clusters vary across 6 runs. Canonical cache runs:
  `pilot_bbs-20260930-191013-r1`, `pilot_itr-20260930-212355-r1`.
- 1 Oct 2026, Day 4 done: `analysis/cascade_from_cache.py <config>` (2×2, rescuable share,
  AUROC + bootstrap CI, τ sweep with default and length-consistency selectors; fallback =
  ITR-only or BBS-only run); `metrics.cascade_outcome` / `failure_auroc`; `results.read_run`.
  Pilot: ITR rescues 4/33 BBS failures (12%); AUROC confidence 0.91, path weight 0.89;
  the cascade never beats BBS alone (length check: +1 at best); beam-100 BBS rescues 0/33.
  **Decision: GO with ITR, framed around when the cascade helps** (`docs/pilot_notes.md`).
  Why (`analysis/itr_failure_modes.py`): ITR harm = homopolymer under-calling (Nanopore reads
  shorten long runs, ITR follows the majority; 93% of harmed outputs have the wrong length);
  no rescue = too few reads (median 7). Hypothesis for the synthetic grid: ITR harms less
  with independent IDS errors; consider one homopolymer-bias condition.
  Code review fix `cb06597`. Day 4 commits: `bad480b`, `55c2711`, `78f056c`, `cb06597`.
  **Next (plan first):** Days 5–7: full Microsoft cache (BBS + ITR on all 10,000), rerun the
  Day 4 analyses on the full dev split, start the IDS simulator, decide on a
  homopolymer-bias synthetic condition.
- Sanity targets **verified** in the BBS paper (iScience 2025, Table 2, "Srinivasavaradhan
  et al." = Microsoft CNR, all 10,000 clusters, default parameters, beam 20): success rate
  (exact match) BBS 94.77%, ITR 87.58%, CPL 94.93%; ITR took 7,352 s (~0.74 s/cluster, i9-13900H).
  The paper does not say how empty clusters were counted; with 16 empty clusters the
  difference is at most 0.16 points.

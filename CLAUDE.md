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
| Caching test split | Allowed (running both tools does not leak); analysis code must filter to dev when choosing τ. Test caches are run now (user, 1 Oct); the runner never prints test accuracy. |
| Parallel model | Controller threads/async subprocesses launching native processes (design doc v3), not Python multiprocessing. |
| Static baseline | Include static partitioning (needed for static-vs-dynamic). |
| Synthetic grid (frozen 1 Oct) | 4 total error rates (3/6/9/12%) × coverage {5,10,20} = 12 conditions, balanced errors, **+ `e06_c10_hp50`** (homopolymer runs ≥ 4 lose one base with p 0.5; tests the Day 4 ITR-harm explanation). 300 dev + 700 test clusters per condition, separate seeds (`configs/dataset_synthetic.yaml`). Skewed compositions at one coverage only if time allows. |
| Length-consistency selector | Secondary variant, computed from the cache. |
| Fallback engine (Day 4 go/no-go, 1 Oct) | **Keep ITR**; frame the paper around *when* the cascade helps. Pilot: ITR rescues 12% of BBS failures but harms more; beam-100 BBS rescues 0. CPL not pursued unless the synthetic grid also shows no rescue. |
| BBS timing | Per batch/shard only; never invent per-cluster BBS runtimes. ITR timing per cluster. |
| Cache-run ITR times | Not timing results (the laptop slowed ~2× mid-run on 1 Oct). Reported timings come only from the controlled scaling runs; cache times are used only as approximate cost. |
| BBS nondeterminism | BBS breaks score ties by random HashMap order (~0.5% of clusters, all confidence ≤ 0.5, confidence itself stable). Cache **one** run as canonical; measure and report run-to-run variability from ~5 repeats. No patch to BBS. |
| τ rule (2 Oct) | Per family, cheapest τ in the grid within 0.25 pt of the best pooled dev exact rate (cost = share routed). One global τ for the 13 synthetic conditions, Microsoft separately; per-condition τ only as labeled oracle. **Frozen: synthetic 0.8, Microsoft 0** (BBS only); default selector primary, length check secondary (synthetic 0.99). |
| BBS shard failure in adaptive runs (2 Oct, user) | **Routed to ITR**: no BBS output counts as confidence 0 (routed iff τ > 0; τ = 0 stays BBS only). Empty clusters never routed. Failure stays visible (`bbs_status` shard_failed, stage/run failed, manifest `n_bbs_failed`, `n_routed_without_bbs`); scaling summary drops runs with failed stages; paper reports the count. No dev cache has any BBS shard failure, so τ and all summaries are unchanged. |
| Static scheduler (2 Oct) | Seeded shuffle of micro-batches, equal contiguous blocks per worker (OpenMP static). `static_lpt` (longest first by min(coverage,25)²) is a labeled extra. |
| Scaling workload (2 Oct) | Full synthetic **test** split (9,100) at τ 0.8 (~2,900 to ITR, ~10 min serial); the 80/condition sample left only ~70 s. One cap for both stages: BBS `-t` = ITR workers. |

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
- 1 Oct 2026 (evening), Days 5–7 (`248704c`, review fix `21fee11`):
  IDS simulator `src/dnarecon/simulator.py` (balanced p_ins = p_del = p_sub, del/sub
  mutually exclusive, optional homopolymer shortening applied first; spec in its docstring
  and `docs/data_policy.md`); `scripts/make_synthetic.py configs/dataset_synthetic.yaml
  [--check]` → 13 conditions (3/6/9/12% × coverage 5/10/20 + `e06_c10_hp50`: runs ≥ 4 lose
  one base with p 0.5), 300 dev + 700 test each, one JSONL (`dataset_id` = condition).
  Gates G6.2/6.3/6.4/6.7 pass. `dataset.load_dataset` (JSONL or Microsoft); runner key
  `split: dev|test` (unknown/unsplit IDs are an error), log shows dev accuracy only,
  manifest records `dataset_files_sha256`. Run configs `configs/{microsoft,synthetic}_
  {dev,test}_{bbs,itr}.yaml`. Dev caches complete: `microsoft_dev_bbs-20261001-213122-r1`
  (96.0%), `microsoft_dev_itr-20261001-213129-r1` (87.8%; identical to the pilot on shared
  clusters), `synthetic_dev_bbs-20261001-223625-r1` (75.8% pooled),
  `synthetic_dev_itr-20261001-223644-r1` (88.7% pooled: ITR beats BBS on independent
  errors). `microsoft_test_bbs-20261001-232933-r1` complete;
  `microsoft_test_itr-20261001-232952-r1` stopped by the user → failed, do not use.
  ITR slowed ~2× mid-evening (Windows side): cache ITR times are mixed-speed, never report
  them as timing results.
- 2 Oct 2026, Day 7 done. **All caches complete and checked** (one row per split cluster,
  only empty clusters failed, no timeouts/crashes, clean tree, pinned engines). Test caches:
  `microsoft_test_bbs-20261001-232933-r1`, `microsoft_test_itr-20261002-124047-r1`,
  `synthetic_test_bbs-20261002-142420-r1`, `synthetic_test_itr-20261002-161609-r1`
  (test accuracy not computed). Interrupted, marked failed, do not use:
  `microsoft_test_itr-20261001-232952-r1`, `synthetic_test_itr-20261002-142437-r1`.
  Full Microsoft dev (`configs/microsoft_dev_cascade.yaml`, `docs/pilot_notes.md` Day 7):
  ITR rescues 16/120 BBS failures (13.3%, CI 8.4–20.6%), harms 261; confidence AUROC 0.958
  (path weight 0.882); default selector never beats BBS; length check +4 clusters at
  τ 0.6–0.7 (within noise); harm = homopolymer under-calling (92% wrong length), both-wrong
  median 7 reads. Pilot findings hold. Synthetic dev per condition: ITR ≥ BBS everywhere
  except `e03_c05` (95.3 vs 97.3) and `e06_c10_hp50` (58 vs 84: homopolymer bias confirmed).
- 2 Oct 2026, Week 2 items 1–5 done (`6eff2d0`, `aa4b174`, `04f84ce`, `c7328e8`, `243b9dd`,
  code review fixes `bd09263`: summary uses one `run_scaling` invocation and stops on
  incomplete cells or mixed code/data, cells validated up front, Ctrl-C cancels ITR relaunch;
  BBS failures routed to ITR `4a0599c`; second review `7c6853e`: one selection rule
  `metrics.itr_selected` for live and cache, summary picks the latest complete clean launch;
  plan `docs/PLAN.md` "Methodology", results `docs/pilot_notes.md` Days 8–9).
  Synthetic dev per condition: ITR rescues 66% of BBS failures (13% on Microsoft); `hp50`
  reproduces the Microsoft harm; cascade 89.0% at τ 0.9 vs BBS 75.8% / ITR 88.6%; length
  check 91.6%. `analysis/select_tau.py` → τ synthetic 0.8, Microsoft 0. New code:
  `router.py`, `scheduler.py` (serial/static/static_lpt/dynamic), runner method `adaptive`
  (+ `itr.scheduler/workers/partition_seed`, `warmup`), `scripts/run_scaling.py`,
  `analysis/scaling_summary.py`, `configs/final/`. Live adaptive dev run reproduces the cache
  exactly; gates G3.1–5, G5.1–7 pass (tests); smoke campaign complete.
  **Methodology frozen** 2 Oct 2026: tag `methodology-freeze` on `ae7aa1d` (pushed). Any
  change to τ, router, selectors, schedulers or the campaign after this is a method change.
- 2 Oct 2026 (night), Week 3 item 1 done: final test runs on `307c39b` (clean, pinned
  engines), all complete and integrity-checked (one row per test cluster, only the 9 empty
  Microsoft test clusters failed, no BBS shard failures/timeouts/crashes, routing = cache rule,
  BBS confidence identical to the test cache): `final_microsoft_test_adaptive-20261002-232255-r1`
  (τ 0, 0 routed), `final_synthetic_test_adaptive-20261002-232315-r1` (τ 0.8, 2,813 routed),
  `final_synthetic_test_adaptive_lengthcheck-20261002-232904-r1` (τ 0.99, 5,472 routed).
  These live runs are the headline test results; cache-derived numbers must match them.
  Not timing results. Log `results/final_test.log`.
  **Next (Week 3):** test-set analysis, full scaling campaign (`configs/final/scaling.yaml`,
  overnight, idle laptop, timing protocol), figures.
- Sanity targets **verified** in the BBS paper (iScience 2025, Table 2, "Srinivasavaradhan
  et al." = Microsoft CNR, all 10,000 clusters, default parameters, beam 20): success rate
  (exact match) BBS 94.77%, ITR 87.58%, CPL 94.93%; ITR took 7,352 s (~0.74 s/cluster, i9-13900H).
  The paper does not say how empty clusters were counted; with 16 empty clusters the
  difference is at most 0.16 points.

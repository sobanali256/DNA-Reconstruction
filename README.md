# DNA-Reconstruction

Adaptive and parallel DNA trace reconstruction. **BBS** (Bidirectional Beam Search, Rust)
reconstructs every cluster of noisy reads and reports a confidence score. Clusters with
confidence below a threshold τ are escalated to **ITR** (Iterative Reconstruction, C++).
The uneven ITR workload that results is scheduled on a multicore worker pool (serial vs
static vs dynamic). A Python controller owns data, routing, scheduling, logging and
metrics; BBS and ITR stay native executables.

## Scope and claims

We study **this specific BBS → ITR cascade**: when escalating low-confidence BBS results
to ITR helps or hurts, what it costs, and how the resulting ITR workload scales on a
multicore CPU.

We do **not** claim that any of the following is new: confidence-guided fallback,
parallel DNA reconstruction, or per-cluster algorithm switching. Negative results (for
example, ITR rarely fixing BBS's mistakes) are reported as they are.

Rules the code follows:
- Ground truth is used **only for evaluation**. It never reaches BBS, ITR, the router or
  the final selector. ITR receives only the expected strand length (see
  `docs/upstream_notes.md`).
- τ is chosen on the dev split only. Final quality numbers come from the held-out test split.
- Every eligible cluster counts in the denominator; crashes, timeouts and empty clusters
  are failures (`docs/data_policy.md`).
- The core evaluation assumes the strand length is known (BBS requires it), which fits DNA
  storage but not unknown-length reconstruction.

## Reproduce

Needs Linux (tested on Ubuntu 24.04 / WSL2) with g++ ≥ 13, Rust ≥ 1.85 and Python ≥ 3.11.
Machine setup: `docs/SETUP.md`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -e .     # or requirements.lock for exact versions
bash scripts/setup_external.sh                     # clone + build BBS and ITR at pinned commits
bash scripts/download_microsoft.sh                 # Microsoft CNR dataset, checksum-verified
.venv/bin/python scripts/make_split.py configs/dataset_microsoft.yaml --check
.venv/bin/python scripts/validate_environment.py   # all checks must PASS
.venv/bin/python scripts/check_itr_wrapper.py      # our ITR wrapper == upstream ITR
.venv/bin/pytest
```

## Layout

| Path | Contents |
|---|---|
| `configs/` | One YAML per experiment; everything that varies lives here |
| `scripts/` | Setup, data download, split, validation, experiment entry points |
| `src/dnarecon/` | Python package: models, formats, dataset, adapters, router, scheduler, metrics |
| `adapters/itr_native/` | Our thin ITR command-line wrapper (no ITR source is committed) |
| `data/splits/` | Committed dev/test split and dataset checksums |
| `docs/` | Plan, setup, data policy, upstream audit notes |
| `external/` | Upstream BBS and ITR, cloned by script (git-ignored) |

Upstream versions and licences: `SOURCE_VERSIONS.md`. Our own code is MIT-licensed
(`LICENSE`); ITR's licence is "TBA", so its source is never redistributed here.

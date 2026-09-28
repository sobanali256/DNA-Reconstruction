# Source versions

Pinned upstream code and data. The machine-readable pins are in
`scripts/external_pins.env`; `scripts/setup_external.sh` and
`scripts/download_microsoft.sh` check out exactly these commits, and
`scripts/validate_environment.py` fails if a checkout differs.

| Component | Repository | Commit | Date | Licence |
|---|---|---|---|---|
| BBS (Bidirectional Beam Search) | https://github.com/GZHoffie/bbs | `3e4ab46871929819e4f3e34a831c57cac88bb456` | 2026-07-07 | MIT |
| ITR (Iterative Reconstruction), folder `Iterative/` | https://github.com/omersabary/Reconstruction | `c50dec739bd2c7f18ac7678d921eecee02e86c6a` | 2023-05-23 | **TBA** (see below) |
| Microsoft CNR dataset | https://github.com/microsoft/clustered-nanopore-reads-dataset | `6938f44796185902a08381943c2895782886c5c3` | 2024-11-18 | MIT |

## Notes

- **BBS** is pinned one commit after the `v0.2.0` tag (`27c39ec`). That commit, "Adjust
  k_max as k_max=63 can cause bugs in the output", is a bug fix; the binary still reports
  version `0.2.0`. Build: `cargo build --release` (Rust edition 2024).
- **ITR licence is "TBA"** in the upstream README. We do not redistribute its source:
  `external/` is git-ignored and the source is cloned by script. Only our own wrapper in
  `adapters/itr_native/` and a description of how it differs from upstream `DNA.cpp` are
  committed. Build flags follow the upstream README: `g++ -std=c++0x -O3 -g3 -Wall`.
- **Microsoft CNR** file checksums (SHA-256) are in `data/splits/microsoft_cnr.sha256`.

## Toolchain used on the timing machine

Recorded per run by `src/dnarecon/provenance.py`. At setup (28 Sep 2026): g++ 13.3.0,
cargo/rustc 1.98.1, Python 3.12.3 (exact packages in `requirements.lock`), Ubuntu 24.04
on WSL2, Intel i5-10210U (4 physical / 8 logical cores).

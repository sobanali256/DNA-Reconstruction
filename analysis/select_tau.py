"""Week 2: choose the primary confidence threshold τ per dataset family, on dev only.

    .venv/bin/python analysis/select_tau.py configs/tau_selection.yaml

Rule (decided 2 Oct 2026, docs/pilot_notes.md): among the τ grid, take the cheapest τ whose
pooled dev exact rate is within `tolerance` of the best τ in the grid; cost = share of
clusters routed to ITR (cache ITR times are mixed-speed, so they are not used). The default
selector is primary, the length-consistency selector secondary. Per-condition τ (synthetic
grid) uses the same rule inside each condition and is an oracle: it knows the condition.

Reads only dev cache runs: a run whose manifest is not a dev run is an error (gate G7.5).
Writes <prefix>.csv with one row per (family, scope, selector).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from cascade_from_cache import ROOT, groups, load_cache
from dnarecon.metrics import cascade_outcome

EPS = 1e-12  # float slack when comparing rates


def select_tau(sweep: pd.DataFrame, tolerance: float) -> pd.Series:
    """The chosen row of one τ sweep (one selector, one group): cheapest within tolerance of the best."""
    best = sweep.exact_rate.max()
    eligible = sweep[sweep.exact_rate >= best - tolerance - EPS]
    chosen = eligible.sort_values(["fallback_ratio", "tau"]).iloc[0]
    return pd.concat([chosen, pd.Series({"best_exact_rate": best,
                                         "best_tau": sweep.loc[sweep.exact_rate.idxmax(), "tau"]})])


def check_dev_run(run_dir: Path) -> None:
    split = json.loads((run_dir / "manifest.json").read_text())["config"].get("split")
    if split != "dev":
        sys.exit(f"{run_dir.name}: not a dev run (split={split!r}); τ is chosen on dev only")


def family_rows(name: str, fam: dict, taus: list[float], tolerance: float) -> list[dict]:
    bbs_dir, fb_dir = ROOT / fam["bbs_run"], ROOT / fam["fallback_run"]
    for run_dir in (bbs_dir, fb_dir):
        check_dev_run(run_dir)
    df, _, method = load_cache(bbs_dir, fb_dir, "dev")
    if method != "itr_only" or not df.split.eq("dev").all():
        sys.exit(f"{name}: need dev BBS-only and ITR-only runs")
    group_by = fam.get("group_by")
    rows = []
    for group, g in groups(df, group_by):
        scope = "global" if group in (None, "all") else f"oracle:{group}"
        for selector in ("default", "length_check"):
            sweep = pd.DataFrame([cascade_outcome(g, tau, selector == "length_check") for tau in taus])
            chosen = select_tau(sweep, tolerance)
            bbs_only = sweep.loc[sweep.tau == 0, "exact_rate"]
            rows.append({
                "family": name, "scope": scope, "selector": selector,
                "primary": scope == "global" and selector == "default",
                "tau": chosen.tau, "exact_rate": chosen.exact_rate,
                "fallback_ratio": chosen.fallback_ratio, "n_routed": int(chosen.n_routed),
                "n_clusters": int(chosen.n_clusters),
                "best_tau": chosen.best_tau, "best_exact_rate": chosen.best_exact_rate,
                "bbs_only_exact_rate": bbs_only.iloc[0] if len(bbs_only) else None,
                "rule": f"cheapest tau with exact_rate >= best - {tolerance}",
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())
    tolerance = cfg["objective"]["tolerance"]
    out = pd.DataFrame([r for name, fam in cfg["families"].items()
                        for r in family_rows(name, fam, cfg["taus"], tolerance)])
    path = (ROOT / cfg["output_prefix"]).with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, float_format="%.4f", lineterminator="\n")
    print(out.drop(columns="rule").to_string(index=False, float_format=lambda v: f"{v:.4f}"), f"\nWrote {path}")


if __name__ == "__main__":
    main()

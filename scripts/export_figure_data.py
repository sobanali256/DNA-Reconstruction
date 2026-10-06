"""Export the data behind every paper figure, for drawing the figures in another tool.

    .venv/bin/python scripts/export_figure_data.py configs/figures.yaml

Reads the same aggregated CSVs as scripts/make_figures.py (configs/figures.yaml) and writes
<output_dir>/data/:
  f2_exact_by_condition.csv ... f9_time_vs_accuracy.csv   one tidy table per figure;
  FIGURE_DATA.md   all tables inline + conventions, captions and the pipeline spec, ready
                   to upload to a chat assistant.
Numbers are the plotted values (rates in %, times in s), rounded only in FIGURE_DATA.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from make_figures import KERNELS, METHODS, ROOT, cascade_metric, condition_label, condition_order

ROWS_NOTE = ("Row labels: 'Microsoft (real)' = Microsoft CNR Nanopore data; synthetic conditions are "
             "'error rate · coverage' (coverage = reads per cluster); 'hp' = homopolymer bias.")


def rows(test: pd.DataFrame) -> list[tuple[str, str, str]]:
    order = condition_order(test[(test.family == "synthetic") & (test.group != "all")].group.unique())
    return ([("microsoft", "all", "Microsoft (real)"), ("synthetic", "all", "Synthetic, pooled")]
            + [("synthetic", g, condition_label(g)) for g in order])


def f2(d, cfg):
    t = d["test_table"]
    t = t[t.split == "test"]
    out = []
    for fam, grp, label in rows(t):
        for method in METHODS:
            r = t[(t.family == fam) & (t.group == grp) & (t.method == method)]
            if not len(r) or (fam == "microsoft" and method == "adaptive"):
                continue
            r = r.iloc[0]
            out.append({"row": label, "method": METHODS[method][0], "exact_pct": r.exact_rate * 100,
                        "ci95_low_pct": r.exact_ci95_low * 100, "ci95_high_pct": r.exact_ci95_high * 100,
                        "n_clusters": r.n_clusters, "routed_pct": r.routed_share * 100})
    return pd.DataFrame(out)


def f3(d, cfg):
    t = d["test_table"]
    t = t[t.split == "test"]
    out = []
    for fam, grp, label in rows(t):
        for method in ("itr_only", "adaptive", "adaptive_lengthcheck"):
            r = t[(t.family == fam) & (t.group == grp) & (t.method == method)]
            note = "" if len(r) else "not run"
            if fam == "microsoft" and method == "adaptive":
                note = "τ 0 = BBS only (nothing routed)"
            out.append({"row": label, "method": METHODS[method][0],
                        "rescued": int(r.rescued_vs_bbs.iloc[0]) if len(r) else None,
                        "harmed": int(r.harmed_vs_bbs.iloc[0]) if len(r) else None, "note": note})
    return pd.DataFrame(out).astype({"rescued": "Int64", "harmed": "Int64"})


def f4(d, cfg):
    s, m = d["synthetic_test_cascade"], d["microsoft_test_cascade"]
    get = lambda df, k: cascade_metric(df, k)  # noqa: E731
    auc, lo, hi = get(s, "confidence"), get(s, "confidence_ci95_low"), get(s, "confidence_ci95_high")
    nw = cascade_metric(s[s.section == "auroc"], "n_bbs_wrong")
    out = [{"row": "Microsoft (real)", "auroc": get(m, "confidence")["all"], "ci95_low": get(m, "confidence_ci95_low")["all"],
            "ci95_high": get(m, "confidence_ci95_high")["all"],
            "n_bbs_failures_with_output": int(cascade_metric(m[m.section == "auroc"], "n_bbs_wrong")["all"])}]
    for g in ["all"] + condition_order([g for g in auc.index if g != "all"]):
        out.append({"row": "Synthetic, pooled" if g == "all" else condition_label(g), "auroc": auc[g],
                    "ci95_low": lo[g], "ci95_high": hi[g], "n_bbs_failures_with_output": int(nw[g])})
    df = pd.DataFrame(out)
    df["note"] = np.where(df.auroc.isna(), "not computable (no BBS failures)",
                          np.where(df.ci95_low.isna(), "CI not computable (too few failures)", ""))
    return df


def f5(d, cfg):
    out = []
    tt = d["test_table"]
    for fam, dev, test in (("synthetic", d["synthetic_dev_sweep"], d["synthetic_test_sweep"]),
                           ("microsoft", d["microsoft_dev_sweep"], d["microsoft_test_sweep"])):
        if "dataset_id" in dev:
            dev, test = dev[dev.dataset_id == "all"], test[test.dataset_id == "all"]
        for _, r in dev.sort_values(["selector", "tau"]).iterrows():
            out.append({"panel": fam, "series": f"{r.selector} τ sweep (dev)", "tau": r.tau,
                        "routed_pct": r.fallback_ratio * 100, "exact_pct": r.exact_rate * 100})
        for sel, tau in cfg["frozen_tau"][fam].items():
            r = test[(test.selector == sel) & np.isclose(test.tau, tau)].iloc[0]
            out.append({"panel": fam, "series": f"{sel} frozen τ on test", "tau": tau,
                        "routed_pct": r.fallback_ratio * 100, "exact_pct": r.exact_rate * 100})
        ref = tt[(tt.family == fam) & (tt.group == "all") & (tt.split == "dev")].set_index("method").exact_rate * 100
        for m, label in (("bbs_only", "reference: BBS only (dev)"), ("itr_only", "reference: full ITR (dev)")):
            out.append({"panel": fam, "series": label, "tau": None, "routed_pct": None, "exact_pct": ref[m]})
    return pd.DataFrame(out)


def f6(d, cfg):
    cells, runs = d["scaling_cells"], d["scaling_runs"]
    t1 = cells.set_index("cell").loc["serial_p1", "makespan_s_median"]
    runs = runs.assign(speedup=t1 / runs.makespan_s)
    out = []
    for _, c in cells.sort_values(["scheduler", "workers"]).iterrows():
        r = runs[runs.cell == c.cell]
        out.append({"scheduler": c.scheduler, "workers": c.workers, "hyperthreaded": c.hyperthreaded,
                    "makespan_s_median": c.makespan_s_median, "speedup_median": c.speedup,
                    "speedup_min": r.speedup.min(), "speedup_max": r.speedup.max(),
                    "efficiency_median": c.efficiency, "itr_stage_s_median": c.itr_s_median,
                    "bbs_stage_s_median": c.bbs_s_median, "utilization_median": c.utilization_median,
                    "imbalance_median": c.imbalance_median})
    return pd.DataFrame(out)


def f7(d, cfg):
    r = d["scaling_runs"]
    r = r[r.workers > 1].sort_values(["workers", "scheduler", "repetition"])
    return r[["workers", "scheduler", "repetition", "makespan_s", "imbalance", "utilization"]].reset_index(drop=True)


def f8(d, cfg):
    c = d["contention"]
    med = c.groupby(["kind", "copies"]).seconds.median()
    out = [{"kernel": KERNELS[k][0], "copies": n, "median_seconds_per_process": v,
            "slowdown": v / med[(k, 1)], "throughput_vs_1": n / (v / med[(k, 1)])}
           for (k, n), v in med.items()]
    return pd.DataFrame(out)


def f9(d, cfg):
    t = d["test_table"]
    t = t[(t.split == "test") & (t.family == "synthetic") & (t.group == "all")].set_index("method").exact_rate * 100
    cells, itr = d["scaling_cells"].set_index("cell"), d["itr_only_cells"].set_index("cell")
    cost = d["routed_cost"].set_index("method")
    day = cells.loc["serial_p1", "itr_s_median"] / cost.loc["adaptive", "routed_itr_s_median"]
    out = []
    for method, times in (("bbs_only", ("bbs_s_median", cells)), ("adaptive", ("makespan_s_median", cells)),
                          ("itr_only", ("makespan_s_median", itr))):
        col, table = times
        for cell, w in (("serial_p1", 1), ("dynamic_p4", 4)):
            out.append({"method": METHODS[method][0], "workers": w, "seconds": table.loc[cell, col],
                        "exact_pct": t[method], "status": "measured" + (" (BBS stage)" if method == "bbs_only" else "")
                        + (" on a different day (~6-7% slower)" if method == "itr_only" else "")})
    out.append({"method": METHODS["adaptive_lengthcheck"][0], "workers": 1,
                "seconds": cells.loc["serial_p1", "bbs_s_median"] + cost.loc["adaptive_lengthcheck", "routed_itr_s_median"] * day,
                "exact_pct": t["adaptive_lengthcheck"], "status": "estimated (not timed)"})
    return pd.DataFrame(out)


FIGURES = {
    "f2_exact_by_condition": (f2, "Exact-match rate per condition, test split, four methods (dot plot).", ROWS_NOTE),
    "f3_rescue_harm": (f3, "Rescued vs harmed clusters relative to BBS only, test split (diverging bars, "
                           "one panel per method).", ROWS_NOTE + " Rescued = BBS wrong and method right; harmed = BBS "
                           "right and method wrong. Counts span 0-1,403: use a symmetric log scale or label values."),
    "f4_auroc": (f4, "AUROC of BBS confidence for predicting BBS failure, test split, with 95% bootstrap CIs.",
                 ROWS_NOTE + " Chance = 0.5."),
    "f5_tau_tradeoff": (f5, "Exact rate vs share of clusters routed to ITR over the τ grid (dev), frozen τ "
                            "applied to test, two panels (synthetic, Microsoft).",
                        "'default' = primary cascade, 'length_check' = length-check cascade. Reference rows are "
                        "horizontal lines. On Microsoft the frozen τ is 0 (BBS only); no length-check test point."),
    "f6_speedup": (f6, "Speedup and efficiency vs workers (1, 2, 4, 8) for dynamic and static scheduling.",
                   "Speedup = serial median makespan / cell median makespan (whole run: BBS + routing + ITR). "
                   "Static has no 1-worker cell: use the serial run. 8 workers = hyper-threaded (4 physical cores)."),
    "f7_schedulers": (f7, "Makespan and load imbalance per run (3 repetitions) by scheduler and worker count.",
                      "Imbalance = max / mean worker busy time (1.0 = perfect). static_lpt only at 4 workers."),
    "f8_contention": (f8, "Contention probe: per-process slowdown when N identical processes run at once.",
                      "Slowdown = median per-process time / median at 1 copy (3 repetitions). Diagnostic only."),
    "f9_time_vs_accuracy": (f9, "Wall time vs exact-match rate on the synthetic test split (9,100 clusters).",
                            "Use a log time axis. Mark the estimated point differently from measured ones."),
}

CONVENTIONS = """## Conventions (keep them consistent across all figures)

| Entity | Colour | Marker |
|---|---|---|
| BBS only | #2a78d6 (blue) | circle |
| ITR only (full) | #eb6834 (orange) | square |
| Cascade, primary (τ 0.8) | #1baf7a (green) | triangle |
| Cascade, length check (τ 0.99) | #eda100 (yellow) | diamond |
| Scheduler dynamic / static / static LPT / serial | #2a78d6 / #eb6834 / #1baf7a / #52514e | circle / square / triangle / circle |
| Kernel compute / memory / ITR (f8) | #2a78d6 / #eb6834 / #1baf7a | circle / square / triangle |
| Rescued / harmed (f3) | #256abf / #e34948 | bars |

Text #0b0b0b / #52514e, gridlines #e1e0d9, axes #c3c2b7, hyper-threaded shading #f0efec.
Marker shapes repeat the identity so figures read in grayscale. Width: IEEE single column
3.5 in (f2, f4, f8, f9) or double column 7.16 in (f1, f3, f5, f6, f7). Sans-serif, 7–8 pt.
"""

RULES = """## Rules for whoever draws these figures

1. Use only the numbers in this file. Do not round differently in labels than in the text
   (one decimal for percentages, two for AUROC and speedup), and do not invent values.
2. Every colour, marker, line style and shaded region must appear in a legend or carry a
   direct label.
3. Label the split: f2, f3, f4, f9 are **test** results; the f5 curves are **dev** (with test
   points marked); f6–f8 are timing runs on one laptop (Intel i5-10210U, 4 cores / 8 threads).
4. Keep the caveats visible (caption or note): the f9 length-check time is an estimate; full
   ITR in f9 was timed on another day (~6-7% slower; measured ratio 9.5×/9.7×, ≈ 9× corrected);
   f8 is a diagnostic, not a timing result; 8 workers are hyper-threaded.
5. Empty or not-computable cells are meaningful (e.g. no BBS failures): show them as text,
   not as zero.
"""

F1_SPEC = """## f1 — Pipeline diagram (no data)

Left to right:
1. **Clusters** — noisy reads per stored strand.
2. **A · BBS** (blue) — reconstructs every cluster in batches; outputs a sequence and a
   confidence c.
3. **B · Router** — c < τ → ITR; c ≥ τ → keep the BBS result. (τ chosen on dev: 0.8; Microsoft 0.)
4. Two branches:
   - **C · ITR worker pool** (orange) — 4 workers (w1–w4) taking micro-batches of 5 clusters;
     scheduling serial / static / dynamic (shared queue).
   - **BBS result kept** (blue) — confidence ≥ τ.
5. **Selector** — successful ITR result → ITR; otherwise BBS. Length-check variant: use ITR
   only if its output has the designed length (110 nt).
6. Output: final sequence + which engine produced it.
Footnote: ground truth is used only after the final selection, for evaluation.
"""


def md_table(df: pd.DataFrame) -> str:
    def digits(column: str) -> int:  # percentages, seconds and τ: 2 decimals; ratios, AUROC: 4
        return 2 if column == "tau" or column.endswith(("pct", "_s", "_s_median", "seconds", "seconds_per_process")) else 4

    def fmt(v, column):
        if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
            return "—"
        if isinstance(v, (float, np.floating)):
            return f"{v:.{digits(column)}f}"
        return str(v)
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(fmt(v, c) for v, c in zip(row, df.columns)) + " |"
              for row in df.itertuples(index=False)]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    cfg = yaml.safe_load(parser.parse_args().config.read_text())
    d = {name: pd.read_csv(ROOT / path) for name, path in cfg["inputs"].items()}
    out = ROOT / cfg["output_dir"] / "data"
    out.mkdir(parents=True, exist_ok=True)
    captions = (ROOT / cfg["output_dir"] / "CAPTIONS.md").read_text()
    parts = ["# Data behind the paper figures\n",
             "Adaptive DNA trace reconstruction: BBS first, low-confidence clusters to ITR, ITR work on "
             "a multicore worker pool. Generated by `scripts/export_figure_data.py` from the project's "
             "aggregated result tables (the same inputs as `scripts/make_figures.py`).\n",
             RULES, CONVENTIONS, F1_SPEC]
    for name, (fn, what, note) in FIGURES.items():
        df = fn(d, cfg)
        df.to_csv(out / f"{name}.csv", index=False, float_format="%.6g")
        parts.append(f"## {name.split('_')[0]} — {what}\n\n{note}\n\nCSV: `{name}.csv`\n\n{md_table(df)}\n")
    parts.append("## Draft captions\n\n" + captions.split("\n", 1)[1])
    (out / "FIGURE_DATA.md").write_text("\n".join(parts))
    print(f"wrote {len(FIGURES)} CSVs and FIGURE_DATA.md to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

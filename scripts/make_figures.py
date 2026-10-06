"""Paper figures from the aggregated result tables only (design doc v3, Phase 9).

    .venv/bin/python scripts/make_figures.py configs/figures.yaml [--only f2 f5 ...]

Reads the CSVs named in the config (never per-cluster files, never raw runs) and writes
<output_dir>/<name>.<format> for every figure:
  f1_pipeline         pipeline and worker-pool diagram (no data)
  f2_exact_by_condition   exact-match rate per condition, test split, four methods
  f3_rescue_harm      rescued vs harmed clusters against BBS only, per condition
  f4_auroc            BBS confidence AUROC per condition with 95% CIs (test)
  f5_tau_tradeoff     exact rate vs share routed to ITR over the τ grid (dev), frozen τ and test
  f6_speedup          speedup and efficiency vs workers, dynamic vs static
  f7_schedulers       makespan per repetition and imbalance, per scheduler and worker count
  f8_contention       per-process slowdown vs concurrent copies (contention probe)
  f9_time_vs_accuracy wall time vs exact rate: BBS only, cascades, full ITR (RQ0)
Colors follow the entity in every figure (method, scheduler, kernel); marker shapes repeat
the identity so the figures read in grayscale.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Reference categorical palette (dataviz skill), fixed slot order; chrome inks.
SLOT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
POS, NEG = "#256abf", "#e34948"  # diverging pair: rescued / harmed

METHODS = {  # entity -> (label, color, marker)
    "bbs_only": ("BBS only", SLOT[0], "o"),
    "itr_only": ("ITR only (full)", SLOT[1], "s"),
    "adaptive": ("Cascade, primary", SLOT[2], "^"),
    "adaptive_lengthcheck": ("Cascade, length check", SLOT[3], "D"),
}
SCHEDULERS = {"serial": ("serial", INK2, "o"), "dynamic": ("dynamic", SLOT[0], "o"),
              "static": ("static", SLOT[1], "s"), "static_lpt": ("static LPT", SLOT[2], "^")}
KERNELS = {"compute": ("compute kernel", SLOT[0], "o"), "memory": ("memory kernel", SLOT[1], "s"),
           "itr": ("ITR", SLOT[2], "^")}
COL1, COL2 = 3.5, 7.16  # IEEE single / double column width (inches)
HT_SHADE = "#f0efec"


def marker_handle(marker, color, label, hollow=False, edge="white", size=5.5):
    return plt.Line2D([], [], linestyle="", marker=marker, markersize=size, label=label,
                      markerfacecolor="white" if hollow else color,
                      markeredgecolor=color if hollow else edge, markeredgewidth=1.2 if hollow else 0.5)


def line_handle(color, label, style="-", width=1.4, marker=None):
    return plt.Line2D([], [], color=color, linestyle=style, linewidth=width, marker=marker, label=label)


def patch_handle(color, label):
    return matplotlib.patches.Patch(facecolor=color, edgecolor="none", label=label)


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 7.5, "axes.titlesize": 8, "axes.labelsize": 7.5,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7, "legend.frameon": False,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.6, "axes.labelcolor": INK2, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.5, "axes.axisbelow": True, "axes.spines.top": False,
        "axes.spines.right": False, "lines.linewidth": 1.4, "lines.markersize": 4.5,
        "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
        "figure.facecolor": "white", "axes.facecolor": "white",
    })


def condition_label(dataset_id: str) -> str:
    """synthetic_e06_c10_hp50 -> '6% · 10× · hp'."""
    parts = dataset_id.removeprefix("synthetic_").split("_")
    label = f"{int(parts[0][1:])}% · {int(parts[1][1:])}×"
    return label + " · hp" if len(parts) > 2 else label


def condition_order(ids) -> list[str]:
    """By coverage, then error rate; the homopolymer condition right after its base condition."""
    def key(d):
        p = d.removeprefix("synthetic_").split("_")
        return int(p[1][1:]), int(p[0][1:]), len(p)
    return sorted(ids, key=key)


def cascade_metric(df: pd.DataFrame, metric: str) -> pd.Series:
    """One metric of the long-format cascade table, by group (dataset_id 'all' if ungrouped)."""
    rows = df[df.metric == metric]
    index = rows.dataset_id if "dataset_id" in rows else pd.Series(["all"] * len(rows), index=rows.index)
    return pd.Series(pd.to_numeric(rows.value).values, index=index.values)


# ---------------------------------------------------------------- figures

def f1_pipeline(d: dict, cfg: dict):
    fig, ax = plt.subplots(figsize=(COL2, 2.35))
    ax.set_xlim(0, 100)
    ax.set_ylim(-4, 33)
    ax.axis("off")

    def box(x, y, w, h, color, title=None, sub=None, title_y=0.68, sub_y=0.3, fill="white"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2",
                                    linewidth=0.9, edgecolor=color, facecolor=fill))
        if title:
            ax.text(x + w / 2, y + h * title_y, title, ha="center", va="center", fontsize=7.5,
                    fontweight="bold", color=INK)
        if sub:
            ax.text(x + w / 2, y + h * sub_y, sub, ha="center", va="center", fontsize=6.0,
                    color=INK2, linespacing=1.25)

    def arrow(x0, y0, x1, y1, text="", tx=None, ty=None):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=7,
                                     linewidth=0.8, color=INK2))
        if text:
            ax.text(tx, ty, text, ha="center", va="center", fontsize=6.0, color=INK2)

    box(0.5, 9.5, 11.5, 11, INK2, "Clusters", "noisy reads\nper strand")
    box(15.5, 9.5, 17, 11, SLOT[0], "A  BBS", "all clusters, batched\n→ sequence,\nconfidence c", sub_y=0.3)
    box(36, 9.5, 13.5, 11, INK2, "B  Router", "c < τ → ITR\nc ≥ τ → keep BBS")
    # C: worker pool (title, worker row, caption stacked inside)
    box(55, 16.5, 24, 15.5, SLOT[1], "C  ITR worker pool", title_y=0.86)
    for i in range(4):
        ax.add_patch(FancyBboxPatch((57.2 + i * 5.3, 22.6), 4.2, 3.6, boxstyle="round,pad=0.15,rounding_size=0.6",
                                    linewidth=0.6, edgecolor=SLOT[1], facecolor="#fdeee7"))
        ax.text(59.3 + i * 5.3, 24.4, f"w{i + 1}", ha="center", va="center", fontsize=5.8, color=INK2)
    ax.text(67, 19.3, "micro-batches of 5 clusters\nserial / static / dynamic queue", ha="center",
            va="center", fontsize=6.0, color=INK2, linespacing=1.25)
    box(55, 1, 24, 9, SLOT[0], "BBS result kept", "confidence ≥ τ")
    box(83.5, 9.5, 16, 11, INK2, "Selector", "ITR ok → ITR\nelse → BBS\n(length check: ITR\nonly if length fits)",
        title_y=0.8, sub_y=0.36)
    arrow(12.4, 15, 15.1, 15)
    arrow(32.9, 15, 35.6, 15)
    arrow(49.9, 17.5, 54.6, 22, "c < τ", 50.3, 21.5)
    arrow(49.9, 12.5, 54.6, 7, "c ≥ τ", 50.3, 8.4)
    arrow(79.4, 23, 83.1, 18.5)
    arrow(79.4, 5.5, 83.1, 11.5)
    ax.text(91.5, 6.2, "final sequence\n+ engine used", ha="center", va="top", fontsize=6.0, color=INK2)
    ax.text(50, -3.2, "Ground truth is used only after the final selection, for evaluation.", ha="center",
            va="bottom", fontsize=6.0, color=MUTED, style="italic")
    return fig


def f2_exact_by_condition(d: dict, cfg: dict):
    t = d["test_table"]
    t = t[t.split == "test"]
    syn = t[(t.family == "synthetic") & (t.group != "all")]
    order = condition_order(syn.group.unique())
    rows = [("microsoft", "all", "Microsoft (real)"), ("synthetic", "all", "Synthetic, pooled")]
    rows += [("synthetic", g, condition_label(g)) for g in order]
    fig, ax = plt.subplots(figsize=(COL1, 4.1))
    y = np.arange(len(rows))[::-1]
    for (fam, grp, _), yy in zip(rows, y):
        sub = t[(t.family == fam) & (t.group == grp)].set_index("method").exact_rate * 100
        ax.plot([sub.min(), sub.max()], [yy, yy], color=GRID, linewidth=2.2, zorder=1, solid_capstyle="round")
    for k, (method, (label, color, marker)) in enumerate(METHODS.items()):
        xs, ys = [], []
        for (fam, grp, _), yy in zip(rows, y):
            sub = t[(t.family == fam) & (t.group == grp) & (t.method == method)]
            if fam == "microsoft" and method == "adaptive":
                continue  # Microsoft τ 0: the cascade is BBS only (same point)
            if len(sub):
                xs.append(sub.exact_rate.iloc[0] * 100)
                ys.append(yy + (k - 1.5) * 0.09)
        ax.scatter(xs, ys, s=17, marker=marker, color=color, edgecolor="white", linewidth=0.5,
                   zorder=3, label=label)
    ax.set_yticks(y, [r[2] for r in rows])
    ax.axhline(y[1] - 0.5, color=AXIS, linewidth=0.6)
    ax.set_xlabel("Exact-match rate on the test split (%)")
    ax.set_xlim(0, 101)
    ax.grid(axis="y", visible=False)
    handles = [marker_handle(m, c, l) for l, c, m in METHODS.values()]
    handles.append(line_handle(GRID, "range across methods", width=2.2))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.42, 1.145), ncol=2,
              handletextpad=0.3, columnspacing=0.9)
    ax.set_ylabel("Error rate · coverage", labelpad=2)
    return fig


def f3_rescue_harm(d: dict, cfg: dict):
    t = d["test_table"]
    t = t[t.split == "test"]
    order = condition_order(t[(t.family == "synthetic") & (t.group != "all")].group.unique())
    rows = [("microsoft", "all", "Microsoft (real)"), ("synthetic", "all", "Synthetic, pooled")]
    rows += [("synthetic", g, condition_label(g)) for g in order]
    panels = [("itr_only", "(a) Full ITR"), ("adaptive", "(b) Cascade, primary"),
              ("adaptive_lengthcheck", "(c) Cascade, length check")]
    fig, axes = plt.subplots(1, 3, figsize=(COL2, 3.3), sharey=True)
    y = np.arange(len(rows))[::-1]
    for ax, (method, title) in zip(axes, panels):
        resc, harm = [], []
        for fam, grp, _ in rows:
            sub = t[(t.family == fam) & (t.group == grp) & (t.method == method)]
            resc.append(sub.rescued_vs_bbs.iloc[0] if len(sub) else np.nan)
            harm.append(sub.harmed_vs_bbs.iloc[0] if len(sub) else np.nan)
        resc, harm = np.array(resc, float), np.array(harm, float)
        # pooled rows dwarf per-condition rows: symmetric log scale keeps both readable
        ax.barh(y, resc, height=0.62, color=POS, label="Rescued: BBS wrong, method right")
        ax.barh(y, -harm, height=0.62, color=NEG, label="Harmed: BBS right, method wrong")
        ax.set_xscale("symlog", linthresh=10)
        lim = 3000
        ax.set_xlim(-lim, lim)
        ax.set_xticks([-1000, -100, -10, 0, 10, 100, 1000], ["1000", "100", "10", "0", "10", "100", "1000"],
                      fontsize=6)
        ax.axvline(0, color=INK2, linewidth=0.6)
        ax.axhline(y[1] - 0.5, color=AXIS, linewidth=0.6)
        for yy, r, h in zip(y, resc, harm):
            if np.isnan(r):
                ax.text(0, yy, "not run", ha="center", va="center", fontsize=5.6, color=MUTED)
                continue
            if r == 0 and h == 0 and yy == y[0]:
                ax.text(0, yy, "0 / 0 (τ 0 = BBS only)", ha="center", va="center", fontsize=5.6, color=MUTED,
                        bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
                continue
            if r:
                ax.text(r * 1.15 + 0.5, yy, f"{int(r)}", va="center", ha="left", fontsize=5.6, color=INK2)
            if h:
                ax.text(-h * 1.15 - 0.5, yy, f"{int(h)}", va="center", ha="right", fontsize=5.6, color=INK2)
        ax.set_title(title, loc="left")
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Clusters (symmetric log scale)")
    axes[0].set_yticks(y, [r[2] for r in rows])
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.55, 1.04), ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def f4_auroc(d: dict, cfg: dict):
    s, m = d["synthetic_test_cascade"], d["microsoft_test_cascade"]
    auc, lo, hi = (cascade_metric(s, k) for k in ("confidence", "confidence_ci95_low", "confidence_ci95_high"))
    nwrong = cascade_metric(s[s.section == "auroc"], "n_bbs_wrong")
    mauc, mlo, mhi = (cascade_metric(m, k)["all"] for k in ("confidence", "confidence_ci95_low", "confidence_ci95_high"))
    order = condition_order([g for g in auc.index if g != "all"])
    rows = [("Microsoft (real)", mauc, mlo, mhi, None), ("Synthetic, pooled", auc["all"], lo["all"], hi["all"], None)]
    rows += [(condition_label(g), auc[g], lo[g], hi[g], int(nwrong[g])) for g in order]
    fig, ax = plt.subplots(figsize=(COL1, 3.6))
    y = np.arange(len(rows))[::-1]
    for (label, a, l, h, n), yy in zip(rows, y):
        if np.isnan(a):
            ax.text(0.51, yy, f"not computable ({n} BBS failures)", va="center", fontsize=6, color=MUTED)
            continue
        has_ci = not (np.isnan(l) or np.isnan(h))
        if has_ci:
            ax.plot([l, h], [yy, yy], color=SLOT[0], linewidth=1.2, solid_capstyle="round")
        ax.scatter([a], [yy], s=18, color=SLOT[0], edgecolor="white", linewidth=0.5, zorder=3)
        note = f"{a:.2f}" if has_ci else f"{a:.2f} (CI n/a: {n} BBS failures)"
        ax.text((h if has_ci else a) + 0.012, yy, note, va="center", fontsize=6, color=INK2)
    ax.axvline(0.5, color=AXIS, linewidth=0.8, linestyle=(0, (3, 2)))
    handles = [marker_handle("o", SLOT[0], "AUROC"), line_handle(SLOT[0], "95% bootstrap CI", width=1.2),
               line_handle(AXIS, "chance (0.5)", style=(0, (3, 2)), width=0.8)]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.45, 1.075), ncol=3, handlelength=1.6)
    ax.axhline(y[1] - 0.5, color=AXIS, linewidth=0.6)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlim(0.44, 1.12)
    ax.set_xticks([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    ax.set_xlabel("AUROC of BBS confidence for predicting BBS failure (test)")
    ax.grid(axis="y", visible=False)
    return fig


def f5_tau_tradeoff(d: dict, cfg: dict):
    frozen = cfg["frozen_tau"]
    fig, axes = plt.subplots(1, 2, figsize=(COL2, 2.6))
    panels = [("synthetic", d["synthetic_dev_sweep"], d["synthetic_test_sweep"], "(a) Synthetic grid (13 conditions)"),
              ("microsoft", d["microsoft_dev_sweep"], d["microsoft_test_sweep"], "(b) Microsoft (real Nanopore)")]
    for ax, (fam, dev, test, title) in zip(axes, panels):
        if "dataset_id" in dev:
            dev, test = dev[dev.dataset_id == "all"], test[test.dataset_id == "all"]
        for selector, method in (("default", "adaptive"), ("length_check", "adaptive_lengthcheck")):
            label, color, marker = METHODS[method]
            dv = dev[dev.selector == selector].sort_values("tau")
            ax.plot(dv.fallback_ratio * 100, dv.exact_rate * 100, color=color, marker=marker,
                    markersize=3.2, label=f"{label}: τ sweep (dev)")
            tau = frozen[fam].get(selector)
            if tau is None:
                continue
            ts = test[(test.selector == selector) & np.isclose(test.tau, tau)]
            ax.scatter(ts.fallback_ratio * 100, ts.exact_rate * 100, s=46, marker=marker, facecolor="white",
                       edgecolor=color, linewidth=1.3, zorder=4)
            ax.annotate(f"τ {tau:g}, test", (ts.fallback_ratio.iloc[0] * 100, ts.exact_rate.iloc[0] * 100),
                        textcoords="offset points",
                        xytext=(6, 6) if selector == "length_check" else ((26, -3) if tau == 0 else (6, -11)),
                        fontsize=6, color=INK2, bbox=dict(facecolor="white", edgecolor="none", pad=0.6))
        tt = d["test_table"]
        ref = tt[(tt.family == fam) & (tt.group == "all") & (tt.split == "dev")].set_index("method").exact_rate * 100
        for method in ("bbs_only", "itr_only"):
            ax.axhline(ref[method], color=METHODS[method][1], linewidth=0.9, linestyle=(0, (3, 2)),
                       label={"bbs_only": "BBS only (dev)", "itr_only": "Full ITR (dev)"}[method])
        ax.set_title(title, loc="left")
        ax.set_xlabel("Clusters routed to ITR (%)")
        ax.set_xlim(-2, 102)
    axes[0].set_ylabel("Exact-match rate (%)")
    axes[0].set_ylim(74, 93)
    axes[1].set_ylim(86, 97.5)
    for ax in axes:
        ax.yaxis.set_major_locator(matplotlib.ticker.MultipleLocator(2 if ax is axes[1] else 4))
    handles, labels = axes[0].get_legend_handles_labels()
    handles += [marker_handle("^", METHODS["adaptive"][1], "Primary: frozen τ on test", hollow=True, size=7),
                marker_handle("D", METHODS["adaptive_lengthcheck"][1], "Length check: frozen τ on test",
                              hollow=True, size=6)]
    labels += [h.get_label() for h in handles[-2:]]
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.1), ncol=3)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    return fig


def f6_speedup(d: dict, cfg: dict):
    cells, runs = d["scaling_cells"], d["scaling_runs"]
    t1 = cells.set_index("cell").loc["serial_p1", "makespan_s_median"]
    runs = runs.assign(speedup=t1 / runs.makespan_s)
    fig, axes = plt.subplots(1, 2, figsize=(COL2, 2.4))
    ws = [1, 2, 4, 8]
    for ax, metric in zip(axes, ("speedup", "efficiency")):
        ax.axvspan(cfg["physical_cores"] + 0.5, 8.6, color=HT_SHADE, zorder=0)
        ideal = ws if metric == "speedup" else [1] * len(ws)
        ax.plot(ws, ideal, color=AXIS, linewidth=1.0, linestyle=(0, (3, 2)), label="ideal")
        for sched in ("dynamic", "static"):
            label, color, marker = SCHEDULERS[sched]
            c = cells[cells.scheduler == sched].sort_values("workers")
            c = pd.concat([cells[cells.cell == "serial_p1"], c]) if sched == "static" else c
            r = runs[runs.scheduler.isin([sched, "serial"] if sched == "static" else [sched])]
            g = r.groupby("workers").speedup
            lo, hi = g.min(), g.max()
            p = c.workers.values
            mid = c.speedup.values if metric == "speedup" else c.efficiency.values
            div = 1 if metric == "speedup" else lo.index.values
            ax.plot(p, mid, color=color, marker=marker,
                    label=label + (" (1 worker = serial run)" if sched == "static" else ""))
            ax.vlines(lo.index, lo.values / div, hi.values / div, color=color, linewidth=1.0)
        ax.set_xticks(ws, [str(w) for w in ws])
        ax.set_xlim(0.6, 8.6)
        ax.set_xlabel("Workers")
    axes[0].set_ylabel("Speedup over serial (whole run)")
    axes[0].set_ylim(0, 8.4)
    axes[1].set_ylabel("Efficiency (speedup / workers)")
    axes[1].set_ylim(0, 1.1)
    axes[0].set_title("(a) Speedup", loc="left")
    axes[1].set_title("(b) Efficiency", loc="left")
    handles, labels = axes[0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], color=INK2, marker="|", markersize=7, linestyle="", markeredgewidth=1.0),
                patch_handle(HT_SHADE, "")]
    labels += ["min–max of 3 repetitions", "hyper-threaded (> 4 physical cores)"]
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.88), ncol=3)
    return fig


def f7_schedulers(d: dict, cfg: dict):
    runs = d["scaling_runs"]
    fig, axes = plt.subplots(1, 2, figsize=(COL2, 2.3))
    groups = [(2, ["dynamic", "static"]), (4, ["dynamic", "static", "static_lpt"]), (8, ["dynamic", "static"])]
    xt, xl = [], []
    x = 0
    for p, scheds in groups:
        for sched in scheds:
            label, color, marker = SCHEDULERS[sched]
            r = runs[(runs.workers == p) & (runs.scheduler == sched)]
            for ax, col in zip(axes, ("makespan_s", "imbalance")):
                jitter = np.linspace(-0.12, 0.12, len(r))
                ax.scatter(x + jitter, r[col], s=16, marker=marker, color=color, edgecolor="white",
                           linewidth=0.5, zorder=3, label=label if p == 4 else None)
                ax.hlines(r[col].median(), x - 0.25, x + 0.25, color=color, linewidth=1.2)
            xt.append(x)
            xl.append(label.replace("static LPT", "LPT"))
            x += 1.25
        x += 0.5
    for ax in axes:
        ax.set_xticks(xt, [""] * len(xt))
        ax.tick_params(axis="x", length=0)
        ax.grid(axis="x", visible=False)
        trans = ax.get_xaxis_transform()
        starts = [0, 3.0, 7.25]
        for (p, scheds), start in zip(groups, starts):
            ax.text(start + (len(scheds) - 1) * 1.25 / 2, -0.06, f"{p} workers" + (" (HT)" if p > cfg["physical_cores"] else ""),
                    transform=trans, ha="center", va="top", fontsize=6.5, color=INK2)
    axes[0].set_ylabel("Makespan (s)")
    axes[1].set_ylabel("Imbalance (max / mean busy time)")
    axes[1].axhline(1, color=AXIS, linewidth=0.8, linestyle=(0, (3, 2)))
    axes[0].set_title("(a) Makespan", loc="left")
    axes[1].set_title("(b) Load imbalance", loc="left")
    handles = [marker_handle(m, c, l) for k, (l, c, m) in SCHEDULERS.items() if k != "serial"]
    handles += [line_handle(INK2, "median of 3 repetitions (dots)", width=1.2),
                line_handle(AXIS, "perfect balance (1.0)", style=(0, (3, 2)), width=0.8)]
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.88), ncol=5, handletextpad=0.3)
    return fig


def f8_contention(d: dict, cfg: dict):
    c = d["contention"]
    if c.dirty.any():
        raise SystemExit("contention probe data comes from a dirty tree")
    per_rep = c.groupby(["kind", "copies", "repetition"]).seconds.median().reset_index()
    base = per_rep[per_rep.copies == 1].groupby("kind").seconds.median()
    med = c.groupby(["kind", "copies"]).seconds.median().reset_index()
    med["slowdown"] = med.seconds / med.kind.map(base)
    fig, ax = plt.subplots(figsize=(COL1, 2.4))
    copies = sorted(med.copies.unique())
    ax.plot(copies, copies, color=AXIS, linewidth=1.0, linestyle=(0, (1, 1.5)))
    ax.axhline(1, color=AXIS, linewidth=1.0, linestyle=(0, (3, 2)))
    ax.axvspan(cfg["physical_cores"] * 1.41, 9.5, color=HT_SHADE, zorder=0)
    for kind, (label, color, marker) in KERNELS.items():
        k = med[med.kind == kind].sort_values("copies")
        ax.plot(k.copies, k.slowdown, color=color, marker=marker, label=label)
        ax.text(k.copies.iloc[-1] * 1.04, k.slowdown.iloc[-1], f"{k.slowdown.iloc[-1]:.1f}×", va="center",
                fontsize=6, color=INK2)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    ax.set_xticks(copies, [str(int(x)) for x in copies])
    ax.set_yticks([1, 2, 4, 8], ["1×", "2×", "4×", "8×"])
    ax.minorticks_off()
    ax.set_xlim(0.85, 10.5)
    ax.set_ylim(0.8, 10)
    ax.set_xlabel("Identical processes started together")
    ax.set_ylabel("Per-process slowdown (median)")
    handles, labels = ax.get_legend_handles_labels()
    handles += [line_handle(AXIS, "no slowdown (perfect scaling)", style=(0, (3, 2)), width=1.0),
                line_handle(AXIS, "slowdown = copies (no gain)", style=(0, (1, 1.5)), width=1.0),
                patch_handle(HT_SHADE, "> 4 copies (hyper-threading)")]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, columnspacing=1.0)
    return fig


def f9_time_vs_accuracy(d: dict, cfg: dict):
    t = d["test_table"]
    t = t[(t.split == "test") & (t.family == "synthetic") & (t.group == "all")].set_index("method").exact_rate * 100
    cells = d["scaling_cells"].set_index("cell")
    itr = d["itr_only_cells"].set_index("cell")
    cost = d["routed_cost"].set_index("method")
    points = {  # method -> {workers: seconds}; None = estimate
        "bbs_only": {1: cells.loc["serial_p1", "bbs_s_median"], 4: cells.loc["dynamic_p4", "bbs_s_median"]},
        "adaptive": {1: cells.loc["serial_p1", "makespan_s_median"], 4: cells.loc["dynamic_p4", "makespan_s_median"]},
        "itr_only": {1: itr.loc["serial_p1", "makespan_s_median"], 4: itr.loc["dynamic_p4", "makespan_s_median"]},
    }
    # The routed-cost sums come from the full-ITR day; rescale to the cascade day by the primary
    # cascade's own ratio (its ITR stage as measured / the same clusters on the full-ITR day).
    day = cells.loc["serial_p1", "itr_s_median"] / cost.loc["adaptive", "routed_itr_s_median"]
    lc_est = cells.loc["serial_p1", "bbs_s_median"] + cost.loc["adaptive_lengthcheck", "routed_itr_s_median"] * day
    fig, ax = plt.subplots(figsize=(COL1, 2.6))
    for method, by_p in points.items():
        label, color, marker = METHODS[method]
        xs = [by_p[1], by_p[4]]
        ax.plot(xs, [t[method]] * 2, color=color, linewidth=1.0)
        ax.scatter([by_p[1]], [t[method]], s=30, marker=marker, color=color, edgecolor="white", linewidth=0.5,
                   zorder=3)
        ax.scatter([by_p[4]], [t[method]], s=30, marker=marker, facecolor="white", edgecolor=color,
                   linewidth=1.2, zorder=3)
        offset, ha = {"bbs_only": ((6, 5), "left"), "adaptive": ((-4, -12), "center"),
                      "itr_only": ((0, -12), "center")}[method]
        x_mid = np.sqrt(by_p[1] * by_p[4])  # label centred on the serial–4-worker segment (log axis)
        ax.annotate(label, (x_mid if ha == "center" else by_p[1], t[method]), textcoords="offset points",
                    xytext=offset, ha=ha, fontsize=6.2, color=INK2)
    label, color, marker = METHODS["adaptive_lengthcheck"]
    ax.scatter([lc_est], [t["adaptive_lengthcheck"]], s=30, marker=marker, color=color, edgecolor=INK,
               linewidth=0.9, zorder=3)
    ax.annotate(label, (lc_est, t["adaptive_lengthcheck"]), textcoords="offset points",
                xytext=(0, 9), ha="center", va="bottom", fontsize=6.2, color=INK2)
    ax.set_xscale("log")
    ax.set_xticks([10, 30, 100, 300, 1000, 3000], ["10", "30", "100", "300", "1,000", "3,000"])
    ax.minorticks_off()
    ax.set_xlim(8, 6000)
    ax.set_ylim(72, 95)
    ax.set_xlabel("Wall time on 9,100 test clusters (s, log scale)")
    ax.set_ylabel("Exact-match rate, test (%)")
    handles = [marker_handle("o", MUTED, "1 worker (serial)"),
               marker_handle("o", MUTED, "4 workers (dynamic)", hollow=True),
               marker_handle("o", MUTED, "1 worker, estimated", edge=INK)]
    ax.legend(handles=handles, loc="lower right", title="measured unless noted", title_fontsize=6.2)
    return fig


FIGURES = {"f1_pipeline": f1_pipeline, "f2_exact_by_condition": f2_exact_by_condition,
           "f3_rescue_harm": f3_rescue_harm, "f4_auroc": f4_auroc, "f5_tau_tradeoff": f5_tau_tradeoff,
           "f6_speedup": f6_speedup, "f7_schedulers": f7_schedulers, "f8_contention": f8_contention,
           "f9_time_vs_accuracy": f9_time_vs_accuracy}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", nargs="+", help="figure names or prefixes (e.g. f2 f5)")
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    data = {name: pd.read_csv(ROOT / path) for name, path in cfg["inputs"].items()}
    out = ROOT / cfg["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    style()
    chosen = [n for n in FIGURES if not args.only or any(n.startswith(o) for o in args.only)]
    if not chosen:
        raise SystemExit(f"no figure matches {args.only}")
    for name in chosen:
        fig = FIGURES[name](data, cfg)
        for fmt in cfg["formats"]:
            fig.savefig(out / f"{name}.{fmt}", dpi=cfg["dpi"])
        plt.close(fig)
        print(f"wrote {cfg['output_dir']}/{name}.{{{','.join(cfg['formats'])}}}")


if __name__ == "__main__":
    main()

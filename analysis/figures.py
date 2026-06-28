"""Paper figures from the analysis CSVs (matplotlib only — no pandas/numpy needed).

  analysis/fig_rq2_advantage.{png,pdf}      - RQ2 cost-benefit heatmap: CCR's net-utility
        advantage over the BEST baseline per (V, kappa) mixed_I cell; winner annotated.
  analysis/fig_lambda_r_crossover.{png,pdf} - CCR net utility minus best baseline as the
        incident price lambda_r varies, for the caveat cells (crossing zero = CCR takes
        the lead). Makes the "the winner depends on how you price an incident" point visual.

Reads the newest analysis/rq2_costbenefit_*.csv and analysis/lambda_r_sweep_*.csv, so run
`analyze.py --ci` and `lambda_r_sweep.py` first. Needs matplotlib (analysis/requirements.txt).

Usage: python analysis/figures.py
"""
from __future__ import annotations

import csv
import glob
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSIS = os.path.join(ROOT, "analysis")
BASELINES = ["optimistic_fallback", "attestation_only", "card_only", "embedding"]
ABBR = {"ccr": "CCR", "optimistic_fallback": "OPT", "attestation_only": "ATT",
        "card_only": "CARD", "embedding": "EMB", "random": "RND"}


def _newest(pattern: str) -> str:
    files = glob.glob(os.path.join(ANALYSIS, pattern))
    if not files:
        raise SystemExit(f"No file matches {pattern} — run analyze.py / lambda_r_sweep.py first.")
    return max(files, key=os.path.getmtime)


def _save(fig, stem: str):
    for ext in ("png", "pdf"):
        path = os.path.join(ANALYSIS, f"{stem}.{ext}")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        print(f"Wrote {path}")
    plt.close(fig)


def rq2_advantage_heatmap():
    rows = list(csv.DictReader(open(_newest("rq2_costbenefit_*.csv"), encoding="utf-8")))
    bases = [b for b in BASELINES if b in rows[0]]
    Vs = sorted({int(r["V"]) for r in rows})
    Ks = sorted({float(r["kappa"]) for r in rows})
    adv = [[0.0] * len(Ks) for _ in Vs]
    win = [[""] * len(Ks) for _ in Vs]
    for r in rows:
        i, j = Vs.index(int(r["V"])), Ks.index(float(r["kappa"]))
        ccr = float(r["ccr"])
        bb = max(float(r[b]) for b in bases if r[b] not in ("", "None"))
        adv[i][j] = ccr - bb
        win[i][j] = ABBR.get(r["winner"], r["winner"])

    flat = [v for row in adv for v in row]
    lim = max(abs(min(flat)), abs(max(flat))) or 1.0
    norm = TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim)

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    im = ax.imshow(adv, cmap="RdYlGn", norm=norm, aspect="auto")
    ax.set_xticks(range(len(Ks)), [f"{k:g}" for k in Ks])
    ax.set_yticks(range(len(Vs)), [str(v) for v in Vs])
    ax.set_xlabel(r"canary cost  $\kappa$")
    ax.set_ylabel(r"task value  $V$")
    ax.set_title("RQ2: CCR net-utility advantage over the best baseline (mixed, indistinguishable)")
    for i in range(len(Vs)):
        for j in range(len(Ks)):
            ax.text(j, i, f"{win[i][j]}\n{adv[i][j]:+.1f}", ha="center", va="center",
                    fontsize=7.5, color="black")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(r"$U_{\mathrm{CCR}} - \max_b U_b$  (green = CCR wins)")
    _save(fig, "fig_rq2_advantage")


def lambda_r_crossover():
    rows = list(csv.DictReader(open(_newest("lambda_r_sweep_*.csv"), encoding="utf-8")))
    by = defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)

    # Crossover (caveat-#2) cells: indistinguishable hostile/mixed where CCR TRAILS at
    # lambda_r=2 but OVERTAKES as the incident price rises (i.e. the best baseline at L=2
    # has more incidents than CCR, so pricing them flips it). We focus on the realistic
    # crossover region (lambda_r <= XCAP): under the FROZEN post-hoc reprice, at extreme
    # lambda_r the zero-incident `embedding` baseline eventually re-overtakes CCR (CCR would
    # re-route in reality — see the decision-coupled sweep, sec 8.5), which is not the flip
    # this figure is about. So we test "crosses above 0 somewhere past lambda_r=2", not the
    # endpoint value.
    XCAP = 8.0
    cross = []
    for cond, rs in by.items():
        if not (cond.endswith("_I") and ("_hostile_" in cond or "_mixed_" in cond)):
            continue
        rs = sorted((r for r in rs if float(r["lambda_r"]) <= XCAP),
                    key=lambda r: float(r["lambda_r"]))
        y_at2 = float(min(rs, key=lambda r: abs(float(r["lambda_r"]) - 2.0))["ccr_minus_best"])
        y_above = max(float(r["ccr_minus_best"]) for r in rs if float(r["lambda_r"]) > 2.0)
        if y_at2 < 0 and y_above > 0:        # trails at L=2, overtakes as the incident is priced
            cross.append((cond, rs))
    if not cross:
        print("No crossover cells — skipping lambda_r figure.")
        return

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    for cond, rs in sorted(cross):
        xs = [float(r["lambda_r"]) for r in rs]
        ys = [float(r["ccr_minus_best"]) for r in rs]
        ax.plot(xs, ys, marker="o", ms=3, lw=1.5, label=cond[:-2].replace("_", " "))
    ax.axhline(0, color="black", lw=1)
    ax.axvline(2.0, color="gray", ls=":", lw=1)
    ax.text(2.05, ax.get_ylim()[1], r" deployed $\lambda_r{=}2$", va="top", fontsize=7.5, color="gray")
    ax.set_xlabel(r"incident price  $\lambda_r$")
    ax.set_ylabel(r"$U_{\mathrm{CCR}} - \max_b U_b$")
    ax.set_title("CCR overtakes the best baseline once an incident is priced (crossing 0)")
    ax.legend(fontsize=7.5, title="crossover cell", loc="center left",
              bbox_to_anchor=(1.02, 0.5), frameon=False)
    _save(fig, "fig_lambda_r_crossover")


def main():
    rq2_advantage_heatmap()
    lambda_r_crossover()


if __name__ == "__main__":
    main()

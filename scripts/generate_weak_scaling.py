#!/usr/bin/env python3

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "results" / "post-pscw-2026-04-20" / "run_weak_scaling_out.csv"
OUTPUTS = [
    ROOT / "docs" / "figures" / "ppp_weak_scaling.png",
    ROOT / "scripts" / "ppp_weak_scaling.png",
]


def impl_name(row):
    mpi = int(row["mpi_procs"])
    omp = int(row["omp_threads"])
    tiles_x = int(row["grid_tiles_x"])
    tiles_y = int(row["grid_tiles_y"])

    if omp == 1:
        return "MPI 2D"
    if tiles_y == 1:
        return "Hybrid 1D"
    return "Hybrid 2D"


def main():
    series = defaultdict(list)

    with INPUT.open(newline="") as csvfile:
        for row_index, row in enumerate(csv.DictReader(csvfile, delimiter=";")):
            # run_weak_scaling.sh emits rows in groups of six:
            # MPI P2P, MPI RMA, Hybrid 1D P2P, Hybrid 1D RMA,
            # Hybrid 2D P2P, Hybrid 2D RMA.
            if row_index % 6 not in (0, 2, 4):
                continue

            mpi = int(row["mpi_procs"])
            if mpi == 1:
                continue

            name = impl_name(row)
            omp = int(row["omp_threads"])
            cores = mpi * omp

            series[name].append((cores, float(row["iteration_time"])))

    order = ["MPI 2D", "Hybrid 1D", "Hybrid 2D"]
    colors = {"MPI 2D": "#1f77b4", "Hybrid 1D": "#ff7f0e", "Hybrid 2D": "#2ca02c"}
    markers = {"MPI 2D": "o", "Hybrid 1D": "s", "Hybrid 2D": "^"}

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), dpi=300)
    fig.suptitle("Weak Scaling - 65,536 cells/MPI rank", fontsize=11, fontweight="bold")

    for name in order:
        points = sorted(series[name])
        if not points:
            continue

        cores = [p[0] for p in points]
        iter_times_ms = [p[1] * 1000.0 for p in points]
        baseline = iter_times_ms[0]
        efficiency = [baseline / t * 100.0 for t in iter_times_ms]

        axes[0].plot(
            cores,
            iter_times_ms,
            marker=markers[name],
            linewidth=1.5,
            markersize=4,
            label=name,
            color=colors[name],
        )
        axes[1].plot(
            cores,
            efficiency,
            marker=markers[name],
            linewidth=1.5,
            markersize=4,
            label=name,
            color=colors[name],
        )

    axes[0].set_title("Iteration time (flat = perfect scaling)", fontsize=10)
    axes[0].set_xlabel("Total cores")
    axes[0].set_ylabel("Iteration time [ms]")

    axes[1].set_title("Weak-scaling efficiency", fontsize=10)
    axes[1].set_xlabel("Total cores")
    axes[1].set_ylabel("Weak-scaling efficiency [%]")
    axes[1].set_ylim(0, 120)
    axes[1].set_yticks([0, 20, 40, 60, 80, 100, 120])
    axes[1].axhline(100, color="#888888", linestyle="--", linewidth=0.8, label="Ideal")

    for ax in axes:
        ax.set_xscale("log", base=2)
        ax.set_xticks([4, 16, 32, 64, 128, 256])
        ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=8, loc="best")

    fig.tight_layout()
    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    main()

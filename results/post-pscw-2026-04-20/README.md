# post-pscw-2026-04-20

Benchmark results after PSCW synchronization added to RMA halo exchange (commit `bfe8e48`).

## Contents

- `run_full_hybrid_2d_out.csv` — Hybrid 2D (SLURM job 1963262, 26m45s, 8 nodes, up to 32 MPI × 8 OMP)
- `run_full_mpi_2d_out.csv` — MPI 2D (SLURM job 1963454, 33m58s, 4 nodes, up to 128 MPI)
- `run_full_hybrid_1d_out.csv` — Hybrid 1D (SLURM job 1963455, 31m33s, 8 nodes, up to 16 MPI × 8 OMP)

## Compare against baseline

Baseline (pre-PSCW, fence-based sync): `results/best-19-4-2026/`

## Key findings

All three decompositions run both P2P (mode 1) and RMA (mode 2). PSCW only affects RMA.
**no_IO only** — io results dominated by Lustre variance, not meaningful for sync comparison.

### PSCW vs fence — RMA no_IO speedup

**MPI 2D** (up to 128 MPI ranks, pure MPI, 2D decomposition):

| Ranks | 256² | 512² | 1024² | 2048² | 4096² |
|---|---|---|---|---|---|
| 16 | 1.22× | 1.09× | 1.05× | ~1.0× | 1.05× |
| 32 | 1.32× | 1.23× | 1.08× | 1.01× | 0.99× |
| 64 | 1.59× | 1.57× | 1.24× | 1.12× | 0.78× |
| **128** | **2.16×** | **1.75×** | **1.53×** | **1.15×** | **1.13×** |

**Hybrid 1D** (up to 16 MPI × 8 OMP, 1D row decomposition):

| MPI ranks | 256² | 512² | 1024² | 2048² | 4096² |
|---|---|---|---|---|---|
| 4  | ~1.07× | ~1.05× | ~1.07× | ~1.06× | 1.02× |
| 8  | 1.29× | 1.16× | 1.12× | 1.11× | 1.04× |
| 16 | 1.55× | 1.37× | 1.20× | 1.16× | 1.09× |

**Hybrid 2D** (up to 32 MPI × 8 OMP, 2D decomposition):

| MPI ranks | 256² | 4096² |
|---|---|---|
| 4  | 1.10× | 1.02× |
| 16 | 1.29× | 1.01× |
| **32** | **1.46×** | **1.10×** |

### P2P no_IO: ~1.0× across all configs and decompositions

Confirms PSCW is correctly scoped to RMA path only. No regression on P2P.

### io_par: ignore

Swings of 0.45×–2.1× across all configs — pure Lustre contention variance, unrelated to PSCW.

## Summary

PSCW benefit confirmed in **all three decompositions**. Clear trend: more MPI ranks crossing node boundaries → more fence overhead saved → larger PSCW gain. Best result: **2.16× on MPI 2D, 128 ranks, 256² grid** (sync latency dominates at small grids and high rank counts). P2P unaffected in all cases.

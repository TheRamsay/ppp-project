# PPP Project 1 — Parallel Heat Equation Solver

## Project
- Student: Dominik Huml (xhumld00), VUT FIT, PPP 2023/2024
- Deadline: **2026-04-30 23:59:59**
- Submission files: `ParallelHeatSolver.hpp`, `ParallelHeatSolver.cpp`, `xhumld00.pdf`
- Teachers: gnecasov, jarosjir

## Build

**Local (macOS, debug, no OMP)**
```sh
cmake -B build -S . -DLOGIN=xhumld00 -DALLOW_OPENMP=OFF -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

**Barbora (release)**
```sh
source scripts/load_modules.sh
cmake -B build -S . -DLOGIN=xhumld00 -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

**Barbora (Score-P)**
```sh
export SCOREP_WRAPPER_INSTRUMENTER_FLAGS=--thread=omp
SCOREP_WRAPPER=off cmake -DCMAKE_CXX_COMPILER=scorep-mpiicpc -DLOGIN=xhumld00 -DCMAKE_BUILD_TYPE=RelWithDebInfo -Bbuild_prof -S.
cmake --build build_prof --config RelWithDebInfo
```

**Verify correctness** (local, both modes give deviation=0):
```sh
mpiexec -np 4 ./build/ppp_proj01 -n 100 -m 1 -w 50 -i test_input.h5 -v   # P2P
mpiexec -np 4 ./build/ppp_proj01 -n 100 -m 2 -w 50 -i test_input.h5 -v   # RMA
```

## Barbora
- SLURM account: `ATR-25-7`
- Scratch: `/scratch/project/atr-25-7/$USER/`
- Score-P backups: `/scratch/project/atr-25-7/$USER/scorep_backup/`
- Hardware (`login2`, verified via lscpu): **Intel Xeon Gold 6240 (Cascade Lake)**, 2 sockets × 18 cores = **36 cores/node**, 2 NUMA domains, 24.75 MB L3/socket, 190 GB RAM. HT off on compute (`ThreadsPerCore=1`).
- Scripts use `--ntasks-per-node=32` → **4 cores/node idle** (course-provided setting).

## Optimizations done (with microbenchmark attribution)

Controlled attribution via `scripts/halo_modes_benchmark.cpp` (16 ranks, 1-2 nodes, 5 modes × varied tile sizes). Raw outputs in `results/benchmarks-random/`, summary in `SUMMARY.md`.

1. **`MPI_Get` → `MPI_Put` (dominant RMA fix, 55-80×)** — initial impl used strided `MPI_Get` with derived datatypes; catastrophic on Cascade Lake + Intel MPI RDMA path. Replacing with `MPI_Put` alone gives bulk of the improvement. Reference implementations (Repk1ns 2023/2024) used `MPI_Put` from the start.
2. **Packing (secondary, 1.5-3× on strided halos, −40% on contiguous halos)** — `MPI_Pack` → contiguous `MPI_Put` → `MPI_Unpack` in `startHaloExchangeRMA`/`awaitHaloExchangeRMA`. Net positive for all used decomps (MPI 2D, Hybrid 2D, Hybrid 1D wide strips — all have `processTileX > 1` = vertical halos). Would hurt on 1D tall strips (not in benchmark sweeps).
3. **PSCW sync (`MPI_Win_post/start/complete/wait`) replacing `MPI_Win_fence`** — neighbor-only synchronization. 1.15× single node, **1.74× at 2 nodes**, projected 2-3× at 4-8 nodes. Uses `mNeighborGroup` built from Cart shifts in `initHaloExchange`. Commit `bfe8e48`.
4. **HDF5 chunk fix (4220× parallel-IO speedup)** — `H5Pset_chunk(…, gridSize.data())` instead of tile-size chunks. One collective chunk, 128-proc par IO 2954s → 0.7s.
5. **Lustre striping** — `lfs setstripe -S 1M -c 16` in all benchmark scripts.

### Combined stack (naive `MPI_Get` + strided + fence → optimized)
- Single node 2D: **164× faster**
- Single node 1D wide: **268× faster**
- 2 nodes 2D: **133× faster**

## Benchmark outcomes (from dashboard analysis)
- Best speedup: ~650× on 4096² Hybrid P2P at 256 cores (super-linear, cache effect)
- P2P ≈ RMA parity confirmed after packing fix (ratio ~1.0×)
- **Hybrid 2D wins most matched configs** (56/90), MPI 2D wins on small-grid P2P no_IO, Hybrid 1D rarely best
- Par_IO ≈ no_IO on large grids (chunk fix working)
- Results dirs: `results/old_rma_strided/` (pre-fix baseline), `results/best-19-4-2026/` (snapshot 2026-04-19 with plots + dashboard)
- Dashboard embeds both datasets; switcher in Filters section lets you compare optimized vs baseline

## Dashboard
- Repo: `TheRamsay/ppp-dashboard` (public) · Live: https://theramsay.github.io/ppp-dashboard/
- Builder: `uv run scripts/build_dashboard.py` → writes `scripts/dashboard.html`
- Deploy update: `cp scripts/dashboard.html ../ppp-dashboard/index.html && (cd ../ppp-dashboard && git add index.html && git commit -m "…" && git push)`

## Score-P
- Configs profiled: 1D + 2D, P2P + RMA, 16 procs, 1024 grid, 100 iters. Tracing with `ppp_scorep_filter.flt`.
- **Old backup (Apr 12 — pre-packing, pre-PSCW)**: `/scratch/project/atr-25-7/ramsay/scorep_backup/scorep-20260412_*` (10 profiles). Shows strided Get + fence overhead. Use as "before" evidence.
- **New profile (post-PSCW)**: TODO — rebuild `build_prof` with current source, resubmit `scripts/run_scorep_profile.sh`.
- Key finding (pre-fix): P2P MPI overhead ~3%, RMA fence overhead ~30%.

## Known gotchas
- `stdbuf -oL` on `srun` to avoid NFS truncation on Barbora.
- Home quota ~23 GB — keep traces on scratch.
- `SimulationProperties.cpp` / `data_generator.cpp` have upstream cxxopts 3.2.0 bug with `f`-suffixed float defaults. Not submission files.

## TODO
- [ ] **Rebuild `build_prof` on Barbora** with PSCW source, submit new Score-P profile.
- [ ] **Hybrid 2D benchmark with PSCW binary** — job `1963262` pending, validates production PSCW benefit.
- [ ] **Weak scaling benchmark** — currently NOT submitted (was `1963235`, now missing). Resubmit or extract from existing strong-scaling CSVs.
- [ ] **Write `xhumld00.pdf` (20 pts)** — 1–2 pages text + graphs. Core narrative: controlled microbenchmark attributes 163× RMA improvement to Get→Put (dominant), packing (secondary), PSCW (scale-dependent). HDF5 chunk story. Super-linear cache explanation.
- [ ] **Oral presentation (10 pts)** — 5 min. Dashboard is strong demo material.
- [ ] `cmake --build build --target pack` → submission zip.
- ~~Optional: fix `computeHaloZones` OMP fork/join (~20% Hybrid 2D)~~ — decided not worth it, low ROI vs report time.
- ~~Optional: conditional packing for 1D tall strips~~ — not applicable; all used configs have `processTileX > 1`.

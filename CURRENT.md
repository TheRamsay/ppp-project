# Current Status — PPP Project

Working doc. Updated ad-hoc. Refer to `CLAUDE.md` for stable project info.

## Timeline
- **Today:** 2026-04-20
- **Deadline:** 2026-04-30 23:59:59
- **Days left:** ~10

## Current state of code

### Committed & pushed
- `bfe8e48` **perf: replace MPI_Win_fence with PSCW for neighbor-only RMA sync** (today's main change)
- `21f7f99` perf: use full grid chunk size for parallel HDF5 I/O
- `0f400d0` refactor: use MPI_Pack/Unpack instead of manual loops
- `a11342b` feat: optimize RMA halo exchange with contiguous pack/put (original packing)

### Verification status
- Local (macOS, OpenMPI, 4 procs): ✅ `Max deviation: 0` for both P2P and RMA
- Barbora (Intel MPI, login node, 4 procs, 1024²): ✅ `Max deviation: 3.2e-5, Verification OK`

## Barbora jobs

| JobID | Name | State | Notes |
|---|---|---|---|
| 1963262 | PPP_PROJ01_HYBRID_2D | PENDING | Scheduled ~21:36 today. Production test of PSCW. |
| ~~1963235~~ | weak scaling | MISSING | Not in queue — either completed & missing outputs, or cancelled. |

## What's on Barbora

- `~/ppp-project` at commit `bfe8e48` (PSCW)
- `~/ppp-project/build/ppp_proj01` — REBUILT today with PSCW ✅
- `~/ppp-project/build_prof/ppp_proj01` — **STALE** from Apr 12 (pre-packing, pre-PSCW). Needs rebuild.
- `~/ppp-project/scripts/run_full_hybrid_2d_out.csv` — Apr 13 baseline (will be overwritten when 1963262 runs)
- `~/ppp-project/scripts/halo_modes_benchmark.cpp` — microbenchmark binary

## Data snapshot

- `results/best-19-4-2026/` — strong-scaling CSVs + 18 PNG plots + dashboard.html (from yesterday, pre-PSCW baseline)
- `results/old_rma_strided/` — pre-fix baseline (Apr 12)
- `results/benchmarks-random/` — microbenchmark outputs + SUMMARY.md (factor attribution)

## Remaining tasks (priority order)

### 🔴 Must do
1. **Rebuild `build_prof` on Barbora with PSCW source** — required for new Score-P profile
2. **Submit Score-P profile** (small job, should queue fast) — for report "after" screenshots
3. **Wait for job 1963262** to complete (Hybrid 2D production run with PSCW)
4. **Write `xhumld00.pdf`** (20 pts) — 1-2 pages, answers 5 required questions
5. **Generate Score-P screenshots** (Cube + Vampir) once profile completes
6. **`cmake --build build --target pack`** → submission zip

### 🟡 Should do
7. **Resubmit weak scaling** OR extract from existing CSVs as fallback
8. **Oral presentation prep** (10 pts, 5 min)

### 🟢 Done today
- ✅ PSCW implementation + commit + push
- ✅ Microbenchmark: 3 modes + PSCW + multi-node (16/32/64 ranks)
- ✅ Verified correctness on Barbora login node
- ✅ Dashboard: dataset switcher, cache-fit panel, optimization comparison, reference plots
- ✅ Pushed dashboard to GitHub Pages

## Report narrative (draft)

Core story arc for `xhumld00.pdf`:

1. **Decomposition comparison (1D vs 2D)** — theoretical O(√P·N) vs O(P·N), empirical 56/90 configs 2D wins
2. **RMA fix attribution** — microbenchmark: Get→Put dominant (72×), packing secondary (2-3×), PSCW scale-dependent (1.15× → 1.74× at 2 nodes)
3. **HDF5 chunk fix** — 4220× par_IO speedup, one big chunk vs tile-sized chunks
4. **Super-linear speedup** — cache effect, per-rank WS fits in L3 while seq was RAM-bound
5. **Computation-communication overlap** — `computeHaloZones` + inner compute + halo exchange pipelining
6. **Load balance** — Score-P evidence

## Open questions

- Is Score-P job "small enough" to fit interactive window or do we sbatch?
- Does weak scaling need real data or is CSV-extract enough? → probably CSV-extract with honest note
- Multi-node PSCW production validation before report?

## Commands for next session

```sh
# Rebuild Score-P build on Barbora:
ssh barbora
cd ~/ppp-project
export SCOREP_WRAPPER_INSTRUMENTER_FLAGS=--thread=omp
rm -rf build_prof
SCOREP_WRAPPER=off cmake \
    -DCMAKE_CXX_COMPILER=scorep-mpiicpc \
    -DLOGIN=xhumld00 \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -Bbuild_prof -S.
cmake --build build_prof -j

# Submit Score-P:
cd scripts
sbatch run_scorep_profile.sh
squeue -u ramsay

# Check Hybrid 2D progress:
squeue -j 1963262
```

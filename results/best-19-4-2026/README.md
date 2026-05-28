# best-19-4-2026

Snapshot of benchmark results and plots as of 2026-04-19.

## Contents

- `*.csv` — raw benchmark data (MPI 2D, Hybrid 1D, Hybrid 2D)
- `*.png` — scaling, speedup, efficiency plots per configuration
- `dashboard.html` — interactive dashboard (open in browser)
- `display_plots.html` — static plot viewer

## Key results

- All runs from job `1959255` on Barbora (account `atr-25-7`)
- RMA packing fix applied (P2P ≈ RMA parity)
- HDF5 chunk fix applied (par_IO ≈ no_IO on large grids)
- Lustre striping: `-S 1M -c 16`
- Best speedup: ~650× on 4096² Hybrid P2P at 256 cores (super-linear, cache effect)

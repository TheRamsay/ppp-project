#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas>=2.0"]
# ///
"""Build a self-contained interactive performance dashboard from benchmark CSVs.

Reads the three run_full_*.csv files in this directory, computes derived
metrics (speedup, efficiency, throughput), and writes dashboard.html with the
data embedded as JSON. Open dashboard.html directly in a browser.

Usage:  uv run scripts/build_dashboard.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPTS_DIR.parent / "results"

DATASETS_META = {
    "post_pscw": {
        "label": "PSCW (Put packed + PSCW sync, Apr 21)",
        "dir": RESULTS_DIR / "post-pscw-2026-04-20",
    },
    "pre_pscw": {
        "label": "Fence (Put packed + fence, Apr 19)",
        "dir": RESULTS_DIR / "best-19-4-2026",
    },
    "old_rma": {
        "label": "Baseline (Get strided + fence)",
        "dir": RESULTS_DIR / "old_rma_strided",
    },
}
SIZES = [256, 512, 1024, 2048, 4096]

# Matches the run_full_*.sh scripts: for each (procs, size), 6 srun calls
# in fixed order: p2p/no_io, p2p/seq_io, p2p/par_io, rma/no_io, rma/seq_io, rma/par_io.
VARIANTS = [
    ("p2p", "none"),
    ("p2p", "seq"),
    ("p2p", "par"),
    ("rma", "none"),
    ("rma", "seq"),
    ("rma", "par"),
]

CONFIGS = {
    "mpi_2d":    {"label": "MPI 2D",    "csv": "run_full_mpi_2d_out.csv",    "cores": [1, 16, 32, 64, 128]},
    "hybrid_1d": {"label": "Hybrid 1D", "csv": "run_full_hybrid_1d_out.csv", "cores": [1, 32, 64, 128, 256]},
    "hybrid_2d": {"label": "Hybrid 2D", "csv": "run_full_hybrid_2d_out.csv", "cores": [1, 32, 128, 256]},
}


def parse_csv(impl: str, spec: dict, src_dir: Path | None = None) -> list[dict]:
    df = pd.read_csv((src_dir or SCRIPTS_DIR) / spec["csv"], sep=";")
    cores = spec["cores"]
    expected = len(cores) * len(SIZES) * len(VARIANTS)
    assert len(df) == expected, f"{impl}: expected {expected} rows, got {len(df)}"

    runs = []
    for i, row in df.iterrows():
        procs_idx = i // (len(SIZES) * len(VARIANTS))
        var_idx = i % len(VARIANTS)
        comm, io = VARIANTS[var_idx]

        runs.append({
            "impl": impl,
            "impl_label": spec["label"],
            "cores": cores[procs_idx],
            "mpi": int(row["mpi_procs"]),
            "omp": int(row["omp_threads"]),
            "decomp_x": int(row["grid_tiles_x"]),
            "decomp_y": int(row["grid_tiles_y"]),
            "grid": int(row["domain_size"]),
            "iters": int(row["n_iterations"]),
            "comm": comm,
            "io": io,
            "total_time": float(row["total_time"]),
            "iter_time_ms": float(row["iteration_time"]) * 1000.0,
        })
    return runs


def compute_derived(runs: list[dict]) -> list[dict]:
    """Speedup/efficiency relative to matching single-core run; throughput in Mcells/s."""
    baseline: dict[tuple, float] = {}
    for r in runs:
        if r["cores"] == 1:
            baseline[(r["impl"], r["grid"], r["comm"], r["io"])] = r["iter_time_ms"]

    for r in runs:
        base = baseline.get((r["impl"], r["grid"], r["comm"], r["io"]))
        if base is not None and r["iter_time_ms"] > 0:
            r["speedup"] = base / r["iter_time_ms"]
            r["efficiency"] = r["speedup"] / r["cores"] * 100.0
        else:
            r["speedup"] = None
            r["efficiency"] = None

        sec = r["iter_time_ms"] / 1000.0
        r["throughput_mcells"] = (r["grid"] ** 2) / sec / 1e6 if sec > 0 else 0.0

    return runs


def summary(runs: list[dict]) -> dict:
    parallel = [r for r in runs if r["cores"] > 1 and r["speedup"] is not None]

    best_spd = max(parallel, key=lambda r: r["speedup"])
    best_eff = max(parallel, key=lambda r: r["efficiency"])
    best_thr = max(runs, key=lambda r: r["throughput_mcells"])

    superlinear = [r for r in parallel if r["efficiency"] > 100]

    # P2P vs RMA parity: mean ratio of RMA/P2P at matched (impl,grid,cores,io)
    pairs = defaultdict(dict)
    for r in parallel:
        pairs[(r["impl"], r["grid"], r["cores"], r["io"])][r["comm"]] = r["iter_time_ms"]
    ratios = [d["rma"] / d["p2p"] for d in pairs.values() if "rma" in d and "p2p" in d]
    rma_ratio = sum(ratios) / len(ratios) if ratios else 1.0

    # Hybrid 2D vs Hybrid 1D gap at matching (cores, grid, comm, io)
    h1 = {(r["cores"], r["grid"], r["comm"], r["io"]): r["iter_time_ms"]
          for r in runs if r["impl"] == "hybrid_1d" and r["cores"] > 1}
    h2 = {(r["cores"], r["grid"], r["comm"], r["io"]): r["iter_time_ms"]
          for r in runs if r["impl"] == "hybrid_2d" and r["cores"] > 1}
    matched = [(h2[k] / h1[k]) for k in h2 if k in h1]
    hybrid_gap = sum(matched) / len(matched) if matched else 1.0

    total_cpu_hours = sum(r["total_time"] * r["cores"] for r in runs) / 3600.0
    total_wall_hours = sum(r["total_time"] for r in runs) / 3600.0

    return {
        "total_runs": len(runs),
        "total_cpu_hours": total_cpu_hours,
        "total_wall_hours": total_wall_hours,
        "superlinear_count": len(superlinear),
        "rma_vs_p2p_mean": rma_ratio,
        "hybrid_2d_vs_1d_mean": hybrid_gap,
        "best_speedup": {
            "value": best_spd["speedup"],
            "config": f"{best_spd['impl_label']} · {best_spd['comm'].upper()} · {best_spd['cores']} cores · {best_spd['grid']}²",
        },
        "best_efficiency": {
            "value": best_eff["efficiency"],
            "config": f"{best_eff['impl_label']} · {best_eff['comm'].upper()} · {best_eff['cores']} cores · {best_eff['grid']}²",
        },
        "best_throughput": {
            "value": best_thr["throughput_mcells"],
            "config": f"{best_thr['impl_label']} · {best_thr['comm'].upper()} · {best_thr['cores']} cores · {best_thr['grid']}²",
        },
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PPP Heat Solver — Performance Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  /* ===== THEMES ===== */
  :root, html[data-theme="nebula"] {
    --bg: #08080c;
    --bg-2: #0f0f18;
    --panel: rgba(255,255,255,0.025);
    --panel-hi: rgba(255,255,255,0.045);
    --border: rgba(255,255,255,0.07);
    --border-hi: rgba(255,255,255,0.14);
    --text: #e7e7ea;
    --text-2: #a1a1aa;
    --text-3: #71717a;
    --accent: #60a5fa;
    --accent-2: #a78bfa;
    --accent-3: #34d399;
    --success: #34d399;
    --warn: #fbbf24;
    --danger: #f87171;
    --chip-bg: rgba(96,165,250,0.16);
    --chip-border: rgba(96,165,250,0.4);
    --chip-fg: #bfdbfe;
    --tab-bg: rgba(96,165,250,0.12);
    --tab-fg: #bfdbfe;
    --glow-1: rgba(96,165,250,0.08);
    --glow-2: rgba(167,139,250,0.07);
    --glow-3: rgba(52,211,153,0.04);
  }
  html[data-theme="inferno"] {
    --bg: #0a0405;
    --bg-2: #170709;
    --panel: rgba(255,120,60,0.03);
    --panel-hi: rgba(255,120,60,0.06);
    --border: rgba(255,150,100,0.08);
    --border-hi: rgba(255,150,100,0.18);
    --text: #f5ede4;
    --text-2: #d6a89a;
    --text-3: #8f6456;
    --accent: #ff6b35;
    --accent-2: #ffd700;
    --accent-3: #f7d13d;
    --success: #f7d13d;
    --warn: #ed6925;
    --danger: #cf4446;
    --chip-bg: rgba(255,107,53,0.18);
    --chip-border: rgba(255,107,53,0.5);
    --chip-fg: #ffcd9e;
    --tab-bg: rgba(255,107,53,0.15);
    --tab-fg: #ffcd9e;
    --glow-1: rgba(237,105,37,0.12);
    --glow-2: rgba(255,215,0,0.08);
    --glow-3: rgba(207,68,70,0.06);
  }
  html[data-theme="synthwave"] {
    --bg: #0d0221;
    --bg-2: #190736;
    --panel: rgba(255,60,200,0.03);
    --panel-hi: rgba(255,60,200,0.06);
    --border: rgba(255,100,220,0.08);
    --border-hi: rgba(255,100,220,0.2);
    --text: #f4d4ff;
    --text-2: #c9a0ee;
    --text-3: #8470a8;
    --accent: #ff71ce;
    --accent-2: #01cdfe;
    --accent-3: #05ffa1;
    --success: #05ffa1;
    --warn: #fffb96;
    --danger: #ff71ce;
    --chip-bg: rgba(1,205,254,0.15);
    --chip-border: rgba(1,205,254,0.45);
    --chip-fg: #a3eaff;
    --tab-bg: rgba(1,205,254,0.12);
    --tab-fg: #a3eaff;
    --glow-1: rgba(255,113,206,0.1);
    --glow-2: rgba(1,205,254,0.08);
    --glow-3: rgba(185,103,255,0.06);
  }

  html, body { background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, -apple-system, sans-serif; }
  body {
    background:
      radial-gradient(1200px 600px at 0% 0%, var(--glow-1), transparent 50%),
      radial-gradient(1000px 500px at 100% 0%, var(--glow-2), transparent 50%),
      radial-gradient(800px 400px at 50% 100%, var(--glow-3), transparent 50%),
      var(--bg);
    min-height: 100vh;
    transition: background-color 0.3s ease;
  }
  /* Synthwave gets a scanline overlay for 80s feel */
  html[data-theme="synthwave"] body::before {
    content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 1;
    background: repeating-linear-gradient(0deg, transparent 0, transparent 2px, rgba(255,255,255,0.012) 2px, rgba(255,255,255,0.012) 3px);
  }
  /* Inferno gets a subtle ember flicker via gradient noise */
  html[data-theme="inferno"] body {
    background:
      radial-gradient(1400px 700px at 0% 0%, var(--glow-1), transparent 55%),
      radial-gradient(1000px 500px at 100% 20%, var(--glow-2), transparent 50%),
      radial-gradient(900px 500px at 50% 110%, var(--glow-3), transparent 50%),
      var(--bg);
  }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
  .glass { background: var(--panel); backdrop-filter: blur(12px) saturate(150%); border: 1px solid var(--border); }
  .glass:hover { background: var(--panel-hi); border-color: var(--border-hi); }
  .card { transition: all 0.2s ease; }
  .card:hover { transform: translateY(-1px); }
  .chip { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 500; letter-spacing: 0.02em; }
  .chip-active { background: var(--chip-bg); border: 1px solid var(--chip-border); color: var(--chip-fg); }
  .chip-inactive { background: rgba(255,255,255,0.03); border: 1px solid var(--border); color: var(--text-3); cursor: pointer; }
  .chip-inactive:hover { color: var(--text-2); border-color: var(--border-hi); }
  .section-title { font-weight: 700; letter-spacing: -0.01em; font-size: 1.125rem; }
  .section-sub { color: var(--text-3); font-size: 0.875rem; margin-top: 2px; }
  .stat-num { font-variant-numeric: tabular-nums; font-weight: 700; letter-spacing: -0.03em; }
  .chart { width: 100%; min-height: 420px; }
  .chart-tall { min-height: 540px; }
  .chart-wrap { position: relative; }
  .chart-expand-btn {
    position: absolute; top: 6px; right: 6px; z-index: 10;
    opacity: 0; transition: opacity 0.15s;
    background: rgba(255,255,255,0.07); border: 1px solid var(--border-hi);
    color: var(--text-2); border-radius: 6px; padding: 3px 7px;
    font-size: 14px; cursor: pointer; line-height: 1;
  }
  .chart-wrap:hover .chart-expand-btn { opacity: 1; }
  .chart-expand-btn:hover { background: rgba(255,255,255,0.14); color: var(--text); }
  #chart-fullscreen-modal {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(0,0,0,0.92); backdrop-filter: blur(8px);
    display: none; flex-direction: column;
  }
  #chart-fullscreen-modal.open { display: flex; }
  #chart-fullscreen-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  #chart-fullscreen-close {
    background: rgba(255,255,255,0.07); border: 1px solid var(--border-hi);
    color: var(--text-2); border-radius: 8px; padding: 5px 12px;
    font-size: 15px; cursor: pointer; transition: all 0.15s;
  }
  #chart-fullscreen-close:hover { background: rgba(255,255,255,0.14); color: var(--text); }
  #chart-fullscreen-inner { flex: 1; width: 100%; min-height: 0; }
  .tab-btn { padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer; transition: all 0.15s; }
  .tab-btn:hover { color: var(--text-2); background: rgba(255,255,255,0.04); }
  .tab-active { color: var(--tab-fg); background: var(--tab-bg); }
  table { font-variant-numeric: tabular-nums; border-collapse: collapse; width: 100%; font-size: 13px; }
  thead th { position: sticky; top: 0; background: var(--bg-2); padding: 8px 12px; text-align: left; font-weight: 600; color: var(--text-2); border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; white-space: nowrap; }
  thead th:hover { color: var(--text); }
  tbody td { padding: 6px 12px; border-bottom: 1px solid rgba(255,255,255,0.03); white-space: nowrap; }
  tbody tr:hover { background: rgba(255,255,255,0.025); }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 10px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
  .pill-p2p { background: rgba(96,165,250,0.15); color: #93c5fd; }
  .pill-rma { background: rgba(167,139,250,0.15); color: #c4b5fd; }
  /* Theme switcher */
  .theme-dot { width: 22px; height: 22px; border-radius: 50%; cursor: pointer; border: 2px solid transparent; transition: all 0.2s; position: relative; }
  .theme-dot:hover { transform: scale(1.15); }
  .theme-dot.active { border-color: rgba(255,255,255,0.8); box-shadow: 0 0 0 2px rgba(255,255,255,0.15), 0 0 20px currentColor; }
  .theme-dot-nebula { background: linear-gradient(135deg, #60a5fa, #a78bfa 55%, #34d399); color: #60a5fa; }
  .theme-dot-inferno { background: linear-gradient(135deg, #ffd700 0%, #ed6925 50%, #cf4446 100%); color: #ed6925; }
  .theme-dot-synthwave { background: linear-gradient(135deg, #ff71ce, #01cdfe 60%, #b967ff); color: #ff71ce; }
  .gradient-text { background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 50%, var(--accent-3) 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
  .pill-none { background: rgba(113,113,122,0.2); color: #a1a1aa; }
  .pill-seq { background: rgba(251,191,36,0.15); color: #fcd34d; }
  .pill-par { background: rgba(52,211,153,0.15); color: #6ee7b7; }
  .pill-mpi_2d { background: rgba(96,165,250,0.15); color: #93c5fd; }
  .pill-hybrid_1d { background: rgba(52,211,153,0.15); color: #6ee7b7; }
  .pill-hybrid_2d { background: rgba(167,139,250,0.15); color: #c4b5fd; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 5px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.15); }
</style>
</head>
<body x-data="dashboard()" x-init="init()">

<!-- Header -->
<header class="border-b border-white/5 sticky top-0 z-20 backdrop-blur-xl bg-black/40">
  <div class="max-w-[1800px] mx-auto px-6 py-4 flex items-center justify-between flex-wrap gap-3">
    <div>
      <h1 class="text-2xl font-extrabold tracking-tight">
        <span class="gradient-text">PPP Heat Solver</span>
        <span class="text-white/80">— Performance Dashboard</span>
      </h1>
      <div class="text-sm text-zinc-400 mt-0.5">
        Parallel MPI/OpenMP heat equation solver · benchmarked on Barbora (IT4Innovations)
      </div>
    </div>
    <div class="flex items-center gap-4">
      <div class="flex items-center gap-2">
        <div class="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold mr-1">theme</div>
        <template x-for="t in themeOptions" :key="t.key">
          <button :class="['theme-dot', 'theme-dot-' + t.key, theme === t.key ? 'active' : '']"
                  :title="t.label"
                  @click="setTheme(t.key)"></button>
        </template>
      </div>
      <div class="text-right text-xs text-zinc-500 mono">
        <div>login · <span class="text-zinc-300">xhumld00</span></div>
        <div x-text="'generated · ' + _PAYLOAD.generated_at"></div>
      </div>
    </div>
  </div>
</header>

<main class="max-w-[1800px] mx-auto px-6 py-8 space-y-10">

  <!-- STATS GRID -->
  <section>
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <template x-for="s in statCards" :key="s.label">
        <div class="glass rounded-xl p-4 card">
          <div class="text-xs uppercase tracking-wider text-zinc-500 font-semibold" x-text="s.label"></div>
          <div class="mt-2 stat-num text-2xl" :style="`color:${s.color}`" x-html="s.value"></div>
          <div class="text-[11px] text-zinc-500 mt-1 leading-tight" x-text="s.hint"></div>
        </div>
      </template>
    </div>
  </section>

  <!-- FILTERS -->
  <section class="glass rounded-xl p-5 space-y-4">
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <div class="section-title">Filters</div>
        <div class="section-sub">All charts below react to these selections</div>
      </div>
      <button @click="resetFilters()" class="text-xs text-zinc-400 hover:text-white px-3 py-1.5 rounded-md border border-white/10 hover:border-white/25 transition">
        Reset
      </button>
    </div>

    <!-- Dataset switcher -->
    <div class="flex items-center gap-3 pb-1 border-b border-white/5">
      <div class="text-xs text-zinc-500 uppercase tracking-wider font-semibold shrink-0">Dataset</div>
      <div class="flex flex-wrap gap-2">
        <template x-for="d in datasetOptions" :key="d.key">
          <button
            class="text-xs px-3 py-1.5 rounded-lg border transition font-medium"
            :class="activeDataset === d.key
              ? 'bg-white/10 border-white/30 text-white'
              : 'bg-transparent border-white/10 text-zinc-400 hover:text-zinc-200 hover:border-white/20'"
            @click="switchDataset(d.key)"
            x-text="d.label">
          </button>
        </template>
      </div>
    </div>

    <div class="grid md:grid-cols-4 gap-4">
      <div>
        <div class="text-xs text-zinc-500 mb-2 uppercase tracking-wider font-semibold">Grid size</div>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="g in gridOptions" :key="g">
            <span class="chip" :class="filters.grids.includes(g) ? 'chip-active' : 'chip-inactive'"
                  @click="toggle(filters.grids, g)" x-text="g + '²'"></span>
          </template>
        </div>
      </div>
      <div>
        <div class="text-xs text-zinc-500 mb-2 uppercase tracking-wider font-semibold">Implementation</div>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="i in implOptions" :key="i.key">
            <span class="chip" :class="filters.impls.includes(i.key) ? 'chip-active' : 'chip-inactive'"
                  @click="toggle(filters.impls, i.key)" x-text="i.label"></span>
          </template>
        </div>
      </div>
      <div>
        <div class="text-xs text-zinc-500 mb-2 uppercase tracking-wider font-semibold">Communication</div>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="c in commOptions" :key="c">
            <span class="chip" :class="filters.comms.includes(c) ? 'chip-active' : 'chip-inactive'"
                  @click="toggle(filters.comms, c)" x-text="c.toUpperCase()"></span>
          </template>
        </div>
      </div>
      <div>
        <div class="text-xs text-zinc-500 mb-2 uppercase tracking-wider font-semibold">I/O mode</div>
        <div class="flex flex-wrap gap-1.5">
          <template x-for="o in ioOptions" :key="o">
            <span class="chip" :class="filters.ios.includes(o) ? 'chip-active' : 'chip-inactive'"
                  @click="toggle(filters.ios, o)" x-text="ioLabel(o)"></span>
          </template>
        </div>
      </div>
    </div>
    <div class="text-xs text-zinc-500 pt-2 border-t border-white/5">
      <span x-text="filtered.length"></span> of <span x-text="DATA.runs.length"></span> runs in dataset match
    </div>
  </section>

  <!-- SCALING / SPEEDUP / EFFICIENCY tabs -->
  <section class="glass rounded-xl p-5">
    <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
      <div>
        <div class="section-title">Scaling Analysis</div>
        <div class="section-sub">Per-run metrics against core count · filter-driven</div>
      </div>
      <div class="flex gap-1 bg-white/5 p-1 rounded-lg">
        <template x-for="t in scalingTabs" :key="t.key">
          <span class="tab-btn" :class="scalingTab === t.key ? 'tab-active' : ''"
                @click="scalingTab = t.key; renderScaling()" x-text="t.label"></span>
        </template>
      </div>
    </div>
    <div id="chart-scaling" class="chart chart-tall"></div>
    <div class="text-xs text-zinc-500 mt-3" x-html="scalingHint()"></div>
  </section>

  <!-- 2x2 analysis grid -->
  <section class="grid lg:grid-cols-2 gap-5">
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">Throughput (Mcells/s)</div>
        <div class="section-sub">Cell-updates per second — hardware utilization</div>
      </div>
      <div id="chart-throughput" class="chart"></div>
    </div>
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">P2P vs RMA parity</div>
        <div class="section-sub">Matched pairs · points on the diagonal = identical</div>
      </div>
      <div id="chart-parity" class="chart"></div>
    </div>
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">I/O overhead</div>
        <div class="section-sub">Iteration time breakdown: no-IO · seq IO · parallel IO</div>
      </div>
      <div id="chart-io" class="chart"></div>
    </div>
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">Implementation comparison</div>
        <div class="section-sub">Same cores, same grid — which decomposition wins?</div>
      </div>
      <div id="chart-impl" class="chart"></div>
    </div>
  </section>

  <!-- AMDAHL -->
  <section class="glass rounded-xl p-5">
    <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
      <div>
        <div class="section-title">Amdahl fit — serial fraction <span class="mono text-zinc-500 text-sm font-normal">ƒ</span></div>
        <div class="section-sub">
          Fits <span class="mono">S(N) = 1 / (ƒ + (1−ƒ)/N)</span> per series. Lower ƒ = better scaling ceiling (1/ƒ). Super-linear runs yield ƒ &lt; 0 — interpreted as cache-win, not modelable.
        </div>
      </div>
    </div>
    <div class="grid lg:grid-cols-[1.7fr_1fr] gap-5">
      <div>
        <div id="chart-amdahl" class="chart chart-tall"></div>
      </div>
      <div class="glass rounded-lg p-4 max-h-[540px] overflow-y-auto">
        <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">Fitted ƒ per series</div>
        <div id="amdahl-table" class="space-y-0 text-xs"></div>
      </div>
    </div>
  </section>

  <!-- PARETO FRONTIER -->
  <section class="glass rounded-xl p-5">
    <div class="mb-4">
      <div class="section-title">Pareto frontier — time vs cost</div>
      <div class="section-sub">
        Each dot is a run · X = wall time (s) · Y = CPU-cost (cores × seconds).
        <span class="text-emerald-300">Gold stars</span> mark Pareto-optimal configs — no other run is both cheaper AND faster.
        The frontier is your efficient menu.
      </div>
    </div>
    <div id="chart-pareto" class="chart chart-tall"></div>
  </section>

  <!-- OPTIMIZATION COMPARISON (BEFORE vs AFTER) -->
  <section class="glass rounded-xl p-5">
    <div class="mb-4">
      <div class="section-title">Optimization wins — baseline vs optimized</div>
      <div class="section-sub">
        Matched configs across both snapshots. Points below the diagonal = faster now.
        <span class="text-zinc-400">HDF5 chunk fix:</span> <span class="mono">H5Pset_chunk(gridSize)</span> — one whole-grid chunk instead of tile-sized chunks.
      </div>
    </div>

    <!-- Summary chips -->
    <div id="comparison-chips" class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5"></div>

    <!-- Chunk illustration + scatter -->
    <div class="grid lg:grid-cols-[320px_1fr] gap-5">
      <div class="glass rounded-lg p-4 flex flex-col">
        <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">HDF5 chunk layout</div>
        <div id="chunk-illustration" class="flex-1"></div>
        <div class="text-[11px] text-zinc-500 leading-snug mt-3">
          <div class="mb-1.5"><span class="inline-block w-2 h-2 rounded-sm align-middle mr-1" style="background:#f87171"></span><strong class="text-zinc-300">Before</strong>: N×N tile-sized chunks → N metadata ops per collective write, many tiny I/O requests.</div>
          <div><span class="inline-block w-2 h-2 rounded-sm align-middle mr-1" style="background:#34d399"></span><strong class="text-zinc-300">After</strong>: one whole-grid chunk → single collective write, MPI-IO aggregates perfectly into Lustre stripes.</div>
        </div>
      </div>
      <div id="chart-comparison" class="chart chart-tall"></div>
    </div>
  </section>

  <!-- COMM / COMPUTE OVERHEAD -->
  <section class="glass rounded-xl p-5">
    <div class="mb-4">
      <div class="section-title">Parallel overhead — where does the time actually go?</div>
      <div class="section-sub">
        For each run, the ideal time is <span class="mono text-zinc-400">T₁ / N</span>. Anything above that is
        communication + synchronization + OMP fork/join. Dark bars = compute, colored bars = overhead.
        Overhead growing past 50% means you're about to stop scaling.
      </div>
    </div>
    <div class="grid md:grid-cols-3 gap-4">
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">MPI 2D</div>
        <div id="chart-overhead-mpi_2d" class="chart" style="min-height:340px"></div>
      </div>
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">Hybrid 1D</div>
        <div id="chart-overhead-hybrid_1d" class="chart" style="min-height:340px"></div>
      </div>
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">Hybrid 2D</div>
        <div id="chart-overhead-hybrid_2d" class="chart" style="min-height:340px"></div>
      </div>
    </div>
    <div id="chart-overhead-fraction" class="chart mt-4" style="min-height:300px"></div>
  </section>

  <!-- DECOMPOSITION VISUALIZER -->
  <section class="glass rounded-xl p-5">
    <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
      <div>
        <div class="section-title">Decomposition visualizer</div>
        <div class="section-sub">
          How is the grid carved up? Pick a run — see every MPI tile, every OMP thread slice, every halo.
        </div>
      </div>
      <div class="flex gap-2 items-center">
        <span class="text-xs text-zinc-500">Run:</span>
        <select x-model.number="decompRunId" @change="renderDecomp()"
                class="bg-zinc-900 border border-white/10 rounded-md px-3 py-1.5 text-xs mono text-zinc-200 hover:border-white/25 focus:outline-none focus:border-blue-400 max-w-sm">
          <template x-for="r in decompOptions" :key="r._id">
            <option :value="r._id" x-text="decompLabel(r)"></option>
          </template>
        </select>
      </div>
    </div>
    <div class="grid lg:grid-cols-[1fr_340px] gap-5">
      <div class="glass rounded-lg p-4 flex items-center justify-center min-h-[560px]" style="background:radial-gradient(600px 400px at 50% 50%, rgba(96,165,250,0.04), transparent 60%)">
        <div id="decomp-svg" class="w-full"></div>
      </div>
      <div class="space-y-3">
        <div class="glass rounded-lg p-4">
          <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">Configuration</div>
          <div id="decomp-config" class="space-y-1.5 text-xs"></div>
        </div>
        <div class="glass rounded-lg p-4">
          <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">Halo geometry</div>
          <div id="decomp-halo" class="space-y-1.5 text-xs"></div>
        </div>
        <div class="glass rounded-lg p-4">
          <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">Measured performance</div>
          <div id="decomp-perf" class="space-y-1.5 text-xs"></div>
        </div>
        <div class="glass rounded-lg p-4">
          <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-3">Cache fit — why (super-)linear?</div>
          <div id="decomp-cache" class="text-xs"></div>
        </div>
      </div>
    </div>
    <div class="text-xs text-zinc-500 mt-3">
      <span class="inline-block w-2 h-2 rounded-sm align-middle mr-1" style="background:#60a5fa"></span>
      rank tile (color = rank id) ·
      <span class="mono text-zinc-400">║</span> OMP thread row partitioning ·
      <span class="inline-block w-3 h-0.5 align-middle mr-1" style="background:repeating-linear-gradient(90deg, #fbbf24 0 4px, transparent 4px 7px)"></span>
      halo zones (exaggerated for visibility — actual halo is 2 cells)
    </div>
  </section>

  <!-- NODE TOPOLOGY -->
  <section class="glass rounded-xl p-5">
    <div class="mb-4">
      <div class="section-title">Node topology — where ranks physically live</div>
      <div class="section-sub">
        Barbora: <span class="mono text-zinc-400">Intel Xeon Gold 6240 (Cascade Lake)</span> · 2 sockets × 18 cores = <strong>36 cores/node</strong> · 2 NUMA domains · 24.75 MB L3/socket · 190 GB RAM.
        Your scripts use <span class="mono text-zinc-400">--ntasks-per-node=32</span> (hybrid: <span class="mono text-zinc-400">2/4 tasks × 16/8 OMP</span>) — <strong>4 cores per node stay idle</strong>, shown as dashed slots below.
      </div>
    </div>
    <div id="topology-svg" class="w-full overflow-x-auto"></div>
    <div class="text-xs text-zinc-500 mt-3 flex flex-wrap gap-x-5 gap-y-1">
      <span class="font-semibold text-zinc-400">Hover a rank to reveal its halo neighbors:</span>
      <span><span class="inline-block w-3 h-3 align-middle mr-1 rounded-sm" style="background:rgba(52,211,153,0.55);border:1px solid rgba(52,211,153,0.9)"></span>same socket (shared L3 cache — fastest)</span>
      <span><span class="inline-block w-3 h-3 align-middle mr-1 rounded-sm" style="background:rgba(96,165,250,0.55);border:1px solid rgba(96,165,250,0.9)"></span>same node, different socket (NUMA hop)</span>
      <span><span class="inline-block w-3 h-3 align-middle mr-1 rounded-sm" style="background:rgba(248,113,113,0.55);border:1px solid rgba(248,113,113,0.9)"></span>cross-node (InfiniBand, slowest)</span>
    </div>
  </section>

  <!-- HEATMAPS -->
  <section class="glass rounded-xl p-5">
    <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
      <div>
        <div class="section-title">Heatmaps</div>
        <div class="section-sub">Grid × Cores matrices per implementation — hotspots at a glance</div>
      </div>
      <div class="flex gap-1 bg-white/5 p-1 rounded-lg">
        <template x-for="t in heatmapTabs" :key="t.key">
          <span class="tab-btn" :class="heatmapTab === t.key ? 'tab-active' : ''"
                @click="heatmapTab = t.key; renderHeatmaps()" x-text="t.label"></span>
        </template>
      </div>
    </div>
    <div class="grid md:grid-cols-3 gap-4">
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">MPI 2D</div>
        <div id="chart-heat-mpi_2d" class="chart" style="min-height:300px"></div>
      </div>
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">Hybrid 1D</div>
        <div id="chart-heat-hybrid_1d" class="chart" style="min-height:300px"></div>
      </div>
      <div>
        <div class="text-center text-xs font-semibold text-zinc-400 mb-2">Hybrid 2D</div>
        <div id="chart-heat-hybrid_2d" class="chart" style="min-height:300px"></div>
      </div>
    </div>
  </section>

  <!-- BOTTLENECK / BEST CONFIG -->
  <section class="grid lg:grid-cols-2 gap-5">
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">Hybrid 2D vs Hybrid 1D gap</div>
        <div class="section-sub">Ratio > 1 means Hybrid 2D is slower at that config</div>
      </div>
      <div id="chart-hybridgap" class="chart"></div>
    </div>
    <div class="glass rounded-xl p-5">
      <div class="mb-3">
        <div class="section-title">Best config per grid size</div>
        <div class="section-sub">Fastest (no-IO) configuration discovered in the sweep</div>
      </div>
      <div id="best-config-wrap" class="space-y-2"></div>
    </div>
  </section>

  <!-- RAW DATA TABLE -->
  <section class="glass rounded-xl p-5">
    <div class="mb-4 flex items-center justify-between flex-wrap gap-3">
      <div>
        <div class="section-title">All runs</div>
        <div class="section-sub">Click a header to sort · respects filters above</div>
      </div>
      <div class="text-xs text-zinc-500 mono" x-text="filtered.length + ' rows'"></div>
    </div>
    <div class="overflow-x-auto max-h-[600px] overflow-y-auto rounded-lg border border-white/5">
      <table>
        <thead>
          <tr>
            <template x-for="c in columns" :key="c.key">
              <th @click="sortBy(c.key)" x-text="c.label + (sort.key === c.key ? (sort.asc ? ' ↑' : ' ↓') : '')"></th>
            </template>
          </tr>
        </thead>
        <tbody>
          <template x-for="r in sorted" :key="r._id">
            <tr>
              <td><span class="pill" :class="'pill-' + r.impl" x-text="r.impl_label"></span></td>
              <td><span class="mono" x-text="r.grid + '²'"></span></td>
              <td><span class="mono" x-text="r.cores"></span></td>
              <td><span class="mono text-zinc-400" x-text="r.mpi + '×' + r.omp"></span></td>
              <td><span class="mono text-zinc-400" x-text="r.decomp_x + '×' + r.decomp_y"></span></td>
              <td><span class="pill" :class="'pill-' + r.comm" x-text="r.comm"></span></td>
              <td><span class="pill" :class="'pill-' + r.io" x-text="ioLabel(r.io)"></span></td>
              <td><span class="mono" x-text="r.iter_time_ms.toFixed(4)"></span></td>
              <td><span class="mono" x-text="r.total_time.toFixed(2)"></span></td>
              <td><span class="mono" :style="spdColor(r.speedup)" x-text="fmt(r.speedup, 2, '×')"></span></td>
              <td><span class="mono" :style="effColor(r.efficiency)" x-text="fmt(r.efficiency, 1, '%')"></span></td>
              <td><span class="mono" x-text="r.throughput_mcells.toFixed(1)"></span></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>

  <!-- REFERENCE PLOTS (matplotlib, from generate_plots.py) -->
  <section class="glass rounded-xl p-5" x-data="{ refTab: 'mpi' }">
    <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
      <div>
        <div class="section-title">Reference plots — matplotlib output</div>
        <div class="section-sub">
          Generated by <code class="mono text-zinc-400 bg-white/5 px-1 py-0.5 rounded text-[11px]">scripts/generate_plots.py</code> · log-log scales · course-style reference. Click any image to enlarge.
        </div>
      </div>
      <div class="flex gap-1 bg-white/5 p-1 rounded-lg">
        <span class="tab-btn" :class="refTab === 'mpi' ? 'tab-active' : ''" @click="refTab = 'mpi'">MPI 2D</span>
        <span class="tab-btn" :class="refTab === 'hybrid_1D' ? 'tab-active' : ''" @click="refTab = 'hybrid_1D'">Hybrid 1D</span>
        <span class="tab-btn" :class="refTab === 'hybrid' ? 'tab-active' : ''" @click="refTab = 'hybrid'">Hybrid 2D</span>
      </div>
    </div>
    <template x-for="metric in ['scaling', 'speedup', 'efficiency']" :key="metric">
      <div class="mb-5 last:mb-0">
        <div class="text-xs uppercase tracking-wider font-semibold text-zinc-500 mb-2 mono"
             x-text="metric"></div>
        <div class="grid md:grid-cols-2 gap-3">
          <template x-for="comm in ['p2p', 'rma']" :key="comm">
            <a :href="`ppp_${metric}_${refTab}_${comm}.png`" target="_blank"
               class="block rounded-lg border border-white/5 hover:border-white/15 bg-white/[0.015] p-2 transition">
              <div class="text-[11px] text-zinc-500 mono mb-1 flex justify-between">
                <span x-text="`${metric} · ${comm.toUpperCase()}`"></span>
                <span class="text-zinc-600">↗ open</span>
              </div>
              <img :src="`ppp_${metric}_${refTab}_${comm}.png`"
                   class="w-full h-auto rounded block"
                   loading="lazy"
                   onerror="this.style.display='none';this.parentElement.querySelector('.missing').style.display='block'"
                   :alt="`${metric} ${refTab} ${comm}`">
              <div class="missing hidden text-center py-6 text-xs text-zinc-500" style="display:none">
                ⚠ Image not found — run <code class="mono">python scripts/generate_plots.py png</code>
              </div>
            </a>
          </template>
        </div>
      </div>
    </template>
    <div class="text-xs text-zinc-500 mt-2">
      These are the upstream course-provided plots. The interactive Plotly views above are richer — filters,
      hover data, dataset switcher, cache-fit overlay.
    </div>
  </section>

  <footer class="text-center text-xs text-zinc-600 py-6">
    Built with Plotly · Tailwind · Alpine · uv · rebuild via
    <code class="mono text-zinc-400 bg-white/5 px-1.5 py-0.5 rounded">uv run scripts/build_dashboard.py</code>
  </footer>
</main>

<script>
const _PAYLOAD = __DATA_PLACEHOLDER__;
const DATASETS = _PAYLOAD.datasets;
let DATA = DATASETS.post_pscw;

const IMPL_DASH = { mpi_2d: 'solid', hybrid_1d: 'dash', hybrid_2d: 'dot' };
const COMM_SYMBOL = { p2p: 'circle', rma: 'diamond' };
const IMPL_LABEL = { mpi_2d: 'MPI 2D', hybrid_1d: 'Hybrid 1D', hybrid_2d: 'Hybrid 2D' };

// ===== THEMES =====
// Each theme provides: grid color palette (per grid size), Plotly base layout tuned for that palette.
const THEMES = {
  nebula: {
    label: 'Nebula',
    grids: { 256: '#60a5fa', 512: '#34d399', 1024: '#fbbf24', 2048: '#f87171', 4096: '#a78bfa' },
    plotly: {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(255,255,255,0.015)',
      font: { family: 'Inter, sans-serif', color: '#d4d4d8', size: 12 },
      gridcolor: 'rgba(255,255,255,0.06)',
      zerolinecolor: 'rgba(255,255,255,0.12)',
      tickcolor: '#a1a1aa',
      legendBg: 'rgba(0,0,0,0.4)',
      legendBorder: 'rgba(255,255,255,0.08)',
      hoverBg: '#1f1f2e',
      hoverBorder: '#444',
      hoverFg: '#e4e4e7',
    },
  },
  inferno: {
    label: 'Inferno',
    grids: { 256: '#781c6d', 512: '#cf4446', 1024: '#ed6925', 2048: '#fb9a06', 4096: '#f7d13d' },
    plotly: {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(120,40,20,0.04)',
      font: { family: 'Inter, sans-serif', color: '#f5ede4', size: 12 },
      gridcolor: 'rgba(255,180,100,0.08)',
      zerolinecolor: 'rgba(255,180,100,0.18)',
      tickcolor: '#d6a89a',
      legendBg: 'rgba(25,5,5,0.65)',
      legendBorder: 'rgba(255,150,100,0.15)',
      hoverBg: '#1a0606',
      hoverBorder: '#cf4446',
      hoverFg: '#ffcd9e',
    },
  },
  synthwave: {
    label: 'Synthwave',
    grids: { 256: '#ff71ce', 512: '#01cdfe', 1024: '#05ffa1', 2048: '#b967ff', 4096: '#fffb96' },
    plotly: {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(120,0,180,0.035)',
      font: { family: 'Inter, sans-serif', color: '#f4d4ff', size: 12 },
      gridcolor: 'rgba(255,100,220,0.08)',
      zerolinecolor: 'rgba(1,205,254,0.3)',
      tickcolor: '#c9a0ee',
      legendBg: 'rgba(13,2,33,0.7)',
      legendBorder: 'rgba(1,205,254,0.2)',
      hoverBg: '#190736',
      hoverBorder: '#ff71ce',
      hoverFg: '#a3eaff',
    },
  },
};

// Mutable GRID_COLORS + BASE_LAYOUT — overwritten by applyTheme()
const GRID_COLORS = { ...THEMES.nebula.grids };
const BASE_LAYOUT = {};
function buildLayout(p) {
  return {
    paper_bgcolor: p.paper_bgcolor,
    plot_bgcolor: p.plot_bgcolor,
    font: p.font,
    margin: { l: 60, r: 20, t: 20, b: 50 },
    xaxis: { gridcolor: p.gridcolor, zerolinecolor: p.zerolinecolor, tickfont: { color: p.tickcolor }, titlefont: { color: p.font.color, size: 12 } },
    yaxis: { gridcolor: p.gridcolor, zerolinecolor: p.zerolinecolor, tickfont: { color: p.tickcolor }, titlefont: { color: p.font.color, size: 12 } },
    legend: { font: { color: p.font.color, size: 10 }, bgcolor: p.legendBg, bordercolor: p.legendBorder, borderwidth: 1, orientation: 'h', x: 0, y: -0.18 },
    hoverlabel: { bgcolor: p.hoverBg, bordercolor: p.hoverBorder, font: { family: 'JetBrains Mono', color: p.hoverFg, size: 12 } },
  };
}
Object.assign(BASE_LAYOUT, buildLayout(THEMES.nebula.plotly));
const BASE_CONFIG = { responsive: true, displayModeBar: false };

// Attach stable IDs to all datasets
Object.values(DATASETS).forEach(ds => ds.runs.forEach((r, i) => r._id = i));

function dashboard() {
  return {
    DATA,
    activeDataset: 'post_pscw',
    datasetOptions: Object.entries(DATASETS).map(([k, v]) => ({ key: k, label: v.label })),
    switchDataset(key) {
      this.activeDataset = key;
      DATA = DATASETS[key];
      this.DATA = DATA;
      this.renderAll();
    },
    theme: localStorage.getItem('ppp-theme') || 'nebula',
    themeOptions: [
      { key: 'nebula',    label: 'Nebula (cool blue/violet)' },
      { key: 'inferno',   label: 'Inferno (fiery dark)' },
      { key: 'synthwave', label: 'Synthwave (neon 80s)' },
    ],
    filters: {
      grids: [256, 512, 1024, 2048, 4096],
      impls: ['mpi_2d', 'hybrid_1d', 'hybrid_2d'],
      comms: ['p2p', 'rma'],
      ios:   ['none', 'seq', 'par'],
    },
    gridOptions: [256, 512, 1024, 2048, 4096],
    implOptions: [
      { key: 'mpi_2d', label: 'MPI 2D' },
      { key: 'hybrid_1d', label: 'Hybrid 1D' },
      { key: 'hybrid_2d', label: 'Hybrid 2D' },
    ],
    commOptions: ['p2p', 'rma'],
    ioOptions: ['none', 'seq', 'par'],
    scalingTabs: [
      { key: 'scaling', label: 'Iteration time (log-log)' },
      { key: 'speedup', label: 'Speedup' },
      { key: 'efficiency', label: 'Efficiency' },
    ],
    scalingTab: 'scaling',
    decompRunId: null,
    heatmapTabs: [
      { key: 'efficiency', label: 'Efficiency %' },
      { key: 'speedup', label: 'Speedup' },
      { key: 'iter_time_ms', label: 'Iter time (ms)' },
    ],
    heatmapTab: 'efficiency',
    sort: { key: 'iter_time_ms', asc: true },
    columns: [
      { key: 'impl_label',     label: 'Impl' },
      { key: 'grid',           label: 'Grid' },
      { key: 'cores',          label: 'Cores' },
      { key: 'mpi',            label: 'MPI×OMP' },
      { key: 'decomp_x',       label: 'Decomp' },
      { key: 'comm',           label: 'Comm' },
      { key: 'io',             label: 'I/O' },
      { key: 'iter_time_ms',   label: 'Iter (ms)' },
      { key: 'total_time',     label: 'Total (s)' },
      { key: 'speedup',        label: 'Speedup' },
      { key: 'efficiency',     label: 'Eff %' },
      { key: 'throughput_mcells', label: 'Mcells/s' },
    ],

    get filtered() {
      const f = this.filters;
      return this.DATA.runs.filter(r =>
        f.grids.includes(r.grid) &&
        f.impls.includes(r.impl) &&
        f.comms.includes(r.comm) &&
        f.ios.includes(r.io)
      );
    },
    get sorted() {
      const arr = this.filtered.slice();
      const { key, asc } = this.sort;
      const mul = asc ? 1 : -1;
      arr.sort((a, b) => {
        const av = a[key], bv = b[key];
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        if (typeof av === 'number') return (av - bv) * mul;
        return ('' + av).localeCompare('' + bv) * mul;
      });
      return arr.slice(0, 500);  // cap rendering
    },
    get statCards() {
      const s = this.DATA.stats;
      return [
        { label: 'Total runs',     value: s.total_runs.toLocaleString(),                  hint: `${s.total_cpu_hours.toFixed(1)} CPU-hours`,                color: '#e4e4e7' },
        { label: 'Best speedup',   value: s.best_speedup.value.toFixed(0) + '<span class="text-base text-zinc-400">×</span>', hint: s.best_speedup.config, color: '#34d399' },
        { label: 'Best efficiency',value: s.best_efficiency.value.toFixed(0) + '<span class="text-base text-zinc-400">%</span>', hint: s.best_efficiency.config, color: '#fbbf24' },
        { label: 'Peak throughput',value: s.best_throughput.value.toFixed(0) + '<span class="text-base text-zinc-400"> M/s</span>', hint: s.best_throughput.config, color: '#60a5fa' },
        { label: 'RMA / P2P',      value: s.rma_vs_p2p_mean.toFixed(3) + '<span class="text-base text-zinc-400">×</span>', hint: 'Mean ratio (1.0 = parity)', color: s.rma_vs_p2p_mean < 1.05 ? '#34d399' : '#f87171' },
        { label: 'Super-linear',   value: s.superlinear_count.toString(),                 hint: 'Runs with efficiency > 100%',                             color: '#a78bfa' },
      ];
    },

    ioLabel(io) { return io === 'none' ? 'no IO' : io === 'seq' ? 'seq IO' : 'par IO'; },
    toggle(arr, v) {
      const i = arr.indexOf(v);
      if (i === -1) arr.push(v); else arr.splice(i, 1);
      this.renderAll();
    },
    resetFilters() {
      this.filters = {
        grids: [256, 512, 1024, 2048, 4096],
        impls: ['mpi_2d', 'hybrid_1d', 'hybrid_2d'],
        comms: ['p2p', 'rma'],
        ios:   ['none', 'seq', 'par'],
      };
      this.renderAll();
    },
    sortBy(k) {
      if (this.sort.key === k) this.sort.asc = !this.sort.asc;
      else { this.sort.key = k; this.sort.asc = true; }
    },
    fmt(v, d, suf) { return v == null ? '—' : v.toFixed(d) + (suf || ''); },
    spdColor(s) {
      if (s == null) return '';
      if (s >= 100) return 'color:#34d399';
      if (s >= 30)  return 'color:#fbbf24';
      return 'color:#a1a1aa';
    },
    effColor(e) {
      if (e == null) return '';
      if (e >= 100) return 'color:#34d399';
      if (e >= 50)  return 'color:#fbbf24';
      return 'color:#f87171';
    },

    init() {
      this.applyTheme(this.theme, false);
      this.renderAll();
      window.addEventListener('resize', () => this.renderAll());
    },
    setTheme(name) {
      this.theme = name;
      localStorage.setItem('ppp-theme', name);
      this.applyTheme(name, true);
    },
    applyTheme(name, rerender) {
      const t = THEMES[name];
      if (!t) return;
      document.documentElement.setAttribute('data-theme', name);
      // Swap grid palette
      Object.keys(GRID_COLORS).forEach(k => delete GRID_COLORS[k]);
      Object.assign(GRID_COLORS, t.grids);
      // Swap Plotly layout (mutate same object so existing references stay valid)
      const fresh = buildLayout(t.plotly);
      Object.keys(BASE_LAYOUT).forEach(k => delete BASE_LAYOUT[k]);
      Object.assign(BASE_LAYOUT, fresh);
      if (rerender) this.renderAll();
    },
    renderAll() {
      this.renderScaling();
      this.renderThroughput();
      this.renderParity();
      this.renderIo();
      this.renderImpl();
      this.renderAmdahl();
      this.renderPareto();
      this.renderComparison();
      this.renderOverhead();
      this.ensureDecompSelection();
      this.renderDecomp();
      this.renderTopology();
      this.renderHeatmaps();
      this.renderHybridGap();
      this.renderBestConfig();
    },

    // ==================== PARETO FRONTIER ====================
    renderPareto() {
      const runs = this.filtered.filter(r => r.cores > 0 && r.total_time > 0);
      if (runs.length === 0) {
        Plotly.react('chart-pareto', [], { ...BASE_LAYOUT }, BASE_CONFIG);
        return;
      }
      // Pareto-optimal: no other run is both cheaper (cores*time) AND faster (total_time)
      const pts = runs.map(r => ({ ...r, cost: r.cores * r.total_time }));
      const optimal = new Set();
      for (const p of pts) {
        const dominated = pts.some(q => q !== p && q.cost <= p.cost && q.total_time <= p.total_time && (q.cost < p.cost || q.total_time < p.total_time));
        if (!dominated) optimal.add(p._id);
      }

      // Per-grid color, non-optimal smaller & dimmer, optimal bigger & gold-outlined
      const traces = [];
      const byGrid = {};
      for (const p of pts) {
        if (!byGrid[p.grid]) byGrid[p.grid] = [];
        byGrid[p.grid].push(p);
      }
      Object.keys(byGrid).sort((a,b)=>a-b).forEach(g => {
        const pts = byGrid[g];
        const regular = pts.filter(p => !optimal.has(p._id));
        const opt = pts.filter(p => optimal.has(p._id));
        if (regular.length) {
          traces.push({
            x: regular.map(p => p.total_time), y: regular.map(p => p.cost),
            mode: 'markers', type: 'scatter',
            name: `${g}²`, legendgroup: 'g'+g,
            marker: { color: GRID_COLORS[g], size: 7, opacity: 0.45, line: { color: 'rgba(0,0,0,0.3)', width: 0.5 } },
            text: regular.map(p => `${IMPL_LABEL[p.impl]} · ${p.cores}c · ${p.comm.toUpperCase()} · ${this.ioLabel(p.io)}`),
            hovertemplate: '<b>%{text}</b><br>Time: %{x:.2f}s · Cost: %{y:.1f} CPU·s<extra></extra>',
          });
        }
        if (opt.length) {
          traces.push({
            x: opt.map(p => p.total_time), y: opt.map(p => p.cost),
            mode: 'markers', type: 'scatter',
            name: `${g}² · Pareto ★`, legendgroup: 'g'+g, showlegend: false,
            marker: { symbol: 'star', color: GRID_COLORS[g], size: 14, line: { color: '#fde68a', width: 1.8 } },
            text: opt.map(p => `★ ${IMPL_LABEL[p.impl]} · ${p.cores}c · ${p.comm.toUpperCase()} · ${this.ioLabel(p.io)}`),
            hovertemplate: '<b>%{text}</b><br>Time: %{x:.2f}s · Cost: %{y:.1f} CPU·s<br><i>Pareto-optimal</i><extra></extra>',
          });
        }
      });

      // Draw Pareto curve per grid
      Object.keys(byGrid).sort((a,b)=>a-b).forEach(g => {
        const opt = byGrid[g].filter(p => optimal.has(p._id)).sort((a,b) => a.total_time - b.total_time);
        if (opt.length >= 2) {
          traces.push({
            x: opt.map(p => p.total_time), y: opt.map(p => p.cost),
            mode: 'lines', type: 'scatter', name: `${g}² frontier`, showlegend: false, legendgroup: 'g'+g,
            line: { color: GRID_COLORS[g], width: 1.2, dash: 'dot' },
            hoverinfo: 'skip', opacity: 0.5,
          });
        }
      });

      Plotly.react('chart-pareto', traces, {
        ...BASE_LAYOUT,
        xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'Wall time (s, log)' },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'Cost — cores × seconds (log)' },
      }, BASE_CONFIG);
    },

    // ==================== OPTIMIZATION COMPARISON (before/after) ====================
    renderComparison() {
      const chipsEl = document.getElementById('comparison-chips');
      const chartEl = document.getElementById('chart-comparison');
      const illEl = document.getElementById('chunk-illustration');
      if (!chipsEl || !chartEl || !illEl) return;

      // Build matched pairs: (impl, grid, cores, comm, io) present in both datasets
      const key = r => `${r.impl}|${r.grid}|${r.cores}|${r.comm}|${r.io}`;
      const oldMap = {};
      DATASETS.old_rma.runs.forEach(r => {
        const k = key(r);
        // When multiple repeats, keep the min (best) time
        if (!oldMap[k] || r.iter_time_ms < oldMap[k].iter_time_ms) oldMap[k] = r;
      });
      const newMap = {};
      DATASETS.pre_pscw.runs.forEach(r => {
        const k = key(r);
        if (!newMap[k] || r.iter_time_ms < newMap[k].iter_time_ms) newMap[k] = r;
      });

      const pairs = [];
      Object.keys(newMap).forEach(k => {
        if (k in oldMap) {
          pairs.push({
            old: oldMap[k],
            new: newMap[k],
            ratio: oldMap[k].iter_time_ms / newMap[k].iter_time_ms,
          });
        }
      });

      // Summary statistics
      const median = arr => {
        if (arr.length === 0) return 0;
        const s = arr.slice().sort((a, b) => a - b);
        const m = Math.floor(s.length / 2);
        return s.length % 2 ? s[m] : (s[m-1] + s[m]) / 2;
      };
      const statsFor = predicate => {
        const subset = pairs.filter(p => predicate(p.new)).map(p => p.ratio);
        return {
          n: subset.length,
          median: median(subset),
          max: subset.length ? Math.max(...subset) : 0,
        };
      };
      const parIo = statsFor(r => r.io === 'par');
      const noIo = statsFor(r => r.io === 'none');
      const seqIo = statsFor(r => r.io === 'seq');
      const improved = pairs.filter(p => p.ratio > 1.1).length;

      // Chips
      const chipCard = (label, value, hint, color) => `
        <div class="glass rounded-xl p-3 card">
          <div class="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">${label}</div>
          <div class="mt-1 stat-num text-xl" style="color:${color}">${value}</div>
          <div class="text-[10px] text-zinc-500 mt-0.5 leading-tight">${hint}</div>
        </div>`;
      chipsEl.innerHTML = [
        chipCard('par_IO median', parIo.median.toFixed(1) + '<span class="text-sm text-zinc-500">×</span>',
                 `best case: ${parIo.max.toFixed(0)}× · ${parIo.n} matched runs`, '#34d399'),
        chipCard('seq_IO median', seqIo.median.toFixed(2) + '<span class="text-sm text-zinc-500">×</span>',
                 `${seqIo.n} matched runs`, '#fbbf24'),
        chipCard('no_IO median', noIo.median.toFixed(2) + '<span class="text-sm text-zinc-500">×</span>',
                 `compute path — should be ~1.0× (no regression)`, '#a1a1aa'),
        chipCard('Configs improved', `${improved}<span class="text-sm text-zinc-500"> / ${pairs.length}</span>`,
                 `runs with >10% faster iter time`, '#60a5fa'),
      ].join('');

      // HDF5 chunk illustration
      const drawChunks = (nx, ny, label, barColor) => {
        const W = 140, H = 140, PAD = 4;
        const cw = (W - 2*PAD) / nx, ch = (H - 2*PAD) / ny;
        let svg = `<svg viewBox="0 0 ${W} ${H}" class="block mx-auto" xmlns="http://www.w3.org/2000/svg">`;
        svg += `<rect x="0" y="0" width="${W}" height="${H}" rx="6" fill="rgba(255,255,255,0.02)" stroke="rgba(255,255,255,0.08)"/>`;
        for (let i = 0; i < nx; i++) {
          for (let j = 0; j < ny; j++) {
            svg += `<rect x="${PAD + i*cw + 0.5}" y="${PAD + j*ch + 0.5}" width="${cw - 1}" height="${ch - 1}" fill="${barColor}22" stroke="${barColor}aa" stroke-width="0.7"/>`;
          }
        }
        svg += `</svg>`;
        return svg;
      };
      illEl.innerHTML = `
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="text-[10px] font-semibold text-center text-zinc-400 mb-2">BEFORE · ${16} chunks</div>
            ${drawChunks(4, 4, 'before', '#f87171')}
          </div>
          <div>
            <div class="text-[10px] font-semibold text-center text-zinc-400 mb-2">AFTER · 1 chunk</div>
            ${drawChunks(1, 1, 'after', '#34d399')}
          </div>
        </div>`;

      // Scatter plot: old vs new iter_time (log-log), color = io, symbol = comm
      if (pairs.length === 0) {
        Plotly.react('chart-comparison', [], { ...BASE_LAYOUT, annotations: [{ text: 'No matched pairs', showarrow: false, font: { color: '#71717a' } }] }, BASE_CONFIG);
        return;
      }

      const IO_COLOR = { none: '#a1a1aa', seq: '#fbbf24', par: '#34d399' };
      const IO_LABEL = { none: 'no IO', seq: 'seq IO', par: 'par IO' };
      const COMM_SYMBOL = { p2p: 'circle', rma: 'diamond' };

      const groups = {};
      pairs.forEach(p => {
        const g = `${p.new.io}|${p.new.comm}`;
        (groups[g] = groups[g] || []).push(p);
      });

      const xs = pairs.map(p => p.old.iter_time_ms);
      const ys = pairs.map(p => p.new.iter_time_ms);
      const minVal = Math.min(...xs, ...ys) * 0.7;
      const maxVal = Math.max(...xs, ...ys) * 1.4;

      const traces = Object.keys(groups).map(g => {
        const [io, comm] = g.split('|');
        const pts = groups[g];
        return {
          x: pts.map(p => p.old.iter_time_ms),
          y: pts.map(p => p.new.iter_time_ms),
          mode: 'markers',
          type: 'scatter',
          name: `${IO_LABEL[io]} · ${comm.toUpperCase()}`,
          marker: {
            size: 10,
            color: IO_COLOR[io],
            symbol: COMM_SYMBOL[comm],
            line: { width: 1, color: 'rgba(255,255,255,0.4)' },
            opacity: 0.85,
          },
          text: pts.map(p => `${IMPL_LABEL[p.new.impl]} · ${p.new.grid}² · ${p.new.cores}c<br>ratio: <b>${p.ratio.toFixed(2)}×</b>`),
          hovertemplate: '%{text}<br>old: %{x:.3f} ms<br>new: %{y:.3f} ms<extra></extra>',
        };
      });

      // Reference lines: diagonal (parity), 10×, 100× faster
      const refLine = (factor, label, color, dash) => ({
        x: [minVal, maxVal],
        y: [minVal / factor, maxVal / factor],
        mode: 'lines',
        type: 'scatter',
        name: label,
        line: { color, width: 1.2, dash },
        hoverinfo: 'skip',
        showlegend: true,
      });
      traces.push(refLine(1, 'parity (1×)', 'rgba(255,255,255,0.35)', 'solid'));
      traces.push(refLine(10, '10× faster', 'rgba(52,211,153,0.4)', 'dash'));
      traces.push(refLine(100, '100× faster', 'rgba(52,211,153,0.25)', 'dot'));

      Plotly.react('chart-comparison', traces, {
        ...BASE_LAYOUT,
        xaxis: {
          ...BASE_LAYOUT.xaxis,
          type: 'log',
          title: 'Baseline iteration time (ms, log)',
          range: [Math.log10(minVal), Math.log10(maxVal)],
        },
        yaxis: {
          ...BASE_LAYOUT.yaxis,
          type: 'log',
          title: 'Optimized iteration time (ms, log)',
          range: [Math.log10(minVal), Math.log10(maxVal)],
          scaleanchor: 'x',
        },
        annotations: [{
          x: Math.log10(maxVal * 0.7), y: Math.log10(maxVal * 0.7),
          text: 'parity line', showarrow: false,
          font: { size: 10, color: 'rgba(255,255,255,0.4)' },
          textangle: -45, xref: 'x', yref: 'y',
        }],
      }, BASE_CONFIG);
    },

    // ==================== COMM / COMPUTE OVERHEAD ====================
    // Decompose iter_time_ms into ideal-compute (T₁/N) + overhead.
    overheadDecomp(r, baselineMap) {
      const base = baselineMap[`${r.impl}|${r.grid}|${r.comm}|${r.io}`];
      if (!base || r.cores <= 1) return null;
      const ideal = base / r.cores;  // T₁ / N
      const overhead = Math.max(0, r.iter_time_ms - ideal);
      return { ideal, overhead, frac: overhead / r.iter_time_ms };
    },
    renderOverhead() {
      const baseMap = {};
      for (const r of DATA.runs) {
        if (r.cores === 1) baseMap[`${r.impl}|${r.grid}|${r.comm}|${r.io}`] = r.iter_time_ms;
      }
      ['mpi_2d', 'hybrid_1d', 'hybrid_2d'].forEach(impl => this.renderOverheadPanel(impl, baseMap));
      this.renderOverheadFraction(baseMap);
    },
    renderOverheadPanel(impl, baseMap) {
      const runs = this.filtered.filter(r => r.impl === impl && r.cores > 1)
                                .sort((a,b) => a.cores - b.cores || a.grid - b.grid);
      if (runs.length === 0) {
        Plotly.react(`chart-overhead-${impl}`, [], { ...BASE_LAYOUT }, BASE_CONFIG);
        return;
      }
      const labels = runs.map(r => `${r.grid}²·${r.cores}c·${r.comm.toUpperCase().slice(0,3)}${r.io !== 'none' ? '·'+r.io[0] : ''}`);
      const compute = [], overhead = [], customdata = [];
      for (const r of runs) {
        const d = this.overheadDecomp(r, baseMap);
        if (!d) { compute.push(null); overhead.push(null); customdata.push([0, 0, 0]); continue; }
        compute.push(d.ideal);
        overhead.push(d.overhead);
        customdata.push([d.frac * 100, d.ideal, d.overhead]);
      }
      const traces = [
        {
          x: labels, y: compute, type: 'bar', name: 'Ideal compute (T₁/N)',
          marker: { color: 'rgba(96,165,250,0.75)' },
          customdata,
          hovertemplate: '<b>%{x}</b><br>Compute: %{y:.4f} ms<extra></extra>',
        },
        {
          x: labels, y: overhead, type: 'bar', name: 'Parallel overhead',
          marker: { color: 'rgba(248,113,113,0.85)' },
          customdata,
          hovertemplate: '<b>%{x}</b><br>Overhead: %{y:.4f} ms (%{customdata[0]:.0f}%)<extra></extra>',
        },
      ];
      Plotly.react(`chart-overhead-${impl}`, traces, {
        ...BASE_LAYOUT,
        barmode: 'stack',
        showlegend: false,
        xaxis: { ...BASE_LAYOUT.xaxis, title: '', tickangle: -75, tickfont: { size: 8, color: '#a1a1aa' } },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'ms per iter (log)', tickfont: { size: 10 } },
        margin: { l: 50, r: 10, t: 10, b: 100 },
      }, BASE_CONFIG);
    },
    renderOverheadFraction(baseMap) {
      // Single chart: overhead fraction vs cores, one line per (impl, grid, comm, io) series
      const groups = {};
      for (const r of this.filtered) {
        if (r.cores === 1) continue;
        const d = this.overheadDecomp(r, baseMap);
        if (!d) continue;
        const k = this.seriesKey(r);
        if (!groups[k]) groups[k] = [];
        groups[k].push({ cores: r.cores, frac: d.frac * 100, r });
      }
      const traces = [];
      Object.keys(groups).sort().forEach(k => {
        const pts = groups[k].sort((a,b) => a.cores - b.cores);
        const r0 = pts[0].r;
        traces.push({
          x: pts.map(p => p.cores), y: pts.map(p => p.frac),
          mode: 'lines+markers', type: 'scatter',
          name: this.seriesLabel(r0),
          line: { color: GRID_COLORS[r0.grid], dash: IMPL_DASH[r0.impl], width: 1.5 },
          marker: { symbol: COMM_SYMBOL[r0.comm], size: 6, color: GRID_COLORS[r0.grid] },
          hovertemplate: '<b>%{fullData.name}</b><br>%{x} cores<br>Overhead: %{y:.1f}%<extra></extra>',
        });
      });
      // 50% reference line
      const coreRange = [...new Set(this.filtered.map(r=>r.cores).filter(c=>c>1))].sort((a,b)=>a-b);
      if (coreRange.length) {
        traces.push({
          x: [coreRange[0], coreRange[coreRange.length-1]], y: [50, 50],
          mode: 'lines', name: '50% (scaling death)',
          line: { color: 'rgba(248,113,113,0.4)', dash: 'dash', width: 1 },
          hoverinfo: 'skip',
        });
      }
      Plotly.react('chart-overhead-fraction', traces, {
        ...BASE_LAYOUT,
        xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'Cores (log)' },
        yaxis: { ...BASE_LAYOUT.yaxis, title: 'Overhead fraction (%)', range: [0, 100] },
      }, BASE_CONFIG);
    },

    // ==================== DECOMPOSITION VISUALIZER ====================
    get decompOptions() {
      // Show parallel runs only (cores > 1), unique decomp shapes
      // To keep dropdown manageable, group by (impl, grid, cores) — pick no_io, p2p rep.
      const seen = new Set();
      const opts = [];
      for (const r of this.filtered) {
        if (r.cores === 1) continue;
        const k = `${r.impl}|${r.grid}|${r.cores}`;
        if (seen.has(k)) continue;
        // Prefer p2p + no_io as canonical; fall back to whatever
        const canonical = this.filtered.find(x => x.impl === r.impl && x.grid === r.grid && x.cores === r.cores && x.comm === 'p2p' && x.io === 'none')
                          || r;
        seen.add(k);
        opts.push(canonical);
      }
      return opts.sort((a,b) => a.impl.localeCompare(b.impl) || a.cores - b.cores || a.grid - b.grid);
    },
    decompLabel(r) {
      return `${IMPL_LABEL[r.impl]} · ${r.cores} cores · ${r.grid}² (${r.decomp_x}×${r.decomp_y} MPI, ${r.omp} OMP)`;
    },
    ensureDecompSelection() {
      const opts = this.decompOptions;
      if (opts.length === 0) { this.decompRunId = null; return; }
      if (!opts.some(o => o._id === this.decompRunId)) {
        // Default: pick the most interesting one — largest grid, most cores
        const best = opts.slice().sort((a,b) => (b.grid*b.cores) - (a.grid*a.cores))[0];
        this.decompRunId = best._id;
      }
    },
    // ==================== NODE TOPOLOGY ====================
    renderTopology() {
      const run = DATA.runs.find(r => r._id == this.decompRunId);
      const el = document.getElementById('topology-svg');
      if (!el) return;
      if (!run) { el.innerHTML = ''; return; }

      // Also wire up decomp ↔ topology cross-highlight in decompRender (below)
      if (run.cores === 1) {
        el.innerHTML = `<div class="text-zinc-500 text-sm py-8 text-center border border-white/5 rounded-lg">1-core baseline — single rank on a single core, no topology to show.</div>`;
        return;
      }

      // Real Barbora hardware (Intel Xeon Gold 6240 Cascade Lake): 2 sockets × 18 cores = 36 cores/node
      const HW = { cores_per_socket: 18, sockets_per_node: 2, cores_per_node: 36 };
      const TPN = { mpi_2d: 32, hybrid_1d: 2, hybrid_2d: 4 };
      const tpn = TPN[run.impl];
      const cpt = run.omp;
      const totalRanks = run.mpi;
      const nNodes = Math.max(1, Math.ceil(totalRanks / tpn));

      // MPI_Cart_create dims = {nX, nY} — last dim (Y) varies fastest in rank numbering
      const nX = run.decomp_x, nY = run.decomp_y;
      const rankToCart = (r) => ({ rx: Math.floor(r / nY), ry: r % nY });
      const cartToRank = (rx, ry) => {
        if (rx < 0 || rx >= nX || ry < 0 || ry >= nY) return null;
        return rx * nY + ry;
      };

      // SLURM rank → (socket, localInSocket) per impl's distribution
      //   MPI 2D    block:block:block,Pack   → fill socket 0 first, then socket 1
      //   Hybrid *  block:cyclic:cyclic      → alternate sockets per rank
      const distribute = (localRank, nTasksOnNode) => {
        if (run.impl === 'mpi_2d') {
          const half = Math.ceil(nTasksOnNode / 2);
          const socket = localRank < half ? 0 : 1;
          const withinSocket = localRank < half ? localRank : (localRank - half);
          return { socket, localInSocket: withinSocket * cpt };
        } else {
          const socket = localRank % HW.sockets_per_node;
          const withinSocket = Math.floor(localRank / HW.sockets_per_node);
          return { socket, localInSocket: withinSocket * cpt };
        }
      };

      // Build mapping per rank
      const ranks = [];
      for (let r = 0; r < totalRanks; r++) {
        const node = Math.floor(r / tpn);
        const localRank = r % tpn;
        const nTasksOnThisNode = Math.min(tpn, totalRanks - node * tpn);
        const { socket, localInSocket } = distribute(localRank, nTasksOnThisNode);
        const { rx, ry } = rankToCart(r);
        ranks.push({
          r, node, localRank, socket, localInSocket, cores: cpt, rx, ry,
          startCore: socket * HW.cores_per_socket + localInSocket,
          endCore:   socket * HW.cores_per_socket + localInSocket + cpt - 1,
        });
      }
      const rankOf = r => ranks[r];

      // Layout — each socket shows 18 cores in a 3 rows × 6 cols grid
      const NODE_W = 340, NODE_H = 160, GAP = 14, PAD = 16;
      const cols = Math.min(4, nNodes);
      const rows = Math.ceil(nNodes / cols);
      const totalW = cols * NODE_W + (cols - 1) * GAP + 2 * PAD;
      const totalH = rows * NODE_H + (rows - 1) * GAP + 2 * PAD;

      const NODE_PAD = 10, SOCKET_GAP = 10, LABEL_H = 24, BOTTOM_H = 14;
      const SOCKET_W = (NODE_W - 2 * NODE_PAD - SOCKET_GAP) / 2;
      const SOCKET_H = NODE_H - LABEL_H - BOTTOM_H - 2 * 4;
      const CELL_COLS = 6, CELL_ROWS = 3;
      const CELL_W = (SOCKET_W - 8) / CELL_COLS;
      const CELL_H = (SOCKET_H - 8) / CELL_ROWS;

      // Hue scheme — match the decomp visualizer's rank color (rank_idx based)
      // Decomp uses: idx = ry * tx + rx (decomp viz convention) — but that's different from our cart-rank mapping!
      // Decomp visualizer orders ranks by (ry, rx) row-major, while MPI_Cart orders by (rx, ry). They end up different.
      // Use the actual MPI rank id for color to keep topology consistent.
      const rankHue = (r) => (r * 360 / totalRanks + 30) % 360;
      const rankFill = (r) => `hsl(${rankHue(r)} 65% 55% / 0.58)`;
      const rankBorder = (r) => `hsl(${rankHue(r)} 75% 72%)`;

      // Map each core slot → rank id
      const coreToRank = new Map();
      for (const rr of ranks) {
        for (let c = 0; c < rr.cores; c++) {
          coreToRank.set(`${rr.node}-${rr.socket}-${rr.localInSocket + c}`, rr.r);
        }
      }

      let svg = `<svg viewBox="0 0 ${totalW} ${totalH}" style="min-width:${Math.min(totalW, 1400)}px" class="h-auto block" xmlns="http://www.w3.org/2000/svg">`;
      svg += `<defs>
        <filter id="topo-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.5" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>`;

      // Lines layer (drawn first so ranks sit on top — will be revealed on hover)
      svg += `<g class="topo-edges" opacity="0" style="transition:opacity 0.15s" filter="url(#topo-glow)"></g>`;

      // Nodes
      for (let n = 0; n < nNodes; n++) {
        const col = n % cols, row = Math.floor(n / cols);
        const nx = PAD + col * (NODE_W + GAP);
        const ny = PAD + row * (NODE_H + GAP);

        svg += `<g class="topo-node" data-node="${n}">`;
        svg += `<rect x="${nx}" y="${ny}" width="${NODE_W}" height="${NODE_H}" rx="8" fill="rgba(255,255,255,0.02)" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`;
        svg += `<text x="${nx + 12}" y="${ny + 16}" font-family="JetBrains Mono" font-size="10" font-weight="600" fill="var(--text-2)">Node ${n}</text>`;
        svg += `<text x="${nx + NODE_W - 12}" y="${ny + 16}" text-anchor="end" font-family="Inter" font-size="10" fill="var(--text-3)">${ranks.filter(r=>r.node===n).length} ranks · ${HW.cores_per_node} cores</text>`;

        for (let s = 0; s < 2; s++) {
          const sx = nx + NODE_PAD + s * (SOCKET_W + SOCKET_GAP);
          const sy = ny + LABEL_H;
          svg += `<rect x="${sx}" y="${sy}" width="${SOCKET_W}" height="${SOCKET_H}" rx="4" fill="rgba(255,255,255,0.012)" stroke="rgba(255,255,255,0.08)" stroke-width="0.8"/>`;

          for (let ci = 0; ci < HW.cores_per_socket; ci++) {
            const cr = Math.floor(ci / CELL_COLS);
            const cc = ci % CELL_COLS;
            const cx = sx + 4 + cc * CELL_W;
            const cy = sy + 4 + cr * CELL_H;
            const rid = coreToRank.get(`${n}-${s}-${ci}`);
            if (rid === undefined) {
              // Idle core — dashed slot
              svg += `<rect x="${cx + 0.8}" y="${cy + 0.8}" width="${CELL_W - 1.6}" height="${CELL_H - 1.6}" rx="2" fill="rgba(255,255,255,0.015)" stroke="rgba(255,255,255,0.09)" stroke-width="0.6" stroke-dasharray="2 2"/>
                <title>Core ${s * HW.cores_per_socket + ci} — idle (not allocated by SLURM)</title>`;
            } else {
              svg += `<rect class="topo-core" data-rank="${rid}" x="${cx + 0.8}" y="${cy + 0.8}" width="${CELL_W - 1.6}" height="${CELL_H - 1.6}" rx="2" fill="${rankFill(rid)}" stroke="${rankBorder(rid)}" stroke-width="0.7" style="cursor:pointer;transition:all 0.15s"/>`;
            }
          }

          svg += `<text x="${sx + SOCKET_W/2}" y="${sy + SOCKET_H + 10}" text-anchor="middle" font-family="Inter" font-size="9" fill="var(--text-3)">Socket ${s}</text>`;

          // Rank labels in the middle of each rank's contiguous cells
          const ranksInSocket = ranks.filter(rr => rr.node === n && rr.socket === s);
          for (const rr of ranksInSocket) {
            const cells = [];
            for (let c = 0; c < rr.cores; c++) {
              const ci = rr.localInSocket + c;
              if (ci >= HW.cores_per_socket) break;
              const cRow = Math.floor(ci / CELL_COLS);
              const cCol = ci % CELL_COLS;
              cells.push({ x: sx + 4 + cCol * CELL_W + CELL_W/2, y: sy + 4 + cRow * CELL_H + CELL_H/2 });
            }
            if (!cells.length) continue;
            const mx = cells.reduce((a,c)=>a+c.x,0)/cells.length;
            const my = cells.reduce((a,c)=>a+c.y,0)/cells.length;
            const spanW = CELL_W * Math.min(rr.cores, CELL_COLS);
            if (spanW >= 22) {
              const fs = rr.cores >= 8 ? 11 : (rr.cores >= 2 ? 9 : 7);
              svg += `<text x="${mx}" y="${my + fs/3}" text-anchor="middle" font-family="JetBrains Mono" font-size="${fs}" font-weight="600" fill="rgba(255,255,255,0.92)" style="pointer-events:none">r${rr.r}</text>`;
            }
          }
        }
        svg += `</g>`;
      }
      svg += `</svg>`;
      el.innerHTML = svg;

      // Compute screen-space rank center for edge drawing
      const rankCenter = {};
      for (const rr of ranks) {
        const col = rr.node % cols, row = Math.floor(rr.node / cols);
        const nx = PAD + col * (NODE_W + GAP);
        const ny = PAD + row * (NODE_H + GAP);
        const sx = nx + NODE_PAD + rr.socket * (SOCKET_W + SOCKET_GAP);
        const sy = ny + LABEL_H;
        // Average cell position
        let px = 0, py = 0, nc = 0;
        for (let c = 0; c < rr.cores; c++) {
          const ci = rr.localInSocket + c;
          if (ci >= HW.cores_per_socket) break;
          const cRow = Math.floor(ci / CELL_COLS);
          const cCol = ci % CELL_COLS;
          px += sx + 4 + cCol * CELL_W + CELL_W/2;
          py += sy + 4 + cRow * CELL_H + CELL_H/2;
          nc++;
        }
        rankCenter[rr.r] = { x: px/nc, y: py/nc };
      }

      // Tooltip (shared with decomp visualizer)
      let tooltip = document.getElementById('decomp-tooltip');
      if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'decomp-tooltip';
        tooltip.style.cssText = 'position:fixed;pointer-events:none;z-index:9999;background:var(--bg-2);border:1px solid var(--border-hi);border-radius:8px;padding:10px 12px;font-family:JetBrains Mono,monospace;font-size:11px;color:var(--text);box-shadow:0 10px 30px rgba(0,0,0,0.5);opacity:0;transition:opacity 0.12s;max-width:320px;line-height:1.5';
        document.body.appendChild(tooltip);
      }

      const svgEl = el.querySelector('svg');
      const edgesG = svgEl.querySelector('.topo-edges');

      svgEl.querySelectorAll('.topo-core').forEach(cell => {
        const rid = +cell.dataset.rank;
        const me = rankOf(rid);
        const { rx, ry } = me;

        const nbs = [
          { dir: 'left',  id: cartToRank(rx - 1, ry) },
          { dir: 'right', id: cartToRank(rx + 1, ry) },
          { dir: 'up',    id: cartToRank(rx, ry - 1) },
          { dir: 'down',  id: cartToRank(rx, ry + 1) },
        ].filter(n => n.id !== null);

        cell.addEventListener('mouseenter', () => {
          // Dim all ranks first
          svgEl.querySelectorAll('.topo-core').forEach(c => {
            c.style.opacity = '0.2';
            c.style.strokeWidth = '0.7';
          });
          // Highlight my rank
          svgEl.querySelectorAll(`.topo-core[data-rank="${rid}"]`).forEach(c => {
            c.style.opacity = '1';
            c.style.strokeWidth = '1.6';
          });
          // 3-tier classification: same socket (fastest) / same node diff socket / cross node
          const classify = (nb) => {
            const d = rankOf(nb.id);
            if (d.node !== me.node) return 'cross_node';
            if (d.socket !== me.socket) return 'cross_socket';
            return 'same_socket';
          };
          const TIER_COLOR = {
            same_socket:  '#34d399',  // green — L3 shared
            cross_socket: '#60a5fa',  // blue — NUMA hop
            cross_node:   '#f87171',  // red — InfiniBand
          };
          const TIER_LABEL = {
            same_socket:  'same socket',
            cross_socket: 'same node (NUMA hop)',
            cross_node:   'cross-node',
          };

          for (const nb of nbs) {
            const tier = classify(nb);
            const hl = TIER_COLOR[tier];
            svgEl.querySelectorAll(`.topo-core[data-rank="${nb.id}"]`).forEach(c => {
              c.style.opacity = '0.95';
              c.setAttribute('stroke', hl);
              c.style.strokeWidth = '1.6';
            });
          }

          // Curved edges
          let edges = '';
          for (const nb of nbs) {
            const tier = classify(nb);
            const color = TIER_COLOR[tier];
            const p1 = rankCenter[rid];
            const p2 = rankCenter[nb.id];
            const mx = (p1.x + p2.x) / 2;
            const my = (p1.y + p2.y) / 2 - Math.min(40, Math.hypot(p2.x-p1.x, p2.y-p1.y) * 0.15);
            const dash = tier === 'cross_node' ? '4 3' : (tier === 'cross_socket' ? '2 2' : '0');
            edges += `<path d="M${p1.x},${p1.y} Q${mx},${my} ${p2.x},${p2.y}" fill="none" stroke="${color}" stroke-width="1.4" stroke-dasharray="${dash}" opacity="0.88"/>`;
          }
          edgesG.innerHTML = edges;
          edgesG.setAttribute('opacity', '1');

          // Tooltip
          const tiers = nbs.map(classify);
          const countBy = (t) => tiers.filter(x => x === t).length;
          const nbLine = (nb) => {
            const d = rankOf(nb.id);
            const tier = classify(nb);
            const color = TIER_COLOR[tier];
            return `<div><span style="color:#71717a">${nb.dir.padEnd(6)}</span><span style="color:${color}">→ r${nb.id}</span><span style="color:#71717a"> node ${d.node} · sock ${d.socket} (${TIER_LABEL[tier]})</span></div>`;
          };
          tooltip.innerHTML = `
            <div style="font-weight:600;color:var(--chip-fg);margin-bottom:4px">Rank ${rid} <span style="color:#71717a;font-weight:400">(cart ${rx},${ry})</span></div>
            <div style="color:#a1a1aa;margin-bottom:6px">Node ${me.node} · Socket ${me.socket} · cores ${me.startCore}–${me.endCore} · ${me.cores} OMP</div>
            <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:6px;margin-bottom:6px">
              <div style="color:#71717a;margin-bottom:3px">Cart neighbors (${nbs.length}/4)</div>
              ${nbs.map(nbLine).join('')}
            </div>
            <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:6px;font-size:10.5px">
              <span style="color:${TIER_COLOR.same_socket}">${countBy('same_socket')}</span> shared-L3 ·
              <span style="color:${TIER_COLOR.cross_socket}">${countBy('cross_socket')}</span> NUMA ·
              <span style="color:${TIER_COLOR.cross_node}">${countBy('cross_node')}</span> network
            </div>`;
          tooltip.style.opacity = '1';
        });
        cell.addEventListener('mousemove', (e) => {
          tooltip.style.left = Math.min(window.innerWidth - 340, e.clientX + 18) + 'px';
          tooltip.style.top = Math.min(window.innerHeight - 200, e.clientY + 18) + 'px';
        });
        cell.addEventListener('mouseleave', () => {
          svgEl.querySelectorAll('.topo-core').forEach(c => {
            c.style.opacity = '';
            c.style.strokeWidth = '0.7';
            const nativeRank = +c.dataset.rank;
            c.setAttribute('stroke', rankBorder(nativeRank));
          });
          edgesG.innerHTML = '';
          edgesG.setAttribute('opacity', '0');
          tooltip.style.opacity = '0';
        });
      });
    },

    renderDecomp() {
      const run = DATA.runs.find(r => r._id == this.decompRunId);
      const svgEl = document.getElementById('decomp-svg');
      const cfgEl = document.getElementById('decomp-config');
      const haloEl = document.getElementById('decomp-halo');
      const perfEl = document.getElementById('decomp-perf');
      if (!svgEl) return;
      if (!run) {
        svgEl.innerHTML = '<div class="text-zinc-500 text-sm">No run selected.</div>';
        return;
      }

      const { grid, decomp_x: tx, decomp_y: ty, omp, cores, mpi } = run;
      const halo = 2;  // haloZoneSize from the solver

      const CANVAS = 540;
      const PAD = 30;
      const size = CANVAS - 2*PAD;
      const sx = size / grid, sy = size / grid;
      const tileW = grid / tx, tileH = grid / ty;
      const tileWpx = tileW * sx, tileHpx = tileH * sy;

      // Visual halo thickness (not to scale — real halo is 2 cells)
      const haloVis = Math.min(10, Math.max(3, Math.min(tileWpx, tileHpx) * 0.10));

      const rankIdx = (rx, ry) => ry * tx + rx;
      const rankHue = (rx, ry) => (rankIdx(rx, ry) * 360 / (tx*ty) + 30) % 360;
      const rankColor = (rx, ry) => `hsl(${rankHue(rx,ry)} 65% 55% / 0.55)`;
      const rankBorder = (rx, ry) => `hsl(${rankHue(rx,ry)} 75% 72%)`;

      const tileBox = (rx, ry) => ({
        x: PAD + rx * tileWpx, y: PAD + ry * tileHpx,
        w: tileWpx, h: tileHpx,
      });

      let svg = `<svg viewBox="0 0 ${CANVAS} ${CANVAS}" class="w-full h-auto block" xmlns="http://www.w3.org/2000/svg">`;
      svg += `<defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.8" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>`;
      svg += `<rect x="${PAD}" y="${PAD}" width="${size}" height="${size}" fill="rgba(255,255,255,0.015)" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>`;

      // ----- DEFAULT halo strips between tiles (dim amber) — visible when not hovering -----
      svg += `<g class="default-halos">`;
      for (let rx = 0; rx < tx; rx++) {
        for (let ry = 0; ry < ty; ry++) {
          const { x, y, w, h } = tileBox(rx, ry);
          if (rx < tx - 1) {
            svg += `<rect x="${x + w - haloVis/2}" y="${y + haloVis/2}" width="${haloVis}" height="${h - haloVis}" fill="rgba(251,191,36,0.15)" stroke="rgba(251,191,36,0.4)" stroke-dasharray="2 2" stroke-width="0.6"/>`;
          }
          if (ry < ty - 1) {
            svg += `<rect x="${x + haloVis/2}" y="${y + h - haloVis/2}" width="${w - haloVis}" height="${haloVis}" fill="rgba(251,191,36,0.15)" stroke="rgba(251,191,36,0.4)" stroke-dasharray="2 2" stroke-width="0.6"/>`;
          }
        }
      }
      svg += `</g>`;

      // ----- RANK TILES (interactive) -----
      svg += `<g class="ranks">`;
      for (let rx = 0; rx < tx; rx++) {
        for (let ry = 0; ry < ty; ry++) {
          const { x, y, w, h } = tileBox(rx, ry);
          const idx = rankIdx(rx, ry);
          svg += `<g class="rank" data-rx="${rx}" data-ry="${ry}" style="cursor:pointer;transition:opacity 0.15s ease">`;
          svg += `<rect class="rank-rect" x="${x}" y="${y}" width="${w}" height="${h}" fill="${rankColor(rx, ry)}" stroke="${rankBorder(rx, ry)}" stroke-width="1" style="transition:all 0.15s ease"/>`;
          if (omp > 1) {
            for (let t = 1; t < omp; t++) {
              const ly = y + h * (t / omp);
              svg += `<line x1="${x + 1}" y1="${ly}" x2="${x + w - 1}" y2="${ly}" stroke="rgba(255,255,255,0.28)" stroke-width="0.6" stroke-dasharray="1.5 1.5" style="pointer-events:none"/>`;
            }
          }
          if (w > 26 && h > 16 && tx * ty <= 64) {
            svg += `<text x="${x + w/2}" y="${y + h/2 + 3}" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="${Math.min(11, Math.max(7, w/8))}" fill="rgba(255,255,255,0.9)" style="pointer-events:none">r${idx}</text>`;
          }
          svg += `</g>`;
        }
      }
      svg += `</g>`;

      // ----- HOVER OVERLAYS (per-rank halo flows, hidden by default) -----
      svg += `<g class="hover-overlays">`;
      for (let rx = 0; rx < tx; rx++) {
        for (let ry = 0; ry < ty; ry++) {
          const self = tileBox(rx, ry);
          svg += `<g class="hover-overlay" data-rank="${rx}-${ry}" opacity="0" style="pointer-events:none;transition:opacity 0.12s ease" filter="url(#glow)">`;
          // SEND strips (amber) inside SELF, at each edge that has a neighbor
          // RECV strips (cyan) inside NEIGHBOR tiles, at the adjacent edge
          const drawSend = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="rgba(251,191,36,0.85)" stroke="#fde68a" stroke-width="0.8"/>`;
          const drawRecv = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="rgba(96,165,250,0.82)" stroke="#bfdbfe" stroke-width="0.8"/>`;
          const drawArrow = (x1, y1, x2, y2, color) => {
            const dx = x2 - x1, dy = y2 - y1;
            const len = Math.hypot(dx, dy);
            if (len < 3) return '';
            const ux = dx/len, uy = dy/len;
            const hx = x2 - 5*ux, hy = y2 - 5*uy;
            const px = -uy * 3, py = ux * 3;
            return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1.3"/>
                    <polygon points="${x2},${y2} ${hx+px},${hy+py} ${hx-px},${hy-py}" fill="${color}"/>`;
          };

          // LEFT neighbor
          if (rx > 0) {
            const nb = tileBox(rx - 1, ry);
            svg += drawSend(self.x, self.y + haloVis, haloVis, self.h - 2*haloVis);
            svg += drawRecv(nb.x + nb.w - haloVis, nb.y + haloVis, haloVis, nb.h - 2*haloVis);
            svg += drawArrow(self.x + haloVis*1.5, self.y + self.h/2, self.x - haloVis*0.2, self.y + self.h/2, '#fcd34d');
          }
          // RIGHT neighbor
          if (rx < tx - 1) {
            const nb = tileBox(rx + 1, ry);
            svg += drawSend(self.x + self.w - haloVis, self.y + haloVis, haloVis, self.h - 2*haloVis);
            svg += drawRecv(nb.x, nb.y + haloVis, haloVis, nb.h - 2*haloVis);
            svg += drawArrow(self.x + self.w - haloVis*1.5, self.y + self.h/2, self.x + self.w + haloVis*0.2, self.y + self.h/2, '#fcd34d');
          }
          // UP neighbor
          if (ry > 0) {
            const nb = tileBox(rx, ry - 1);
            svg += drawSend(self.x + haloVis, self.y, self.w - 2*haloVis, haloVis);
            svg += drawRecv(nb.x + haloVis, nb.y + nb.h - haloVis, nb.w - 2*haloVis, haloVis);
            svg += drawArrow(self.x + self.w/2, self.y + haloVis*1.5, self.x + self.w/2, self.y - haloVis*0.2, '#fcd34d');
          }
          // DOWN neighbor
          if (ry < ty - 1) {
            const nb = tileBox(rx, ry + 1);
            svg += drawSend(self.x + haloVis, self.y + self.h - haloVis, self.w - 2*haloVis, haloVis);
            svg += drawRecv(nb.x + haloVis, nb.y, nb.w - 2*haloVis, haloVis);
            svg += drawArrow(self.x + self.w/2, self.y + self.h - haloVis*1.5, self.x + self.w/2, self.y + self.h + haloVis*0.2, '#fcd34d');
          }
          svg += `</g>`;
        }
      }
      svg += `</g>`;

      // Corner annotations
      svg += `<text x="${PAD}" y="${PAD - 10}" font-family="JetBrains Mono" font-size="11" fill="#a1a1aa">${grid} × ${grid}</text>`;
      svg += `<text x="${CANVAS - PAD}" y="${PAD - 10}" text-anchor="end" font-family="JetBrains Mono" font-size="11" fill="#a1a1aa">${tx}×${ty} MPI ranks${omp > 1 ? `, ${omp} OMP/rank` : ''}</text>`;
      svg += `<text x="${CANVAS/2}" y="${CANVAS - 8}" text-anchor="middle" font-family="Inter" font-size="10" fill="#71717a" font-style="italic">hover a rank to see its halo flows</text>`;
      svg += `</svg>`;
      svgEl.innerHTML = svg;

      // ----- HOVER INTERACTIONS -----
      let tooltip = document.getElementById('decomp-tooltip');
      if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'decomp-tooltip';
        tooltip.style.cssText = 'position:fixed;pointer-events:none;z-index:9999;background:var(--bg-2);border:1px solid var(--border-hi);border-radius:8px;padding:10px 12px;font-family:JetBrains Mono,monospace;font-size:11px;color:var(--text);box-shadow:0 10px 30px rgba(0,0,0,0.5);opacity:0;transition:opacity 0.12s;max-width:280px;line-height:1.5';
        document.body.appendChild(tooltip);
      }

      const bytesPerEdge = {  // float cells × 4 bytes
        vert: tileH * halo * 4,
        horiz: tileW * halo * 4,
      };
      const fmt = (n) => n >= 1024 ? (n/1024).toFixed(1) + ' KB' : n.toFixed(0) + ' B';

      svgEl.querySelectorAll('.rank').forEach(el => {
        const rx = +el.dataset.rx, ry = +el.dataset.ry;
        const idx = rankIdx(rx, ry);
        const nbs = {
          left:  rx > 0        ? rankIdx(rx-1, ry) : null,
          right: rx < tx - 1   ? rankIdx(rx+1, ry) : null,
          up:    ry > 0        ? rankIdx(rx, ry-1) : null,
          down:  ry < ty - 1   ? rankIdx(rx, ry+1) : null,
        };
        const nCount = Object.values(nbs).filter(v => v !== null).length;
        const totalBytes = (nbs.left!==null || nbs.right!==null ? (+(nbs.left!==null) + +(nbs.right!==null)) * bytesPerEdge.vert : 0)
                         + (nbs.up!==null || nbs.down!==null ? (+(nbs.up!==null) + +(nbs.down!==null)) * bytesPerEdge.horiz : 0);

        el.addEventListener('mouseenter', () => {
          svgEl.querySelector(`.hover-overlay[data-rank="${rx}-${ry}"]`).setAttribute('opacity', '1');
          svgEl.querySelectorAll('.rank').forEach(r => {
            if (r !== el) r.style.opacity = '0.28';
          });
          svgEl.querySelectorAll('.default-halos rect').forEach(r => r.setAttribute('opacity', '0.25'));
          const nbLine = (dir, v) => v !== null ? `<div><span style="color:#71717a">${dir.padEnd(6)}</span><span style="color:#fde68a">→ r${v}</span></div>` : '';
          tooltip.innerHTML = `
            <div style="font-weight:600;color:#bfdbfe;margin-bottom:4px">Rank ${idx} <span style="color:#71717a;font-weight:400">(cart ${rx},${ry})</span></div>
            <div style="color:#a1a1aa;margin-bottom:6px">Tile: ${Math.round(tileW)} × ${Math.round(tileH)} cells</div>
            <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:6px;margin-bottom:6px">
              <div style="color:#71717a;margin-bottom:3px">Halo neighbors (${nCount}/4)</div>
              ${nbLine('left',  nbs.left)}
              ${nbLine('right', nbs.right)}
              ${nbLine('up',    nbs.up)}
              ${nbLine('down',  nbs.down)}
            </div>
            <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:6px">
              <div style="color:#71717a;margin-bottom:3px">Per iteration</div>
              <div><span style="color:#fde68a">send</span> ${fmt(totalBytes)} · <span style="color:#bfdbfe">recv</span> ${fmt(totalBytes)}</div>
            </div>`;
          tooltip.style.opacity = '1';
        });
        el.addEventListener('mousemove', (e) => {
          const x = Math.min(window.innerWidth - 300, e.clientX + 18);
          const y = Math.min(window.innerHeight - 180, e.clientY + 18);
          tooltip.style.left = x + 'px';
          tooltip.style.top = y + 'px';
        });
        el.addEventListener('mouseleave', () => {
          svgEl.querySelectorAll('.hover-overlay').forEach(o => o.setAttribute('opacity', '0'));
          svgEl.querySelectorAll('.rank').forEach(r => r.style.opacity = '1');
          svgEl.querySelectorAll('.default-halos rect').forEach(r => r.removeAttribute('opacity'));
          tooltip.style.opacity = '0';
        });
      });

      // Config panel
      const row = (k, v, color) => `<div class="flex justify-between gap-3">
        <span class="text-zinc-500">${k}</span>
        <span class="mono" style="color:${color || '#e4e4e7'}">${v}</span>
      </div>`;
      cfgEl.innerHTML = [
        row('Implementation', IMPL_LABEL[run.impl]),
        row('Grid', `${grid} × ${grid}`),
        row('MPI ranks', `${mpi} (${tx} × ${ty})`),
        row('OMP threads/rank', omp),
        row('Total cores', cores),
        row('Tile size', `${Math.round(tileW)} × ${Math.round(tileH)} cells`),
        row('OMP row-stripe', omp > 1 ? `${Math.round(tileH/omp)} rows/thread` : 'n/a'),
        row('Communication', run.comm.toUpperCase()),
        row('I/O mode', this.ioLabel(run.io)),
      ].join('');

      // Halo geometry
      const hasL = run.decomp_x > 1, hasR = run.decomp_x > 1;
      const hasU = run.decomp_y > 1, hasD = run.decomp_y > 1;
      const neighbors = (hasL?1:0) + (hasR?1:0) + (hasU?1:0) + (hasD?1:0);
      // Halo cells this rank exchanges (corner rank has fewer, interior rank all 4)
      const innerHalo = 2 * (tileW + tileH) * halo;  // interior rank, 4 neighbors
      const tileArea = tileW * tileH;
      const haloFrac = innerHalo / tileArea * 100;
      haloEl.innerHTML = [
        row('Halo depth', `${halo} cells`),
        row('Neighbors (interior)', `${Math.min(4, neighbors === 0 ? 0 : neighbors)} of 4`),
        row('Halo cells / iter', Math.round(innerHalo).toLocaleString()),
        row('Halo fraction', haloFrac.toFixed(2) + '%', haloFrac > 10 ? '#fbbf24' : '#34d399'),
        row('Halo bytes / iter', (innerHalo * 4).toLocaleString() + ' B'),
      ].join('');

      // Performance
      const spd = run.speedup != null ? run.speedup.toFixed(1) + '×' : '—';
      const eff = run.efficiency != null ? run.efficiency.toFixed(0) + '%' : '—';
      const spdColor = run.speedup == null ? '#a1a1aa' : (run.speedup >= 100 ? '#34d399' : run.speedup >= 30 ? '#fbbf24' : '#a1a1aa');
      const effColor = run.efficiency == null ? '#a1a1aa' : (run.efficiency >= 100 ? '#34d399' : run.efficiency >= 50 ? '#fbbf24' : '#f87171');
      perfEl.innerHTML = [
        row('Iteration', run.iter_time_ms.toFixed(4) + ' ms'),
        row('Total time', run.total_time.toFixed(2) + ' s'),
        row('Iterations', run.iters.toLocaleString()),
        row('Speedup', spd, spdColor),
        row('Efficiency', eff, effColor),
        row('Throughput', run.throughput_mcells.toFixed(0) + ' Mcells/s'),
      ].join('');

      // ===== CACHE FIT ANALYSIS =====
      // Heat solver hot working set per MPI rank: 2 ping-pong temperature buffers + material
      // properties + material types (int = 4B). All padded with haloZoneSize cells.
      const cacheEl = document.getElementById('decomp-cache');
      if (cacheEl) {
        const ARRAYS = 4;        // 2 × temp + 1 × props + 1 × types
        const BYTES_PER_CELL = 4;
        const pTileW = tileW + 2 * halo;
        const pTileH = tileH + 2 * halo;
        const rankBytes = ARRAYS * pTileW * pTileH * BYTES_PER_CELL;

        const seqPadded = (grid + 2 * halo) * (grid + 2 * halo);
        const seqBytes = ARRAYS * seqPadded * BYTES_PER_CELL;

        // Barbora (Intel Xeon Gold 6240, Cascade Lake)
        const CACHE = [
          { name: 'L1d', size: 32 * 1024,                 desc: '32 KB/core',   color: '#22d3ee' },
          { name: 'L2',  size: 1024 * 1024,               desc: '1 MB/core',    color: '#34d399' },
          { name: 'L3',  size: 25 * 1024 * 1024,          desc: '25 MB/socket', color: '#fbbf24' },
          { name: 'RAM', size: 190 * 1024 * 1024 * 1024,  desc: '190 GB/node',  color: '#f87171' },
        ];
        const fitsIn = b => CACHE.find(c => b <= c.size) || CACHE[CACHE.length - 1];
        const fmt = b => b < 1024 ? b + ' B'
                       : b < 1024**2 ? (b / 1024).toFixed(1) + ' KB'
                       : b < 1024**3 ? (b / 1024**2).toFixed(1) + ' MB'
                       : (b / 1024**3).toFixed(1) + ' GB';

        const rankFit = fitsIn(rankBytes);
        const seqFit = fitsIn(seqBytes);

        // Log-scale bar: x ∈ [16 KB, 256 GB]
        const minLog = Math.log10(16 * 1024);
        const maxLog = Math.log10(256 * 1024 ** 3);
        const pct = b => Math.max(0, Math.min(100, (Math.log10(Math.max(b, 1)) - minLog) / (maxLog - minLog) * 100));

        // Cache zones on bar
        let prevEnd = 0;
        const zones = CACHE.map(c => {
          const end = pct(c.size);
          const z = { ...c, start: prevEnd, end };
          prevEnd = end;
          return z;
        });

        const zonesHtml = zones.map(z => `
          <div class="absolute top-0 bottom-0 flex items-center justify-center"
               style="left:${z.start}%;width:${Math.max(0, z.end - z.start)}%;
                      background:${z.color}18;border-right:1px solid ${z.color}40;">
            <span class="text-[9px] font-bold mono" style="color:${z.color};opacity:0.8">${z.name}</span>
          </div>
        `).join('');

        const markerHtml = (leftPct, label, color, above) => `
          <div class="absolute" style="left:${leftPct}%;top:0;bottom:0;">
            <div class="absolute top-0 bottom-0" style="width:2px;left:-1px;background:${color};box-shadow:0 0 5px ${color}"></div>
            <div class="absolute text-[10px] mono font-semibold whitespace-nowrap"
                 style="color:${color};${above ? 'bottom:100%;margin-bottom:2px' : 'top:100%;margin-top:2px'};
                        transform:translateX(-50%);">${label}</div>
          </div>`;

        const rankPct = pct(rankBytes);
        const seqPct = pct(seqBytes);
        // If markers too close, stack them (per-rank above, seq below)
        const barHtml = `
          <div class="relative h-6 rounded-sm mt-6 mb-6" style="background:rgba(255,255,255,0.02)">
            ${zonesHtml}
            ${markerHtml(rankPct, '▼ per-rank', rankFit.color, true)}
            ${markerHtml(seqPct, 'seq ▲', '#a1a1aa', false)}
          </div>`;

        const isSuperLinear = run.efficiency != null && run.efficiency > 100;
        const eScale = rankFit.name === 'RAM' ? 1 : rankFit.name === 'L3' ? 3 : rankFit.name === 'L2' ? 10 : 30;

        let verdictHtml;
        if (cores === 1) {
          verdictHtml = `<div class="text-zinc-400">Baseline — whole grid in <span class="mono font-semibold" style="color:${seqFit.color}">${seqFit.name}</span>. This run defines speedup = 1×.</div>`;
        } else if (rankFit.name !== seqFit.name && (seqFit.name === 'RAM' || seqFit.name === 'L3')) {
          verdictHtml = isSuperLinear
            ? `<div><span class="font-bold" style="color:#34d399">✓ Super-linear confirmed (${run.efficiency.toFixed(0)}%)</span> — sequential was <span class="mono" style="color:${seqFit.color}">${seqFit.name}</span>-bound, each rank now cache-resident in <span class="mono" style="color:${rankFit.color}">${rankFit.name}</span>. Cache latency drop ~${eScale}× per access.</div>`
            : `<div><span class="font-semibold text-amber-300">Cache win expected but unrealized</span> — rank WS fits in ${rankFit.name} (seq was in ${seqFit.name}), yet efficiency = ${(run.efficiency||0).toFixed(0)}%. Halo/sync overhead eating the cache gain.</div>`;
        } else if (rankFit.name === seqFit.name) {
          verdictHtml = `<div class="text-zinc-400">Chunks and sequential both in <span class="mono" style="color:${rankFit.color}">${rankFit.name}</span> — no cache-level jump, expect sub-linear scaling (Amdahl territory).</div>`;
        } else {
          verdictHtml = `<div class="text-zinc-400">Rank WS in <span class="mono font-semibold" style="color:${rankFit.color}">${rankFit.name}</span> (${Math.round(rankBytes/rankFit.size*100)}% of ${rankFit.desc}).</div>`;
        }

        cacheEl.innerHTML = `
          <div class="space-y-1.5">
            <div class="flex justify-between gap-3">
              <span class="text-zinc-500">Sequential WS</span>
              <span class="mono" style="color:${seqFit.color}">${fmt(seqBytes)} · ${seqFit.name}</span>
            </div>
            <div class="flex justify-between gap-3">
              <span class="text-zinc-500">Per-rank WS</span>
              <span class="mono font-bold" style="color:${rankFit.color}">${fmt(rankBytes)} · ${rankFit.name}</span>
            </div>
            <div class="flex justify-between gap-3">
              <span class="text-zinc-500">WS model</span>
              <span class="mono text-zinc-400">${ARRAYS} arr × ${Math.round(pTileW)}×${Math.round(pTileH)} × 4B</span>
            </div>
          </div>
          ${barHtml}
          <div class="text-[11px] leading-snug p-2 rounded"
               style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05)">
            ${verdictHtml}
          </div>
        `;
      }
    },

    renderAmdahl() {
      // Fit Amdahl: 1/S = f + (1-f)/N  →  y = a + b*x where x=1/N, y=1/S, f≈a
      // Unconstrained OLS. f<0 means super-linear throughout — we skip the predicted curve there.
      const groups = {};
      for (const r of this.filtered) {
        if (r.speedup == null) continue;
        const k = this.seriesKey(r);
        if (!groups[k]) groups[k] = [];
        groups[k].push(r);
      }
      const traces = [];
      const fits = [];

      Object.keys(groups).sort().forEach(k => {
        const runs = groups[k].sort((a,b) => a.cores - b.cores);
        const parallel = runs.filter(r => r.cores > 1);
        if (parallel.length < 2) return;

        const xs = parallel.map(r => 1/r.cores);
        const ys = parallel.map(r => 1/r.speedup);
        const n = xs.length;
        const mx = xs.reduce((a,b) => a+b, 0) / n;
        const my = ys.reduce((a,b) => a+b, 0) / n;
        let num = 0, den = 0;
        for (let i = 0; i < n; i++) { num += (xs[i]-mx)*(ys[i]-my); den += (xs[i]-mx)**2; }
        const b = den > 0 ? num/den : 0;
        const a = my - b*mx;
        const f = a;            // serial fraction
        const ceiling = f > 0 ? 1/f : null;

        const r0 = runs[0];
        const label = this.seriesLabel(r0);
        const color = GRID_COLORS[r0.grid];

        // Measured points (+ 1-core baseline if present)
        traces.push({
          x: runs.map(r => r.cores),
          y: runs.map(r => r.speedup),
          mode: 'markers',
          type: 'scatter',
          name: label,
          legendgroup: k,
          marker: { symbol: COMM_SYMBOL[r0.comm], size: 8, color, opacity: 0.9, line: { color: 'rgba(0,0,0,0.3)', width: 0.5 } },
          hovertemplate: '<b>%{fullData.name}</b><br>%{x} cores<br>Measured speedup: %{y:.1f}×<extra></extra>',
        });

        if (f > 0 && f < 1) {
          // Fitted Amdahl curve across the measured core range
          const maxN = Math.max(...runs.map(r => r.cores));
          const ncores = [];
          for (let N = 1; N <= maxN * 1.2; N = N < 4 ? N+1 : Math.ceil(N*1.3)) ncores.push(N);
          if (ncores[ncores.length-1] < maxN) ncores.push(maxN);
          const pred = ncores.map(N => 1 / (f + (1-f)/N));
          traces.push({
            x: ncores,
            y: pred,
            mode: 'lines',
            type: 'scatter',
            name: `${label} · fit ƒ=${(f*100).toFixed(2)}%`,
            legendgroup: k,
            showlegend: false,
            line: { color, dash: IMPL_DASH[r0.impl], width: 1.5 },
            hovertemplate: `<b>Amdahl fit · ƒ=${(f*100).toFixed(2)}%</b><br>%{x} cores<br>Predicted: %{y:.1f}×<br>Ceiling: ${ceiling.toFixed(0)}×<extra></extra>`,
          });
        }

        fits.push({ label, f, ceiling, r0, key: k });
      });

      // Ideal linear reference
      const allN = [...new Set(this.filtered.map(r => r.cores))].sort((a,b)=>a-b);
      if (allN.length) {
        traces.push({
          x: allN, y: allN, mode: 'lines', name: 'Ideal (linear)',
          line: { color: 'rgba(255,255,255,0.25)', dash: 'dash', width: 1 },
          hoverinfo: 'skip',
        });
      }

      Plotly.react('chart-amdahl', traces, {
        ...BASE_LAYOUT,
        xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'Cores (log)' },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'Speedup (log)' },
        legend: { ...BASE_LAYOUT.legend, y: -0.22 },
      }, BASE_CONFIG);

      // Sidebar table
      const wrap = document.getElementById('amdahl-table');
      if (wrap) {
        if (fits.length === 0) {
          wrap.innerHTML = '<div class="text-zinc-500">No series with ≥2 parallel points in current filter.</div>';
          return;
        }
        fits.sort((a, b) => {
          // Super-linear first (negative f), then ascending f
          const av = a.f < 0 ? Infinity : a.f;
          const bv = b.f < 0 ? Infinity : b.f;
          if (a.f < 0 && b.f >= 0) return -1;
          if (b.f < 0 && a.f >= 0) return 1;
          return av - bv;
        });
        wrap.innerHTML = fits.map(fit => {
          const color = GRID_COLORS[fit.r0.grid];
          let fCell, capCell;
          if (fit.f < 0) {
            fCell = '<span style="color:#a78bfa">super-linear</span>';
            capCell = '<span class="text-zinc-500">—</span>';
          } else if (fit.f > 0.999) {
            fCell = '<span style="color:#f87171">ƒ ≈ 100%</span>';
            capCell = '<span class="text-zinc-500">saturated</span>';
          } else {
            const fColor = fit.f < 0.005 ? '#34d399' : fit.f < 0.02 ? '#fbbf24' : '#f87171';
            fCell = `<span style="color:${fColor}">ƒ = ${(fit.f*100).toFixed(2)}%</span>`;
            capCell = `<span style="color:#a1a1aa">cap ${fit.ceiling.toFixed(0)}×</span>`;
          }
          return `<div class="flex justify-between items-center py-1.5 gap-2 border-b border-white/5 last:border-0">
            <span class="flex items-center gap-2 min-w-0 flex-1">
              <span class="inline-block w-2 h-2 rounded-full shrink-0" style="background:${color}"></span>
              <span class="truncate text-zinc-300" style="font-size:11px">${fit.label}</span>
            </span>
            <span class="mono shrink-0" style="font-size:11px">
              ${fCell} <span class="text-zinc-600">·</span> ${capCell}
            </span>
          </div>`;
        }).join('');
      }
    },

    seriesKey(r) { return `${r.impl}|${r.grid}|${r.comm}|${r.io}`; },
    seriesLabel(r) {
      return `${r.grid}² · ${IMPL_LABEL[r.impl]} · ${r.comm.toUpperCase()}${r.io !== 'none' ? ' · ' + this.ioLabel(r.io) : ''}`;
    },

    buildSeries(yKey) {
      // Group filtered runs by (impl, grid, comm, io), sort by cores
      const groups = {};
      for (const r of this.filtered) {
        const k = this.seriesKey(r);
        if (!groups[k]) groups[k] = [];
        groups[k].push(r);
      }
      const traces = [];
      Object.keys(groups).sort().forEach(k => {
        const runs = groups[k].sort((a,b) => a.cores - b.cores);
        const r0 = runs[0];
        traces.push({
          x: runs.map(r => r.cores),
          y: runs.map(r => r[yKey]),
          mode: 'lines+markers',
          type: 'scatter',
          name: this.seriesLabel(r0),
          line: { color: GRID_COLORS[r0.grid], dash: IMPL_DASH[r0.impl], width: 1.8 },
          marker: { symbol: COMM_SYMBOL[r0.comm], size: 7, color: GRID_COLORS[r0.grid], opacity: r0.io === 'none' ? 1 : (r0.io === 'seq' ? 0.6 : 0.35) },
          opacity: r0.io === 'none' ? 1 : 0.7,
          hovertemplate: '<b>%{fullData.name}</b><br>%{x} cores<br>%{y}<extra></extra>',
        });
      });
      return traces;
    },

    scalingHint() {
      const hints = {
        scaling:    'Log-log plot. <b>Ideal = straight downward line at slope −1</b>: doubling cores halves time. Flat tail = communication/sync overhead.',
        speedup:    '<b>Ideal = y=x diagonal (dashed)</b>. Above diagonal = super-linear (cache wins). Plateau = saturation.',
        efficiency: '<b>100% = ideal</b>. Above = super-linear (legitimate on large grids due to cache). Dropping toward 0 = classic strong-scaling collapse.',
      };
      return hints[this.scalingTab];
    },

    renderScaling() {
      let yKey, title, extraLayout = {};
      if (this.scalingTab === 'scaling') {
        yKey = 'iter_time_ms';
        title = 'Iteration time (ms)';
        extraLayout = {
          xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'Cores (log₂)' },
          yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'ms per iteration (log₂)' },
        };
      } else if (this.scalingTab === 'speedup') {
        yKey = 'speedup';
        title = 'Speedup';
        extraLayout = {
          xaxis: { ...BASE_LAYOUT.xaxis, title: 'Cores' },
          yaxis: { ...BASE_LAYOUT.yaxis, title: 'Speedup (T₁ / Tₚ)' },
        };
      } else {
        yKey = 'efficiency';
        title = 'Efficiency %';
        extraLayout = {
          xaxis: { ...BASE_LAYOUT.xaxis, title: 'Cores' },
          yaxis: { ...BASE_LAYOUT.yaxis, title: 'Efficiency (%)' },
        };
      }
      const traces = this.buildSeries(yKey);

      // Add ideal reference line
      const cores = [...new Set(this.filtered.map(r => r.cores))].sort((a,b) => a-b);
      if (this.scalingTab === 'speedup' && cores.length) {
        traces.push({
          x: cores, y: cores, mode: 'lines', name: 'Ideal (linear)',
          line: { color: 'rgba(255,255,255,0.3)', dash: 'dash', width: 1 },
          hoverinfo: 'skip',
        });
      } else if (this.scalingTab === 'efficiency' && cores.length) {
        traces.push({
          x: [Math.min(...cores), Math.max(...cores)], y: [100, 100], mode: 'lines', name: 'Ideal (100%)',
          line: { color: 'rgba(255,255,255,0.3)', dash: 'dash', width: 1 },
          hoverinfo: 'skip',
        });
      }

      Plotly.react('chart-scaling', traces, { ...BASE_LAYOUT, ...extraLayout }, BASE_CONFIG);
    },

    renderThroughput() {
      const traces = this.buildSeries('throughput_mcells');
      Plotly.react('chart-throughput', traces, {
        ...BASE_LAYOUT,
        xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'Cores' },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'Mcells updated / sec' },
      }, BASE_CONFIG);
    },

    renderParity() {
      // For each (impl, grid, cores, io), plot P2P time vs RMA time
      const pairs = {};
      for (const r of this.filtered) {
        const k = `${r.impl}|${r.grid}|${r.cores}|${r.io}`;
        if (!pairs[k]) pairs[k] = {};
        pairs[k][r.comm] = r;
      }
      const dots = Object.values(pairs).filter(p => p.p2p && p.rma);
      const traces = [];
      // Group by grid for color
      const byGrid = {};
      for (const p of dots) {
        if (!byGrid[p.p2p.grid]) byGrid[p.p2p.grid] = [];
        byGrid[p.p2p.grid].push(p);
      }
      Object.keys(byGrid).sort((a,b) => a-b).forEach(g => {
        const pts = byGrid[g];
        traces.push({
          x: pts.map(p => p.p2p.iter_time_ms),
          y: pts.map(p => p.rma.iter_time_ms),
          mode: 'markers',
          type: 'scatter',
          name: `${g}²`,
          marker: { color: GRID_COLORS[g], size: 9, line: { color: 'rgba(0,0,0,0.3)', width: 0.5 } },
          text: pts.map(p => `${IMPL_LABEL[p.p2p.impl]} · ${p.p2p.cores} cores · ${p.p2p.io}`),
          hovertemplate: '<b>%{text}</b><br>P2P: %{x:.4f} ms<br>RMA: %{y:.4f} ms<extra></extra>',
        });
      });
      // Diagonal
      if (dots.length) {
        const vals = dots.flatMap(p => [p.p2p.iter_time_ms, p.rma.iter_time_ms]);
        const lo = Math.min(...vals), hi = Math.max(...vals);
        traces.push({
          x: [lo, hi], y: [lo, hi], mode: 'lines', name: 'y = x', line: { color: 'rgba(255,255,255,0.3)', dash: 'dash', width: 1 },
          hoverinfo: 'skip',
        });
      }
      Plotly.react('chart-parity', traces, {
        ...BASE_LAYOUT,
        xaxis: { ...BASE_LAYOUT.xaxis, type: 'log', title: 'P2P iter time (ms)' },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'RMA iter time (ms)' },
      }, BASE_CONFIG);
    },

    renderIo() {
      // For each (impl, grid, cores, comm), plot three bars: no / seq / par
      const groups = {};
      for (const r of this.filtered) {
        const k = `${r.impl}|${r.grid}|${r.cores}|${r.comm}`;
        if (!groups[k]) groups[k] = {};
        groups[k][r.io] = r;
      }
      // Pick best representative: max cores per grid, per impl, one comm (prefer p2p)
      // Simpler: show all rows as grouped bars, x = cores label, color = io
      const ioColors = { none: '#71717a', seq: '#fbbf24', par: '#34d399' };
      const labelsSet = new Set();
      const byIo = { none: { x: [], y: [], text: [] }, seq: { x: [], y: [], text: [] }, par: { x: [], y: [], text: [] } };
      const entries = Object.values(groups).sort((a,b) => {
        const ra = a.none || a.seq || a.par;
        const rb = b.none || b.seq || b.par;
        return ra.cores - rb.cores || ra.grid - rb.grid || ra.impl.localeCompare(rb.impl);
      });
      for (const g of entries) {
        const ref = g.none || g.seq || g.par;
        const label = `${ref.grid}² · ${ref.cores}c · ${IMPL_LABEL[ref.impl]} · ${ref.comm.toUpperCase()}`;
        labelsSet.add(label);
        ['none', 'seq', 'par'].forEach(io => {
          if (g[io]) {
            byIo[io].x.push(label);
            byIo[io].y.push(g[io].iter_time_ms);
            byIo[io].text.push(g[io].iter_time_ms.toFixed(3) + ' ms');
          }
        });
      }
      const traces = ['none', 'seq', 'par'].map(io => ({
        x: byIo[io].x, y: byIo[io].y,
        type: 'bar', name: this.ioLabel(io),
        marker: { color: ioColors[io] },
        hovertemplate: '<b>%{x}</b><br>' + this.ioLabel(io) + ': %{y:.4f} ms<extra></extra>',
      }));
      Plotly.react('chart-io', traces, {
        ...BASE_LAYOUT,
        barmode: 'group',
        xaxis: { ...BASE_LAYOUT.xaxis, title: '', tickangle: -45, tickfont: { size: 9, color: '#a1a1aa' } },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'Iter time (ms, log)' },
        margin: { l: 60, r: 20, t: 20, b: 140 },
      }, BASE_CONFIG);
    },

    renderImpl() {
      // For each (grid, cores, comm, io), show 3 bars (one per impl) where data exists
      const groups = {};
      for (const r of this.filtered) {
        const k = `${r.grid}|${r.cores}|${r.comm}|${r.io}`;
        if (!groups[k]) groups[k] = {};
        groups[k][r.impl] = r;
      }
      const kept = Object.entries(groups).filter(([_, g]) => Object.keys(g).length >= 2);
      const implColors = { mpi_2d: '#60a5fa', hybrid_1d: '#34d399', hybrid_2d: '#a78bfa' };
      const byImpl = { mpi_2d: { x: [], y: [] }, hybrid_1d: { x: [], y: [] }, hybrid_2d: { x: [], y: [] } };
      kept.sort(([ka,_a],[kb,_b]) => {
        const [ga, ca] = ka.split('|'); const [gb, cb] = kb.split('|');
        return (+ga) - (+gb) || (+ca) - (+cb);
      });
      for (const [k, g] of kept) {
        const ref = Object.values(g)[0];
        const label = `${ref.grid}² · ${ref.cores}c · ${ref.comm.toUpperCase()} · ${this.ioLabel(ref.io)}`;
        ['mpi_2d', 'hybrid_1d', 'hybrid_2d'].forEach(imp => {
          if (g[imp]) { byImpl[imp].x.push(label); byImpl[imp].y.push(g[imp].iter_time_ms); }
        });
      }
      const traces = ['mpi_2d', 'hybrid_1d', 'hybrid_2d'].map(imp => ({
        x: byImpl[imp].x, y: byImpl[imp].y,
        type: 'bar', name: IMPL_LABEL[imp],
        marker: { color: implColors[imp] },
        hovertemplate: '<b>%{x}</b><br>' + IMPL_LABEL[imp] + ': %{y:.4f} ms<extra></extra>',
      }));
      Plotly.react('chart-impl', traces, {
        ...BASE_LAYOUT,
        barmode: 'group',
        xaxis: { ...BASE_LAYOUT.xaxis, title: '', tickangle: -45, tickfont: { size: 9, color: '#a1a1aa' } },
        yaxis: { ...BASE_LAYOUT.yaxis, type: 'log', title: 'Iter time (ms, log)' },
        margin: { l: 60, r: 20, t: 20, b: 140 },
      }, BASE_CONFIG);
    },

    renderHeatmaps() {
      ['mpi_2d', 'hybrid_1d', 'hybrid_2d'].forEach(impl => this.renderHeatmap(impl));
    },
    renderHeatmap(impl) {
      const rows = this.filtered.filter(r => r.impl === impl);
      if (rows.length === 0) {
        Plotly.react(`chart-heat-${impl}`, [], { ...BASE_LAYOUT, annotations: [{ text: 'No data (check filters)', showarrow: false, font: { color: '#71717a' } }] }, BASE_CONFIG);
        return;
      }
      // Rows: grid sizes (sorted desc so big on top)
      // Cols: core counts (sorted asc)
      // Values: mean of selected metric across (comm, io)
      const grids = [...new Set(rows.map(r => r.grid))].sort((a,b) => b-a);
      const cores = [...new Set(rows.map(r => r.cores))].sort((a,b) => a-b);
      const key = this.heatmapTab;
      const z = grids.map(g => cores.map(c => {
        const matches = rows.filter(r => r.grid === g && r.cores === c && r[key] != null);
        if (matches.length === 0) return null;
        return matches.reduce((a, b) => a + b[key], 0) / matches.length;
      }));
      const text = z.map(row => row.map(v => v == null ? '' : (key === 'iter_time_ms' ? v.toFixed(3) : v.toFixed(1))));

      let colorscale, zmin, zmax;
      if (key === 'efficiency') {
        colorscale = [[0, '#1e1b4b'], [0.25, '#312e81'], [0.5, '#f87171'], [0.6, '#fbbf24'], [0.75, '#34d399'], [1.0, '#a78bfa']];
        zmin = 0; zmax = 400;
      } else if (key === 'speedup') {
        colorscale = [[0, '#0b1220'], [0.3, '#60a5fa'], [0.6, '#a78bfa'], [1.0, '#34d399']];
        const maxZ = Math.max(...z.flat().filter(v => v != null));
        zmin = 0; zmax = maxZ;
      } else {
        colorscale = [[0, '#34d399'], [0.5, '#fbbf24'], [1, '#f87171']];
        const vals = z.flat().filter(v => v != null);
        zmin = Math.min(...vals); zmax = Math.max(...vals);
      }
      Plotly.react(`chart-heat-${impl}`, [{
        z, x: cores.map(c => c + 'c'), y: grids.map(g => g + '²'),
        text, texttemplate: '%{text}',
        textfont: { size: 10, color: '#0a0a0f' },
        type: 'heatmap', colorscale, zmin, zmax,
        hovertemplate: '<b>%{y}</b> @ %{x}<br>' + key + ': %{z:.2f}<extra></extra>',
        colorbar: { tickfont: { color: '#a1a1aa', size: 10 }, thickness: 8, len: 0.8 },
      }], {
        ...BASE_LAYOUT,
        margin: { l: 55, r: 40, t: 10, b: 40 },
        xaxis: { ...BASE_LAYOUT.xaxis, title: '' },
        yaxis: { ...BASE_LAYOUT.yaxis, title: '' },
      }, BASE_CONFIG);
    },

    renderHybridGap() {
      const h1 = {};
      const h2 = {};
      for (const r of this.filtered) {
        if (r.cores === 1) continue;
        const k = `${r.grid}|${r.cores}|${r.comm}|${r.io}`;
        if (r.impl === 'hybrid_1d') h1[k] = r;
        if (r.impl === 'hybrid_2d') h2[k] = r;
      }
      const matched = Object.keys(h2).filter(k => h1[k]).map(k => ({
        a: h1[k], b: h2[k], ratio: h2[k].iter_time_ms / h1[k].iter_time_ms,
      })).sort((x, y) => x.a.cores - y.a.cores || x.a.grid - y.a.grid);

      if (matched.length === 0) {
        Plotly.react('chart-hybridgap', [], { ...BASE_LAYOUT }, BASE_CONFIG);
        return;
      }
      const traces = [];
      const byGrid = {};
      for (const m of matched) {
        if (!byGrid[m.a.grid]) byGrid[m.a.grid] = [];
        byGrid[m.a.grid].push(m);
      }
      Object.keys(byGrid).sort((a,b)=>a-b).forEach(g => {
        const items = byGrid[g].sort((x,y) => x.a.cores - y.a.cores);
        traces.push({
          x: items.map(m => `${m.a.cores}c · ${m.a.comm.toUpperCase()}${m.a.io !== 'none' ? '·'+m.a.io : ''}`),
          y: items.map(m => m.ratio),
          type: 'bar',
          name: `${g}²`,
          marker: { color: GRID_COLORS[g] },
          hovertemplate: '<b>' + g + '² · %{x}</b><br>Hybrid 2D / Hybrid 1D: %{y:.2f}×<extra></extra>',
        });
      });
      traces.push({
        x: matched.map(m => `${m.a.cores}c · ${m.a.comm.toUpperCase()}${m.a.io !== 'none' ? '·'+m.a.io : ''}`),
        y: matched.map(() => 1),
        type: 'scatter', mode: 'lines', name: 'Parity (1.0×)',
        line: { color: 'rgba(255,255,255,0.35)', dash: 'dash', width: 1 },
        hoverinfo: 'skip',
      });
      Plotly.react('chart-hybridgap', traces, {
        ...BASE_LAYOUT,
        barmode: 'group',
        xaxis: { ...BASE_LAYOUT.xaxis, title: '', tickangle: -45, tickfont: { size: 9, color: '#a1a1aa' } },
        yaxis: { ...BASE_LAYOUT.yaxis, title: 'H2D / H1D (×)' },
        margin: { l: 55, r: 20, t: 20, b: 120 },
      }, BASE_CONFIG);
    },

    renderBestConfig() {
      const bestByGrid = {};
      for (const r of this.filtered) {
        if (r.io !== 'none') continue;
        if (r.cores === 1) continue;
        const cur = bestByGrid[r.grid];
        if (!cur || r.iter_time_ms < cur.iter_time_ms) bestByGrid[r.grid] = r;
      }
      const wrap = document.getElementById('best-config-wrap');
      if (!wrap) return;
      const entries = Object.keys(bestByGrid).sort((a,b) => (+a) - (+b));
      if (entries.length === 0) {
        wrap.innerHTML = '<div class="text-xs text-zinc-500">No no-IO runs in current filter.</div>';
        return;
      }
      wrap.innerHTML = entries.map(g => {
        const r = bestByGrid[g];
        const eff = r.efficiency != null ? r.efficiency.toFixed(0) + '%' : '—';
        const spd = r.speedup != null ? r.speedup.toFixed(0) + '×' : '—';
        return `
          <div class="flex items-center justify-between rounded-lg px-4 py-3 border border-white/5 hover:border-white/10 bg-white/[0.015]">
            <div class="flex items-center gap-3">
              <div class="text-lg font-bold mono" style="color:${GRID_COLORS[g]}">${g}²</div>
              <div>
                <div class="text-sm font-semibold">${IMPL_LABEL[r.impl]} · ${r.comm.toUpperCase()}</div>
                <div class="text-xs text-zinc-500 mono">${r.mpi}×${r.omp} · ${r.decomp_x}×${r.decomp_y} decomp · ${r.cores} cores</div>
              </div>
            </div>
            <div class="text-right">
              <div class="mono text-sm font-semibold" style="color:#34d399">${r.iter_time_ms.toFixed(3)} ms</div>
              <div class="text-xs text-zinc-500 mono">${spd} · ${eff} · ${r.throughput_mcells.toFixed(0)} M/s</div>
            </div>
          </div>`;
      }).join('');
    },
  };
}

  // ===== FULLSCREEN =====
  window._chartCache = {};
  const _origReact = Plotly.react.bind(Plotly);
  Plotly.react = function(id, traces, layout, config) {
    window._chartCache[typeof id === 'string' ? id : id.id] = { traces, layout: {...layout}, config };
    return _origReact(id, traces, layout, config);
  };

  function _addExpandButtons() {
    document.querySelectorAll('[id^="chart-"]').forEach(el => {
      if (el.id === 'chart-fullscreen-inner') return;
      if (el.parentElement && el.parentElement.classList.contains('chart-wrap')) return;
      const wrap = document.createElement('div');
      wrap.className = 'chart-wrap';
      el.parentNode.insertBefore(wrap, el);
      wrap.appendChild(el);
      const btn = document.createElement('button');
      btn.className = 'chart-expand-btn';
      btn.title = 'Fullscreen (Esc to close)';
      btn.innerHTML = '⛶';
      const titleEl = wrap.closest('section, .glass')?.querySelector('.section-title');
      const title = titleEl ? titleEl.textContent.trim() : el.id;
      btn.onclick = () => openFullscreen(el.id, title);
      wrap.appendChild(btn);
    });
  }

  window.openFullscreen = function(chartId, title) {
    const data = window._chartCache[chartId];
    if (!data) return;
    const modal = document.getElementById('chart-fullscreen-modal');
    document.getElementById('chart-fullscreen-title').textContent = title || chartId;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    const inner = document.getElementById('chart-fullscreen-inner');
    const h = window.innerHeight - 70;
    Plotly.newPlot(inner, data.traces, {
      ...data.layout,
      height: h,
      autosize: true,
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
    }, { ...(data.config || {}), responsive: true, displayModeBar: true });
  };

  window.closeFullscreen = function() {
    document.getElementById('chart-fullscreen-modal').classList.remove('open');
    document.body.style.overflow = '';
    Plotly.purge(document.getElementById('chart-fullscreen-inner'));
  };

  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFullscreen(); });

  // Add buttons after Alpine init renders charts (slight delay)
  document.addEventListener('alpine:initialized', () => setTimeout(_addExpandButtons, 800));
</script>

<!-- Fullscreen modal -->
<div id="chart-fullscreen-modal">
  <div id="chart-fullscreen-header">
    <div id="chart-fullscreen-title" class="section-title"></div>
    <button id="chart-fullscreen-close" onclick="closeFullscreen()">✕ close</button>
  </div>
  <div id="chart-fullscreen-inner"></div>
</div>

</body>
</html>
"""


def load_dataset(src_dir: Path) -> dict:
    runs: list[dict] = []
    for impl, spec in CONFIGS.items():
        runs.extend(parse_csv(impl, spec, src_dir))
    runs = compute_derived(runs)
    return {"runs": runs, "stats": summary(runs)}


def main() -> None:
    datasets = {}
    for key, meta in DATASETS_META.items():
        datasets[key] = {
            **load_dataset(meta["dir"]),
            "label": meta["label"],
        }

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "datasets": datasets,
    }

    total_runs = sum(len(d["runs"]) for d in datasets.values())
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(payload, separators=(",", ":")))
    out = SCRIPTS_DIR / "dashboard.html"
    out.write_text(html)
    print(f"✓ wrote {out.relative_to(SCRIPTS_DIR.parent)}  ({total_runs} runs across {len(datasets)} datasets, {out.stat().st_size // 1024} KB)")
    print(f"  open with: open {out}")


if __name__ == "__main__":
    main()

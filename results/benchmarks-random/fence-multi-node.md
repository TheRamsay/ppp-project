[ramsay@login2.barbora ppp-project]$   salloc -A ATR-25-7 -N 2 --ntasks-per-node=32 -p qcpu_exp -t 00:15:00
salloc: Pending job allocation 1963252
salloc: job 1963252 queued and waiting for resources
^[salloc: job 1963252 has been allocated resources
salloc: Granted job allocation 1963252
salloc: Waiting for resource configuration
salloc: Nodes cn[83-84] are ready for job
[ramsay@cn83.barbora ppp-project]$  cd ~/ppp-project/scripts
[ramsay@cn83.barbora scripts]$   source load_modules.sh

[ramsay@cn83.barbora scripts]$
[ramsay@cn83.barbora scripts]$   mpirun -n 16 ./halo_bench 1024 1024

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 1024x1024  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        3.4406 s   3440.5846 us   73.9023x
  B) strided Put   + fence        0.0466 s     46.5559 us    1.0000x
  C) packed  Put   + fence        0.0233 s     23.3064 us    0.5006x
  D) strided Put   + PSCW         0.0440 s     44.0135 us    0.9454x
  E) packed  Put   + PSCW         0.0203 s     20.3118 us    0.4363x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           73.9023x
    B/C  (strided→packed gain):        1.9976x
    B/D  (fence→PSCW on strided):      1.0578x
    C/E  (fence→PSCW on packed):       1.1474x
    B/E  (best combo vs strided+fence):2.2921x
    A/E  (A naive  vs E fully tuned):  169.3882x

[ramsay@cn83.barbora scripts]$   mpirun -n 32 ./halo_bench 1024 1024

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 32  Topology: 8x4  Tile: 1024x1024  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        3.3403 s   3340.2516 us   60.0978x
  B) strided Put   + fence        0.0556 s     55.5803 us    1.0000x
  C) packed  Put   + fence        0.0291 s     29.1307 us    0.5241x
  D) strided Put   + PSCW         0.0512 s     51.1696 us    0.9206x
  E) packed  Put   + PSCW         0.0247 s     24.6747 us    0.4439x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           60.0978x
    B/C  (strided→packed gain):        1.9080x
    B/D  (fence→PSCW on strided):      1.0862x
    C/E  (fence→PSCW on packed):       1.1806x
    B/E  (best combo vs strided+fence):2.2525x
    A/E  (A naive  vs E fully tuned):  135.3716x

[ramsay@cn83.barbora scripts]$   mpirun -n 64 ./halo_bench 1024 1024

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 64  Topology: 8x8  Tile: 1024x1024  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        3.6833 s   3683.3213 us   35.0645x
  B) strided Put   + fence        0.1050 s    105.0441 us    1.0000x
  C) packed  Put   + fence        0.0483 s     48.3211 us    0.4600x
  D) strided Put   + PSCW         0.0676 s     67.5571 us    0.6431x
  E) packed  Put   + PSCW         0.0277 s     27.7123 us    0.2638x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           35.0645x
    B/C  (strided→packed gain):        2.1739x
    B/D  (fence→PSCW on strided):      1.5549x
    C/E  (fence→PSCW on packed):       1.7437x
    B/E  (best combo vs strided+fence):

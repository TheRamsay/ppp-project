[ramsay@cn5.barbora scripts]$   salloc -A ATR-25-7 -N 1 --ntasks-per-node=32 -p qcpu_exp -t 00:20:00
salloc: Granted job allocation 1963239
salloc: Waiting for resource configuration
salloc: Nodes cn82 are ready for job
[ramsay@cn82.barbora scripts]$   cd ~/ppp-project/scripts
[ramsay@cn82.barbora scripts]$   mpiicpc -O3 -xCORE-AVX512 -std=c++17 halo_modes_benchmark.cpp -o halo_bench
[ramsay@cn82.barbora scripts]$   mpirun -n 16 ./halo_bench 1024 1024


========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 1024x1024  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        3.3665 s   3366.4770 us   70.4880x
  B) strided Put   + fence        0.0478 s     47.7596 us    1.0000x
  C) packed  Put   + fence        0.0231 s     23.0711 us    0.4831x
  D) strided Put   + PSCW         0.0443 s     44.3498 us    0.9286x
  E) packed  Put   + PSCW         0.0206 s     20.5865 us    0.4310x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           70.4880x
    B/C  (strided→packed gain):        2.0701x
    B/D  (fence→PSCW on strided):      1.0769x
    C/E  (fence→PSCW on packed):       1.1207x
    B/E  (best combo vs strided+fence):2.3199x
    A/E  (A naive  vs E fully tuned):  163.5283x

[ramsay@cn82.barbora scripts]$   mpirun -n 16 ./halo_bench 256 4096 16 1

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 16x1  Tile: 256x4096  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence       10.3470 s  10347.0256 us   79.6909x
  B) strided Put   + fence        0.1298 s    129.8394 us    1.0000x
  C) packed  Put   + fence        0.0426 s     42.6119 us    0.3282x
  D) strided Put   + PSCW         0.1219 s    121.9238 us    0.9390x
  E) packed  Put   + PSCW         0.0386 s     38.5657 us    0.2970x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           79.6909x
    B/C  (strided→packed gain):        3.0470x
    B/D  (fence→PSCW on strided):      1.0649x
    C/E  (fence→PSCW on packed):       1.1049x
    B/E  (best combo vs strided+fence):3.3667x
    A/E  (A naive  vs E fully tuned):  268.2958x

[ramsay@cn82.barbora scripts]$   mpirun -n 16 ./halo_bench 4096 256 1 16

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 1x16  Tile: 4096x256  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        0.0341 s     34.1277 us    1.8670x
  B) strided Put   + fence        0.0183 s     18.2794 us    1.0000x
  C) packed  Put   + fence        0.0309 s     30.8538 us    1.6879x
  D) strided Put   + PSCW         0.0170 s     16.9682 us    0.9283x
  E) packed  Put   + PSCW         0.0258 s     25.8164 us    1.4123x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           1.8670x
    B/C  (strided→packed gain):        0.5925x
    B/D  (fence→PSCW on strided):      1.0773x
    C/E  (fence→PSCW on packed):       1.1951x
    B/E  (best combo vs strided+fence):0.7081x
    A/E  (A naive  vs E fully tuned):  1.3219x

[ramsay@cn82.barbora scripts]$   mpirun -n 16 ./halo_bench 2048 2048

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 2048x2048  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                                   Total       Per-iter      vs B
  ----------------------------------------------------------------------
  A) strided Get   + fence        5.5847 s   5584.7409 us   54.8136x
  B) strided Put   + fence        0.1019 s    101.8860 us    1.0000x
  C) packed  Put   + fence        0.0688 s     68.8373 us    0.6756x
  D) strided Put   + PSCW         0.0985 s     98.5211 us    0.9670x
  E) packed  Put   + PSCW         0.0627 s     62.6630 us    0.6150x

  Factor analysis:
    A/B  (MPI_Get→Put gain):           54.8136x
    B/C  (strided→packed gain):        1.4801x
    B/D  (fence→PSCW on strided):      1.0342x
    C/E  (fence→PSCW on packed):       1.0985x
    B/E  (best combo vs strided+fence):1.6259x
    A/E  (A naive  vs E fully tuned):  89.1234x


[ramsay@cn5.barbora ppp-project]$ cd ~/ppp-project/scripts
[ramsay@cn5.barbora scripts]$   source load_modules.sh
[ramsay@cn5.barbora scripts]$   mpiicpc -O3 -xCORE-AVX512 -std=c++17 halo_modes_benchmark.cpp -o halo_bench
[ramsay@cn5.barbora scripts]$   mpirun -n 16 ./halo_bench 1024 1024

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 1024x1024  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                               Total       Per-iter      vs B
  ------------------------------------------------------------------
  A) strided MPI_Get              3.4097 s   3409.7240 us   71.9865x
  B) strided MPI_Put              0.0474 s     47.3661 us    1.0000x
  C) packed  MPI_Put              0.0232 s     23.1762 us    0.4893x

  Factor analysis:
    A/B  (MPI_Get→Put gain):       71.9865x
    B/C  (strided→packed gain):    2.0437x
    A/C  (total A-vs-C gain):      147.1215x

[ramsay@cn5.barbora scripts]$   mpirun -n 16 ./halo_bench 256 4096 16 1

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 16x1  Tile: 256x4096  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                               Total       Per-iter      vs B
  ------------------------------------------------------------------
  A) strided MPI_Get              9.9721 s   9972.0603 us   75.2238x
  B) strided MPI_Put              0.1326 s    132.5652 us    1.0000x
  C) packed  MPI_Put              0.0435 s     43.4997 us    0.3281x

  Factor analysis:
    A/B  (MPI_Get→Put gain):       75.2238x
    B/C  (strided→packed gain):    3.0475x
    A/C  (total A-vs-C gain):      229.2443x

[ramsay@cn5.barbora scripts]$   mpirun -n 16 ./halo_bench 4096 256 1 16

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 1x16  Tile: 4096x256  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                               Total       Per-iter      vs B
  ------------------------------------------------------------------
  A) strided MPI_Get              0.0346 s     34.5763 us    1.8505x
  B) strided MPI_Put              0.0187 s     18.6852 us    1.0000x
  C) packed  MPI_Put              0.0318 s     31.7923 us    1.7015x

  Factor analysis:
    A/B  (MPI_Get→Put gain):       1.8505x
    B/C  (strided→packed gain):    0.5877x
    A/C  (total A-vs-C gain):      1.0876x

[ramsay@cn5.barbora scripts]$   mpirun -n 16 ./halo_bench 512 512

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 512x512  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                               Total       Per-iter      vs B
  ------------------------------------------------------------------
  A) strided MPI_Get              0.0311 s     31.0897 us    1.2157x
  B) strided MPI_Put              0.0256 s     25.5740 us    1.0000x
  C) packed  MPI_Put              0.0161 s     16.1343 us    0.6309x

  Factor analysis:
    A/B  (MPI_Get→Put gain):       1.2157x
    B/C  (strided→packed gain):    1.5851x
    A/C  (total A-vs-C gain):      1.9269x

[ramsay@cn5.barbora scripts]$   mpirun -n 16 ./halo_bench 2048 2048

========== HALO EXCHANGE BENCHMARK ==========
Ranks: 16  Topology: 4x4  Tile: 2048x2048  Halo: 2
Iterations: 1000  (warmup 50)

  Mode                               Total       Per-iter      vs B
  ------------------------------------------------------------------
  A) strided MPI_Get              5.5920 s   5591.9754 us   56.0600x
  B) strided MPI_Put              0.0997 s     99.7499 us    1.0000x
  C) packed  MPI_Put              0.0603 s     60.2531 us    0.6040x

  Factor analysis:
    A/B  (MPI_Get→Put gain):       56.0600x
    B/C  (strided→packed gain):    1.6555x
    A/C  (total A-vs-C gain):      92.8081x

[ramsay@cn5.barbora scripts]$

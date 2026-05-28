#!/usr/bin/env bash
#SBATCH --account=YOUR_PROJECT_ACCOUNT
#SBATCH --job-name=PPP_PROJ01_WEAK
#SBATCH -p qcpu
#SBATCH -t 01:00:00
#SBATCH -N 8
#SBATCH --ntasks-per-node=2
#SBATCH --cpus-per-task=16

# Weak scaling benchmark: constant 65,536 cells per MPI rank.
# Points (rank × grid):  1×256², 4×512², 16×1024², 64×2048², 256×4096².
# Covers all 3 decompositions where core budget allows (max 8 nodes = 256 cores).

source load_modules.sh

declare PROJ_ID="${PPP_PROJECT_ID:-YOUR_PROJECT_ID}"

STDOUT_FILE="run_weak_scaling_out.csv"
STDERR_FILE="run_weak_scaling_err.txt"
BINARY_PATH="../build/ppp_proj01"

rm -f $STDOUT_FILE $STDERR_FILE

USER_SCRATCH_PATH=/scratch/project/$PROJ_ID/$USER
mkdir -p $USER_SCRATCH_PATH
OUT_FILE_PATH=$USER_SCRATCH_PATH/$SLURM_JOBID
mkdir -p $OUT_FILE_PATH
lfs setstripe -S 1M -c 16 $OUT_FILE_PATH

DISK_WRITE_INTENSITY=50

# -----------------------------------------------------------------------------
# Weak-scaling points: (ranks, grid)  →  ranks * grid² = 65,536 cells/rank
# -----------------------------------------------------------------------------
declare -a POINTS=(
    "1 256"
    "4 512"
    "16 1024"
    "64 2048"
    "256 4096"
)

FIRST_RUN=1   # flag: emit CSV header on very first run

# Runs a single (impl × point × comm_mode) configuration.
# Args: impl ranks grid omp tpn dist comm_mode use2d
run_config() {
    local impl=$1 ranks=$2 grid=$3 omp=$4 tpn=$5 dist=$6 comm_mode=$7 use2d=$8
    local cores=$((ranks * omp))
    local nnodes=$(( (cores + 31) / 32 ))     # 32 cores/node on Barbora
    [[ $nnodes -lt 1 ]] && nnodes=1
    [[ $cores -gt 256 ]] && return 0          # skip: >8 nodes unavailable

    local n_iters
    if [[ $ranks -eq 1 ]]; then
        n_iters=$((2000000 / grid))
    else
        n_iters=$((20000000 / grid))
    fi

    # Header emitted on very first run only
    local B="-b"
    if [[ $FIRST_RUN -eq 1 ]]; then
        B="-B"
        FIRST_RUN=0
    fi

    local GRID_FLAG=""
    [[ $use2d -eq 1 ]] && GRID_FLAG="-g"

    export OMP_NUM_THREADS=$omp
    local INPUT=input_data_${grid}.h5

    # Single-rank runs use simulation_mode=0 (serial fallback, no halo exchange)
    local mode=$comm_mode
    [[ $ranks -eq 1 ]] && mode=0

    echo "[weak] impl=$impl ranks=$ranks grid=$grid cores=$cores nodes=$nnodes omp=$omp mode=$mode 2d=$use2d" >> $STDERR_FILE

    if [[ $omp -eq 1 ]]; then
        stdbuf -oL srun --distribution=$dist --ntasks-per-node=$tpn --cpus-per-task=$omp \
             -N $nnodes -n $ranks \
             $BINARY_PATH $B $GRID_FLAG -n $n_iters -m $mode -w $DISK_WRITE_INTENSITY \
             -i $INPUT >> $STDOUT_FILE 2>> $STDERR_FILE
    else
        stdbuf -oL srun --distribution=$dist --ntasks-per-node=$tpn --cpus-per-task=$omp \
             -N $nnodes -n $ranks \
             $BINARY_PATH $B $GRID_FLAG -n $n_iters -m $mode -w $DISK_WRITE_INTENSITY \
             -i $INPUT -t $omp >> $STDOUT_FILE 2>> $STDERR_FILE
    fi
}

# -----------------------------------------------------------------------------
# Main loop: weak-scaling points × 3 decompositions × 2 comm modes (P2P/RMA)
# no-IO only (IO behavior is captured in the strong-scaling benchmarks)
# -----------------------------------------------------------------------------
for point in "${POINTS[@]}"; do
    read -r ranks grid <<< "$point"

    # MPI 2D: 32 ranks/node, 1 OMP thread each, block:block:block, 2D decomp
    run_config mpi_2d    $ranks $grid 1  32 "block:block:block,Pack"     1 1
    run_config mpi_2d    $ranks $grid 1  32 "block:block:block,Pack"     2 1

    # Hybrid 1D: 2 ranks/node, 16 OMP threads each, block:cyclic:cyclic, 1D decomp
    run_config hybrid_1d $ranks $grid 16 2  "block:cyclic:cyclic,NoPack" 1 0
    run_config hybrid_1d $ranks $grid 16 2  "block:cyclic:cyclic,NoPack" 2 0

    # Hybrid 2D: 4 ranks/node, 8 OMP threads each, block:cyclic:cyclic, 2D decomp
    run_config hybrid_2d $ranks $grid 8  4  "block:cyclic:cyclic,NoPack" 1 1
    run_config hybrid_2d $ranks $grid 8  4  "block:cyclic:cyclic,NoPack" 2 1
done

sync
rm -rf $OUT_FILE_PATH

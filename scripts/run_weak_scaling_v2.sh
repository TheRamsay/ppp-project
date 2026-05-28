#!/usr/bin/env bash
#SBATCH --account=ATR-25-7
#SBATCH --job-name=PPP_PROJ01_WEAK_V2
#SBATCH -p qcpu
#SBATCH -t 00:30:00
#SBATCH -N 8
#SBATCH --ntasks-per-node=32

# Weak scaling v2: constant 65,536 cells per CORE (not per MPI rank).
# Per-decomposition rank/thread layout matches the strong-scaling scripts
# (run_full_*.sh), so weak and strong runs are directly comparable.
#
# Points (total cores × grid):  1×256², 4×512², 16×1024², 64×2048², 256×4096².
# All three decompositions are evaluated where total_cores fits the
# decomposition's natural OMP factor.

source load_modules.sh

declare PROJ_ID="atr-25-7"
STDOUT_FILE="run_weak_scaling_v2_out.csv"
STDERR_FILE="run_weak_scaling_v2_err.txt"
BINARY_PATH="../build/ppp_proj01"

rm -f $STDOUT_FILE $STDERR_FILE

USER_SCRATCH_PATH=/scratch/project/$PROJ_ID/$USER
mkdir -p $USER_SCRATCH_PATH
OUT_FILE_PATH=$USER_SCRATCH_PATH/$SLURM_JOBID
mkdir -p $OUT_FILE_PATH
lfs setstripe -S 1M -c 16 $OUT_FILE_PATH

DISK_WRITE_INTENSITY=50

# (total_cores, grid_size) — each row keeps cells/core = 65,536
declare -a POINTS=(
    "1 256"
    "4 512"
    "16 1024"
    "64 2048"
    "256 4096"
)

FIRST_RUN=1

# run_config impl ranks grid omp tpn dist comm_mode use2d
run_config() {
    local impl=$1 ranks=$2 grid=$3 omp=$4 tpn=$5 dist=$6 comm_mode=$7 use2d=$8
    local cores=$((ranks * omp))
    local nnodes=$(( (cores + 31) / 32 ))
    [[ $nnodes -lt 1 ]] && nnodes=1
    [[ $cores -gt 256 ]] && return 0

    local n_iters
    if [[ $ranks -eq 1 ]]; then
        n_iters=$((2000000 / grid))
    else
        n_iters=$((20000000 / grid))
    fi

    local B="-b"
    if [[ $FIRST_RUN -eq 1 ]]; then
        B="-B"
        FIRST_RUN=0
    fi

    local GRID_FLAG=""
    [[ $use2d -eq 1 ]] && GRID_FLAG="-g"

    export OMP_NUM_THREADS=$omp
    local INPUT=input_data_${grid}.h5

    local mode=$comm_mode
    [[ $ranks -eq 1 ]] && mode=0

    echo "[weak-v2] impl=$impl ranks=$ranks grid=$grid cores=$cores nodes=$nnodes omp=$omp mode=$mode 2d=$use2d" >> $STDERR_FILE

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

for point in "${POINTS[@]}"; do
    read -r total_cores grid <<< "$point"

    # ---- MPI 2D: ranks = total_cores, OMP = 1 ----
    # Up to 256 cores fits in 8 nodes at 32 ranks/node.
    if [[ $total_cores -le 256 ]]; then
        run_config mpi_2d $total_cores $grid 1 32 "block:block:block,Pack" 1 1
        if [[ $total_cores -gt 1 ]]; then
            run_config mpi_2d $total_cores $grid 1 32 "block:block:block,Pack" 2 1
        fi
    fi

    # ---- Hybrid 1D: ranks = cores/16, OMP = 16 (matches run_full_hybrid_1d.sh layout) ----
    if [[ $total_cores -ge 16 ]]; then
        h1d_ranks=$((total_cores / 16))
        run_config hybrid_1d $h1d_ranks $grid 16 2 "block:cyclic:cyclic,NoPack" 1 0
        run_config hybrid_1d $h1d_ranks $grid 16 2 "block:cyclic:cyclic,NoPack" 2 0
    elif [[ $total_cores -eq 1 ]]; then
        # seq baseline so the line has a starting point comparable to MPI 2D
        run_config hybrid_1d 1 $grid 1 1 "block:cyclic:cyclic,NoPack" 0 0
    fi

    # ---- Hybrid 2D: ranks = cores/8, OMP = 8 (matches run_full_hybrid_2d.sh layout) ----
    if [[ $total_cores -ge 8 ]]; then
        h2d_ranks=$((total_cores / 8))
        run_config hybrid_2d $h2d_ranks $grid 8 4 "block:cyclic:cyclic,NoPack" 1 1
        run_config hybrid_2d $h2d_ranks $grid 8 4 "block:cyclic:cyclic,NoPack" 2 1
    elif [[ $total_cores -eq 1 ]]; then
        run_config hybrid_2d 1 $grid 1 1 "block:cyclic:cyclic,NoPack" 0 1
    fi
done

sync
rm -rf $OUT_FILE_PATH

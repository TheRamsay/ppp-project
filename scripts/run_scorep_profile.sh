#!/usr/bin/env bash
#SBATCH --account=ATR-25-7
#SBATCH --job-name=PPP_PROJ01_PROF
#SBATCH -p qcpu
#SBATCH -t 01:30:00
#SBATCH -N 4
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=8
#SBATCH --distribution=block:cyclic:cyclic,NoPack

source load_modules.sh

BINARY_PATH="${1:-../build_prof/ppp_proj01}"
LABEL="${2:-unlabeled}"

SCRATCH_DIR="/scratch/project/atr-25-7/ramsay/scorep_backup/${LABEL}"
mkdir -p "$SCRATCH_DIR"

STDOUT_FILE="run_scorep_profile_out_${LABEL}.csv"
STDERR_FILE="run_scorep_profile_err_${LABEL}.txt"
FILTER="$(pwd)/ppp_scorep_filter.flt"

rm -f "$STDOUT_FILE" "$STDERR_FILE"

DISK_WRITE_INTENSITY=50
export OMP_NUM_THREADS=8
export SCOREP_TOTAL_MEMORY=2G

# Helper: sets unique Score-P dir, then runs srun
scorep_run() {
    local run_label=$1; shift
    export SCOREP_EXPERIMENT_DIRECTORY="${SCRATCH_DIR}/${run_label}"
    stdbuf -oL srun "$@" >> "$STDOUT_FILE" 2>> "$STDERR_FILE"
}

# --- P2P (teacher's required runs: 1D + 2D decomposition) ---
export SCOREP_ENABLE_PROFILING=true
export SCOREP_ENABLE_TRACING=false
unset SCOREP_FILTERING_FILE
scorep_run p2p_1d_profile "$BINARY_PATH" -B    -n 100 -t $OMP_NUM_THREADS -m 1 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5
scorep_run p2p_2d_profile "$BINARY_PATH" -b -g -n 100 -t $OMP_NUM_THREADS -m 1 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5

export SCOREP_ENABLE_TRACING=true
export SCOREP_FILTERING_FILE="$FILTER"
scorep_run p2p_1d_trace   "$BINARY_PATH" -B    -n 100 -t $OMP_NUM_THREADS -m 1 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5
scorep_run p2p_2d_trace   "$BINARY_PATH" -b -g -n 100 -t $OMP_NUM_THREADS -m 1 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5

# --- RMA (our extra: fence vs PSCW comparison) ---
export SCOREP_ENABLE_TRACING=false
unset SCOREP_FILTERING_FILE
scorep_run rma_1d_profile "$BINARY_PATH" -B    -n 100 -t $OMP_NUM_THREADS -m 2 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5
scorep_run rma_2d_profile "$BINARY_PATH" -b -g -n 100 -t $OMP_NUM_THREADS -m 2 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5

export SCOREP_ENABLE_TRACING=true
export SCOREP_FILTERING_FILE="$FILTER"
scorep_run rma_1d_trace   "$BINARY_PATH" -B    -n 100 -t $OMP_NUM_THREADS -m 2 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5
scorep_run rma_2d_trace   "$BINARY_PATH" -b -g -n 100 -t $OMP_NUM_THREADS -m 2 -w $DISK_WRITE_INTENSITY -i input_data_1024.h5

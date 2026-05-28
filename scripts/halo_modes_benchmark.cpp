// Isolated MPI halo exchange benchmark.
// Compares:
//   A) strided MPI_Get + derived type (user's pre-fix implementation)
//   B) strided MPI_Put + derived type (Harima / reference implementation)
//   C) packed MPI_Put (user's current implementation)
//
// Goal: attribute the ~490× speedup between A→C to either (Get→Put) or (strided→packed).
//
// Usage:
//   mpirun -n <P> ./halo_bench <tile_w> <tile_h> [<dims_x> <dims_y>]
// Example:
//   mpirun -n 16 ./halo_bench 1024 1024       # MPI_Dims_create picks 4×4 (2D square)
//   mpirun -n 16 ./halo_bench 2048 128 16 1   # 16×1 wide 1D (strided heavy)
//   mpirun -n 16 ./halo_bench 128 2048 1 16   # 1×16 tall 1D (contiguous halos)

#include <mpi.h>

#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <vector>

constexpr int HALO = 2;
constexpr int ITERS = 1000;
constexpr int WARMUP = 50;

// ============================================================================
//  Mode A: strided MPI_Get with derived datatypes
// ============================================================================
double mode_strided_get(float *local, int tile_w, int tile_h, int local_w, MPI_Win win,
                        int L, int R, int U, int D, MPI_Datatype vert, MPI_Datatype horiz,
                        int iters) {
    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = MPI_Wtime();
    for (int i = 0; i < iters; i++) {
        MPI_Win_fence(0, win);
        if (L != MPI_PROC_NULL) {
            MPI_Get(local + HALO * local_w,              // my left halo
                    1, vert, L, HALO * local_w + tile_w, // L's rightmost interior
                    1, vert, win);
        }
        if (R != MPI_PROC_NULL) {
            MPI_Get(local + HALO * local_w + tile_w + HALO, // my right halo
                    1, vert, R, HALO * local_w + HALO,      // R's leftmost interior
                    1, vert, win);
        }
        if (U != MPI_PROC_NULL) {
            MPI_Get(local + HALO,                          // my top halo
                    1, horiz, U, tile_h * local_w + HALO,  // U's bottommost interior
                    1, horiz, win);
        }
        if (D != MPI_PROC_NULL) {
            MPI_Get(local + (tile_h + HALO) * local_w + HALO, // my bottom halo
                    1, horiz, D, HALO * local_w + HALO,       // D's topmost interior
                    1, horiz, win);
        }
    }
    MPI_Win_fence(0, win);
    double elapsed = MPI_Wtime() - t0;
    MPI_Barrier(MPI_COMM_WORLD);
    return elapsed;
}

// ============================================================================
//  Mode B: strided MPI_Put with derived datatypes
// ============================================================================
double mode_strided_put(float *local, int tile_w, int tile_h, int local_w, MPI_Win win,
                        int L, int R, int U, int D, MPI_Datatype vert, MPI_Datatype horiz,
                        int iters) {
    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = MPI_Wtime();
    for (int i = 0; i < iters; i++) {
        MPI_Win_fence(0, win);
        if (L != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + HALO,           // my left interior
                    1, vert, L,
                    HALO * local_w + tile_w + HALO,          // L's right halo
                    1, vert, win);
        }
        if (R != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + tile_w,         // my right interior
                    1, vert, R,
                    HALO * local_w,                          // R's left halo
                    1, vert, win);
        }
        if (U != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + HALO,           // my top interior
                    1, horiz, U,
                    (tile_h + HALO) * local_w + HALO,        // U's bottom halo
                    1, horiz, win);
        }
        if (D != MPI_PROC_NULL) {
            MPI_Put(local + tile_h * local_w + HALO,         // my bottom interior
                    1, horiz, D,
                    HALO,                                    // D's top halo
                    1, horiz, win);
        }
    }
    MPI_Win_fence(0, win);
    double elapsed = MPI_Wtime() - t0;
    MPI_Barrier(MPI_COMM_WORLD);
    return elapsed;
}

// ============================================================================
//  Mode C: packed MPI_Put (manual pack, contiguous put, manual unpack)
// ============================================================================
double mode_packed_put(float *local, float *pack_send, float *pack_recv, int tile_w, int tile_h,
                       int local_w, MPI_Win pack_win, int L, int R, int U, int D, int iters) {
    const int vert_size = tile_h * HALO;
    const int horiz_size = tile_w * HALO;
    const int off_left = 0;
    const int off_right = vert_size;
    const int off_top = 2 * vert_size;
    const int off_bottom = 2 * vert_size + horiz_size;

    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = MPI_Wtime();
    for (int i = 0; i < iters; i++) {
        // ---- Pack ----
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                pack_send[off_left + r * HALO + c] = local[(HALO + r) * local_w + HALO + c];
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                pack_send[off_right + r * HALO + c] = local[(HALO + r) * local_w + tile_w + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                pack_send[off_top + r * tile_w + c] = local[(HALO + r) * local_w + HALO + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                pack_send[off_bottom + r * tile_w + c] = local[(tile_h + r) * local_w + HALO + c];

        MPI_Win_fence(0, pack_win);
        if (L != MPI_PROC_NULL)
            MPI_Put(pack_send + off_left, vert_size, MPI_FLOAT, L, off_right, vert_size, MPI_FLOAT,
                    pack_win);
        if (R != MPI_PROC_NULL)
            MPI_Put(pack_send + off_right, vert_size, MPI_FLOAT, R, off_left, vert_size, MPI_FLOAT,
                    pack_win);
        if (U != MPI_PROC_NULL)
            MPI_Put(pack_send + off_top, horiz_size, MPI_FLOAT, U, off_bottom, horiz_size,
                    MPI_FLOAT, pack_win);
        if (D != MPI_PROC_NULL)
            MPI_Put(pack_send + off_bottom, horiz_size, MPI_FLOAT, D, off_top, horiz_size,
                    MPI_FLOAT, pack_win);
        MPI_Win_fence(0, pack_win);

        // ---- Unpack ----
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                local[(HALO + r) * local_w + c] = pack_recv[off_left + r * HALO + c];
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                local[(HALO + r) * local_w + tile_w + HALO + c] =
                    pack_recv[off_right + r * HALO + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                local[r * local_w + HALO + c] = pack_recv[off_top + r * tile_w + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                local[(tile_h + HALO + r) * local_w + HALO + c] =
                    pack_recv[off_bottom + r * tile_w + c];
    }
    double elapsed = MPI_Wtime() - t0;
    MPI_Barrier(MPI_COMM_WORLD);
    return elapsed;
}

// ============================================================================
//  Mode D: strided MPI_Put with PSCW (post-start-complete-wait, neighbor-only sync)
// ============================================================================
double mode_strided_put_pscw(float *local, int tile_w, int tile_h, int local_w, MPI_Win win,
                             int L, int R, int U, int D, MPI_Datatype vert, MPI_Datatype horiz,
                             MPI_Group neighbor_group, int iters) {
    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = MPI_Wtime();
    for (int i = 0; i < iters; i++) {
        MPI_Win_post(neighbor_group, 0, win);
        MPI_Win_start(neighbor_group, 0, win);
        if (L != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + HALO, 1, vert, L,
                    HALO * local_w + tile_w + HALO, 1, vert, win);
        }
        if (R != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + tile_w, 1, vert, R, HALO * local_w, 1, vert, win);
        }
        if (U != MPI_PROC_NULL) {
            MPI_Put(local + HALO * local_w + HALO, 1, horiz, U,
                    (tile_h + HALO) * local_w + HALO, 1, horiz, win);
        }
        if (D != MPI_PROC_NULL) {
            MPI_Put(local + tile_h * local_w + HALO, 1, horiz, D, HALO, 1, horiz, win);
        }
        MPI_Win_complete(win);
        MPI_Win_wait(win);
    }
    double elapsed = MPI_Wtime() - t0;
    MPI_Barrier(MPI_COMM_WORLD);
    return elapsed;
}

// ============================================================================
//  Mode E: packed MPI_Put with PSCW
// ============================================================================
double mode_packed_put_pscw(float *local, float *pack_send, float *pack_recv, int tile_w,
                            int tile_h, int local_w, MPI_Win pack_win, int L, int R, int U, int D,
                            MPI_Group neighbor_group, int iters) {
    const int vert_size = tile_h * HALO;
    const int horiz_size = tile_w * HALO;
    const int off_left = 0;
    const int off_right = vert_size;
    const int off_top = 2 * vert_size;
    const int off_bottom = 2 * vert_size + horiz_size;

    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = MPI_Wtime();
    for (int i = 0; i < iters; i++) {
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                pack_send[off_left + r * HALO + c] = local[(HALO + r) * local_w + HALO + c];
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                pack_send[off_right + r * HALO + c] = local[(HALO + r) * local_w + tile_w + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                pack_send[off_top + r * tile_w + c] = local[(HALO + r) * local_w + HALO + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                pack_send[off_bottom + r * tile_w + c] = local[(tile_h + r) * local_w + HALO + c];

        MPI_Win_post(neighbor_group, 0, pack_win);
        MPI_Win_start(neighbor_group, 0, pack_win);
        if (L != MPI_PROC_NULL)
            MPI_Put(pack_send + off_left, vert_size, MPI_FLOAT, L, off_right, vert_size, MPI_FLOAT,
                    pack_win);
        if (R != MPI_PROC_NULL)
            MPI_Put(pack_send + off_right, vert_size, MPI_FLOAT, R, off_left, vert_size, MPI_FLOAT,
                    pack_win);
        if (U != MPI_PROC_NULL)
            MPI_Put(pack_send + off_top, horiz_size, MPI_FLOAT, U, off_bottom, horiz_size,
                    MPI_FLOAT, pack_win);
        if (D != MPI_PROC_NULL)
            MPI_Put(pack_send + off_bottom, horiz_size, MPI_FLOAT, D, off_top, horiz_size,
                    MPI_FLOAT, pack_win);
        MPI_Win_complete(pack_win);
        MPI_Win_wait(pack_win);

        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                local[(HALO + r) * local_w + c] = pack_recv[off_left + r * HALO + c];
        for (int r = 0; r < tile_h; r++)
            for (int c = 0; c < HALO; c++)
                local[(HALO + r) * local_w + tile_w + HALO + c] =
                    pack_recv[off_right + r * HALO + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                local[r * local_w + HALO + c] = pack_recv[off_top + r * tile_w + c];
        for (int r = 0; r < HALO; r++)
            for (int c = 0; c < tile_w; c++)
                local[(tile_h + HALO + r) * local_w + HALO + c] =
                    pack_recv[off_bottom + r * tile_w + c];
    }
    double elapsed = MPI_Wtime() - t0;
    MPI_Barrier(MPI_COMM_WORLD);
    return elapsed;
}

// ============================================================================
int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank, size;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);

    int tile_w = 1024, tile_h = 1024;
    if (argc >= 3) {
        tile_w = std::atoi(argv[1]);
        tile_h = std::atoi(argv[2]);
    }

    int dims[2] = {0, 0};
    if (argc >= 5) {
        dims[0] = std::atoi(argv[3]);
        dims[1] = std::atoi(argv[4]);
    } else {
        MPI_Dims_create(size, 2, dims);
    }

    int periods[2] = {0, 0};
    MPI_Comm cart;
    MPI_Cart_create(MPI_COMM_WORLD, 2, dims, periods, 0, &cart);

    int L, R, U, D;
    MPI_Cart_shift(cart, 0, 1, &L, &R);
    MPI_Cart_shift(cart, 1, 1, &U, &D);

    const int local_w = tile_w + 2 * HALO;
    const int local_h = tile_h + 2 * HALO;
    std::vector<float> local(local_w * local_h, float(rank));

    MPI_Win tile_win;
    MPI_Win_create(local.data(), local.size() * sizeof(float), sizeof(float), MPI_INFO_NULL, cart,
                   &tile_win);

    MPI_Datatype vert, horiz;
    MPI_Type_vector(tile_h, HALO, local_w, MPI_FLOAT, &vert);
    MPI_Type_vector(HALO, tile_w, local_w, MPI_FLOAT, &horiz);
    MPI_Type_commit(&vert);
    MPI_Type_commit(&horiz);

    const int vert_size = tile_h * HALO;
    const int horiz_size = tile_w * HALO;
    const int pack_size = 2 * vert_size + 2 * horiz_size;
    std::vector<float> pack_send(pack_size, 0);
    std::vector<float> pack_recv(pack_size, 0);

    MPI_Win pack_win;
    MPI_Win_create(pack_recv.data(), pack_recv.size() * sizeof(float), sizeof(float),
                   MPI_INFO_NULL, cart, &pack_win);

    // Build neighbor group for PSCW synchronization
    MPI_Group world_group, neighbor_group;
    MPI_Comm_group(cart, &world_group);
    int neighbor_ranks[4];
    int n_neighbors = 0;
    if (L != MPI_PROC_NULL) neighbor_ranks[n_neighbors++] = L;
    if (R != MPI_PROC_NULL) neighbor_ranks[n_neighbors++] = R;
    if (U != MPI_PROC_NULL) neighbor_ranks[n_neighbors++] = U;
    if (D != MPI_PROC_NULL) neighbor_ranks[n_neighbors++] = D;
    MPI_Group_incl(world_group, n_neighbors, neighbor_ranks, &neighbor_group);

    // Warmup all modes so no startup effects leak into timing
    mode_strided_get(local.data(), tile_w, tile_h, local_w, tile_win, L, R, U, D, vert, horiz,
                     WARMUP);
    mode_strided_put(local.data(), tile_w, tile_h, local_w, tile_win, L, R, U, D, vert, horiz,
                     WARMUP);
    mode_packed_put(local.data(), pack_send.data(), pack_recv.data(), tile_w, tile_h, local_w,
                    pack_win, L, R, U, D, WARMUP);
    mode_strided_put_pscw(local.data(), tile_w, tile_h, local_w, tile_win, L, R, U, D, vert, horiz,
                          neighbor_group, WARMUP);
    mode_packed_put_pscw(local.data(), pack_send.data(), pack_recv.data(), tile_w, tile_h, local_w,
                         pack_win, L, R, U, D, neighbor_group, WARMUP);

    // Benchmark
    double t_sg = mode_strided_get(local.data(), tile_w, tile_h, local_w, tile_win, L, R, U, D,
                                   vert, horiz, ITERS);
    double t_sp = mode_strided_put(local.data(), tile_w, tile_h, local_w, tile_win, L, R, U, D,
                                   vert, horiz, ITERS);
    double t_pp = mode_packed_put(local.data(), pack_send.data(), pack_recv.data(), tile_w, tile_h,
                                  local_w, pack_win, L, R, U, D, ITERS);
    double t_sp_pscw = mode_strided_put_pscw(local.data(), tile_w, tile_h, local_w, tile_win, L, R,
                                             U, D, vert, horiz, neighbor_group, ITERS);
    double t_pp_pscw = mode_packed_put_pscw(local.data(), pack_send.data(), pack_recv.data(),
                                            tile_w, tile_h, local_w, pack_win, L, R, U, D,
                                            neighbor_group, ITERS);

    if (rank == 0) {
        std::cout << std::fixed << std::setprecision(4);
        std::cout << "\n========== HALO EXCHANGE BENCHMARK ==========\n";
        std::cout << "Ranks: " << size << "  Topology: " << dims[0] << "x" << dims[1]
                  << "  Tile: " << tile_w << "x" << tile_h << "  Halo: " << HALO << "\n";
        std::cout << "Iterations: " << ITERS << "  (warmup " << WARMUP << ")\n\n";

        auto fmt = [](const std::string &name, double t, double baseline) {
            double per_iter_us = t / ITERS * 1e6;
            double ratio = t / baseline;
            std::cout << "  " << std::left << std::setw(28) << name << std::right << std::setw(10)
                      << t << " s" << std::setw(12) << per_iter_us << " us"
                      << std::setw(10) << ratio << "x\n";
        };

        std::cout << "  " << std::left << std::setw(32) << "Mode" << std::right << std::setw(12)
                  << "Total" << std::setw(15) << "Per-iter" << std::setw(10) << "vs B" << "\n";
        std::cout << "  " << std::string(70, '-') << "\n";
        fmt("A) strided Get   + fence", t_sg, t_sp);
        fmt("B) strided Put   + fence", t_sp, t_sp);
        fmt("C) packed  Put   + fence", t_pp, t_sp);
        fmt("D) strided Put   + PSCW", t_sp_pscw, t_sp);
        fmt("E) packed  Put   + PSCW", t_pp_pscw, t_sp);

        std::cout << "\n  Factor analysis:\n";
        std::cout << "    A/B  (MPI_Get→Put gain):           " << (t_sg / t_sp) << "x\n";
        std::cout << "    B/C  (strided→packed gain):        " << (t_sp / t_pp) << "x\n";
        std::cout << "    B/D  (fence→PSCW on strided):      " << (t_sp / t_sp_pscw) << "x\n";
        std::cout << "    C/E  (fence→PSCW on packed):       " << (t_pp / t_pp_pscw) << "x\n";
        std::cout << "    B/E  (best combo vs strided+fence):" << (t_sp / t_pp_pscw) << "x\n";
        std::cout << "    A/E  (A naive  vs E fully tuned):  " << (t_sg / t_pp_pscw) << "x\n\n";
    }

    MPI_Type_free(&vert);
    MPI_Type_free(&horiz);
    MPI_Group_free(&neighbor_group);
    MPI_Group_free(&world_group);
    MPI_Win_free(&tile_win);
    MPI_Win_free(&pack_win);
    MPI_Comm_free(&cart);
    MPI_Finalize();
    return 0;
}

/**
 * @file    ParallelHeatSolver.cpp
 *
 * @author  Name Surname <xlogin00@fit.vutbr.cz>
 *
 * @brief   Course: PPP 2023/2024 - Project 1
 *          This file contains implementation of parallel heat equation solver
 *          using MPI/OpenMP hybrid approach.
 *
 * @date    2024-02-23
 */

#include <algorithm>
#include <array>
#include <cstddef>
#include <ios>
#include <iostream>
#include <mpi.h>
#include <ostream>
#include <string_view>
#include <utility>
#include <vector>

#include "MaterialProperties.hpp"
#include "ParallelHeatSolver.hpp"

ParallelHeatSolver::ParallelHeatSolver(const SimulationProperties &simulationProps,
                                       const MaterialProperties &materialProps)
    : HeatSolverBase(simulationProps, materialProps) {
    MPI_Comm_size(MPI_COMM_WORLD, &mWorldSize);
    MPI_Comm_rank(MPI_COMM_WORLD, &mWorldRank);

    /**********************************************************************************************************************/
    /*                                  Call init* and alloc* methods in correct order */
    /**********************************************************************************************************************/

    initGridTopology();
    initDataDistribution();
    allocLocalTiles();
    initHaloExchange();

    if (!mSimulationProps.getOutputFileName().empty()) {
        /**********************************************************************************************************************/
        /*                               Open output file if output file name was specified. */
        /*  If mSimulationProps.useParallelIO() flag is set to true, open output file for parallel
         * access, otherwise open it  */
        /*                         only on MASTER rank using sequetial IO. Use openOutputFile*
         * methods.                       */
        /**********************************************************************************************************************/
        if (mSimulationProps.useParallelIO()) {
            openOutputFileParallel();
        } else if (mWorldRank == 0) {
            openOutputFileSequential();
        }
    }
}

ParallelHeatSolver::~ParallelHeatSolver() {
    /**********************************************************************************************************************/
    /*                                  Call deinit* and dealloc* methods in correct order */
    /*                                             (should be in reverse order) */
    /**********************************************************************************************************************/
    deinitHaloExchange();
    deallocLocalTiles();
    deinitDataDistribution();
    deinitGridTopology();
}

std::string_view ParallelHeatSolver::getCodeType() const { return codeType; }

void ParallelHeatSolver::initGridTopology() {
    /**********************************************************************************************************************/
    /*                          Initialize 2D grid topology using non-periodic MPI Cartesian
     * topology.                    */
    /*                       Also create a communicator for middle column average temperature
     * computation.                */
    /**********************************************************************************************************************/
    int nX, nY;
    this->mSimulationProps.getDecompGrid(nX, nY);
    this->processTileX = nX;
    this->processTileY = nY;

    size_t edge = this->mMaterialProps.getEdgeSize();
    this->globalTileX = edge;
    this->globalTileY = edge;

    this->transferTileX = this->globalTileX / this->processTileX;
    this->transferTileY = this->globalTileY / this->processTileY;

    int periods[2] = {0, 0};
    int dims[2] = {nX, nY};
    MPI_Cart_create(MPI_COMM_WORLD, 2, dims, periods, false, &this->cartComm);
    MPI_Comm_rank(cartComm, &mCartRank);
    MPI_Cart_coords(cartComm, mCartRank, 2, mCoords.data());

    const int middleColumnTile = static_cast<int>((globalTileX / 2) / transferTileX);
    const int color = (mCoords[0] == middleColumnTile) ? 0 : MPI_UNDEFINED;
    MPI_Comm_split(cartComm, color, mWorldRank, &middleColComm);
}

void ParallelHeatSolver::deinitGridTopology() {
    /**********************************************************************************************************************/
    /*      Deinitialize 2D grid topology and the middle column average temperature computation
     * communicator              */
    /**********************************************************************************************************************/
    if (middleColComm != MPI_COMM_NULL) {
        MPI_Comm_free(&middleColComm);
        middleColComm = MPI_COMM_NULL;
    }
    if (cartComm != MPI_COMM_NULL) {
        MPI_Comm_free(&cartComm);
        cartComm = MPI_COMM_NULL;
    }
}

void ParallelHeatSolver::initDataDistribution() {
    /**********************************************************************************************************************/
    /*                 Initialize variables and MPI datatypes for data distribution (float and int).
     */
    /**********************************************************************************************************************/
    // Local stored array dimensions
    // Includes halo zones
    localTileX = this->transferTileX + this->haloZoneSize * 2;
    localTileY = this->transferTileY + this->haloZoneSize * 2;

    int globalDims[2] = {static_cast<int>(globalTileY), static_cast<int>(globalTileX)};
    int transferDims[2] = {static_cast<int>(transferTileY), static_cast<int>(transferTileX)};
    int globalStart[2] = {0, 0};
    int localDims[2] = {static_cast<int>(localTileY), static_cast<int>(localTileX)};
    int localStart[2] = {static_cast<int>(haloZoneSize), static_cast<int>(haloZoneSize)};

    MPI_Datatype globalTileIntRaw = MPI_DATATYPE_NULL;
    MPI_Datatype globalTileFloatRaw = MPI_DATATYPE_NULL;
    MPI_Datatype localTileIntRaw = MPI_DATATYPE_NULL;
    MPI_Datatype localTileFloatRaw = MPI_DATATYPE_NULL;

    MPI_Type_create_subarray(2, globalDims, transferDims, globalStart, MPI_ORDER_C, MPI_INT,
                             &globalTileIntRaw);
    MPI_Type_create_subarray(2, globalDims, transferDims, globalStart, MPI_ORDER_C, MPI_FLOAT,
                             &globalTileFloatRaw);
    MPI_Type_create_subarray(2, localDims, transferDims, localStart, MPI_ORDER_C, MPI_INT,
                             &localTileIntRaw);
    MPI_Type_create_subarray(2, localDims, transferDims, localStart, MPI_ORDER_C, MPI_FLOAT,
                             &localTileFloatRaw);

    MPI_Type_create_resized(globalTileIntRaw, 0, sizeof(int), &globalTransferTileInt);
    MPI_Type_create_resized(globalTileFloatRaw, 0, sizeof(float), &globalTransferTileFloat);
    MPI_Type_create_resized(localTileIntRaw, 0, sizeof(int), &localTransferTileInt);
    MPI_Type_create_resized(localTileFloatRaw, 0, sizeof(float), &localTransferTileFloat);

    MPI_Type_commit(&globalTransferTileInt);
    MPI_Type_commit(&globalTransferTileFloat);
    MPI_Type_commit(&localTransferTileInt);
    MPI_Type_commit(&localTransferTileFloat);

    MPI_Type_free(&globalTileIntRaw);
    MPI_Type_free(&globalTileFloatRaw);
    MPI_Type_free(&localTileIntRaw);
    MPI_Type_free(&localTileFloatRaw);
}

void ParallelHeatSolver::deinitDataDistribution() {
    /**********************************************************************************************************************/
    /*                       Deinitialize variables and MPI datatypes for data distribution. */
    /**********************************************************************************************************************/
    if (globalTransferTileInt != MPI_DATATYPE_NULL) {
        MPI_Type_free(&globalTransferTileInt);
        globalTransferTileInt = MPI_DATATYPE_NULL;
    }
    if (globalTransferTileFloat != MPI_DATATYPE_NULL) {
        MPI_Type_free(&globalTransferTileFloat);
        globalTransferTileFloat = MPI_DATATYPE_NULL;
    }
    if (localTransferTileInt != MPI_DATATYPE_NULL) {
        MPI_Type_free(&localTransferTileInt);
        localTransferTileInt = MPI_DATATYPE_NULL;
    }
    if (localTransferTileFloat != MPI_DATATYPE_NULL) {
        MPI_Type_free(&localTransferTileFloat);
        localTransferTileFloat = MPI_DATATYPE_NULL;
    }
}

void ParallelHeatSolver::allocLocalTiles() {
    /**********************************************************************************************************************/
    /*            Allocate local tiles for domain map (1x), domain parameters (1x) and domain
     * temperature (2x).           */
    /*                                               Use AlignedAllocator. */
    /**********************************************************************************************************************/

    int localTileSize = localTileX * localTileY;
    materialTypesLocal.resize(localTileSize);
    materialPropertiesLocal.resize(localTileSize);
    temperatureBufferLocal[0].resize(localTileSize);
    temperatureBufferLocal[1].resize(localTileSize);
}

void ParallelHeatSolver::deallocLocalTiles() {
    /**********************************************************************************************************************/
    /*                                   Deallocate local tiles (may be empty). */
    /**********************************************************************************************************************/
    materialTypesLocal.clear();
    materialPropertiesLocal.clear();
    temperatureBufferLocal[0].clear();
    temperatureBufferLocal[1].clear();
}

void ParallelHeatSolver::initHaloExchange() {
    /**********************************************************************************************************************/
    /*                            Initialize variables and MPI datatypes for halo exchange. */
    /*                    If mSimulationProps.isRunParallelRMA() flag is set to true, create RMA
     * windows.                 */
    /**********************************************************************************************************************/

    MPI_Type_vector(static_cast<int>(haloZoneSize), static_cast<int>(transferTileX),
                    static_cast<int>(localTileX), MPI_FLOAT, &haloZoneHorizontal);
    MPI_Type_vector(static_cast<int>(transferTileY), static_cast<int>(haloZoneSize),
                    static_cast<int>(localTileX), MPI_FLOAT, &haloZoneVertical);

    MPI_Type_commit(&haloZoneVertical);
    MPI_Type_commit(&haloZoneHorizontal);

    if (mSimulationProps.isRunParallelRMA()) {
        const MPI_Aint windowSize = static_cast<MPI_Aint>(localTileY * localTileX * sizeof(float));
        MPI_Win_create(temperatureBufferLocal[0].data(), windowSize, sizeof(float), MPI_INFO_NULL,
                       cartComm, &windows[0]);
        MPI_Win_create(temperatureBufferLocal[1].data(), windowSize, sizeof(float), MPI_INFO_NULL,
                       cartComm, &windows[1]);
    }
}

void ParallelHeatSolver::deinitHaloExchange() {
    /**********************************************************************************************************************/
    /*                            Deinitialize variables and MPI datatypes for halo exchange. */
    /**********************************************************************************************************************/
    if (windows[0] != MPI_WIN_NULL) {
        MPI_Win_free(&windows[0]);
        windows[0] = MPI_WIN_NULL;
    }
    if (windows[1] != MPI_WIN_NULL) {
        MPI_Win_free(&windows[1]);
        windows[1] = MPI_WIN_NULL;
    }
    if (haloZoneHorizontal != MPI_DATATYPE_NULL) {
        MPI_Type_free(&haloZoneHorizontal);
        haloZoneHorizontal = MPI_DATATYPE_NULL;
    }
    if (haloZoneVertical != MPI_DATATYPE_NULL) {
        MPI_Type_free(&haloZoneVertical);
        haloZoneVertical = MPI_DATATYPE_NULL;
    }
}

template <typename T> void ParallelHeatSolver::scatterTiles(const T *globalData, T *localData) {
    static_assert(std::is_same_v<T, int> || std::is_same_v<T, float>,
                  "Unsupported scatter datatype!");

    /**********************************************************************************************************************/
    /*                      Implement master's global tile scatter to each rank's local tile. */
    /*     The template T parameter is restricted to int or float type. You can choose the
     * correct MPI datatype like:     */
    /*                                                                                                                    */
    /*  const MPI_Datatype globalTileType = std::is_same_v<T, int> ? globalFloatTileType :
     * globalIntTileType;             */
    /*  const MPI_Datatype localTileType  = std::is_same_v<T, int> ? localIntTileType    :
     * localfloatTileType;            */
    /**********************************************************************************************************************/
    const MPI_Datatype globalTileType =
        std::is_same_v<T, int> ? globalTransferTileInt : globalTransferTileFloat;
    const MPI_Datatype localTileType =
        std::is_same_v<T, int> ? localTransferTileInt : localTransferTileFloat;

    // TODO: root 0?
    std::vector<int> sendCounts(mWorldSize);
    std::vector<int> displs(mWorldSize);
    // coords = (tileY, tileX)
    int coords[2] = {};

    if (mWorldRank == 0) {
        for (int i = 0; i < mWorldSize; i++) {
            sendCounts[i] = 1;
            MPI_Cart_coords(cartComm, i, 2, coords);
            // funny math here, basically take number of rows from that SMALL TILE, multiply it by
            // its position in the global grid (process based), and then also multiply it by stride
            // of the global grid (transferTileX)
            // that will moves us to correct row in the global grid, and then we just adjust
            // the column
            // (pláčem 😭)
            displs[i] = (coords[1] * transferTileY) * globalTileX + coords[0] * transferTileX;
        }
    }

    MPI_Scatterv(globalData, sendCounts.data(), displs.data(), globalTileType, localData, 1,
                 localTileType, 0, cartComm);
}

template <typename T> void ParallelHeatSolver::gatherTiles(const T *localData, T *globalData) {
    static_assert(std::is_same_v<T, int> || std::is_same_v<T, float>,
                  "Unsupported gather datatype!");

    /**********************************************************************************************************************/
    /*                      Implement each rank's local tile gather to master's rank global
     * tile. */
    /*     The template T parameter is restricted to int or float type. You can choose the
     * correct MPI datatype like:     */
    /*                                                                                                                    */
    /*  const MPI_Datatype localTileType  = std::is_same_v<T, int> ? localIntTileType    :
     * localfloatTileType;            */
    /*  const MPI_Datatype globalTileType = std::is_same_v<T, int> ? globalFloatTileType :
     * globalIntTileType;             */
    /**********************************************************************************************************************/
    const MPI_Datatype globalTileType =
        std::is_same_v<T, int> ? globalTransferTileInt : globalTransferTileFloat;
    const MPI_Datatype localTileType =
        std::is_same_v<T, int> ? localTransferTileInt : localTransferTileFloat;

    std::vector<int> recvCounts(mWorldSize);
    std::vector<int> displs(mWorldSize);
    int coords[2] = {};

    for (int i = 0; i < mWorldSize; i++) {
        recvCounts[i] = 1;
        MPI_Cart_coords(cartComm, i, 2, coords);
        displs[i] = (coords[1] * transferTileY) * globalTileX + coords[0] * transferTileX;
    }

    MPI_Gatherv(localData, 1, localTileType, globalData, recvCounts.data(), displs.data(),
                globalTileType, 0, cartComm);
}

void ParallelHeatSolver::computeHaloZones(const float *oldTemp, float *newTemp) {
    /**********************************************************************************************************************/
    /*  Compute new temperatures in halo zones, so that copy operations can be overlapped with
     * inner region computation.  */
    /*                        Use updateTile method to compute new temperatures in halo zones.
     */
    /*                             TAKE CARE NOT TO COMPUTE THE SAME AREAS TWICE */
    /**********************************************************************************************************************/

    const bool hasLeft = mCoords[0] > 0;
    const bool hasRight = mCoords[0] < static_cast<int>(processTileX) - 1;
    const bool hasUp = mCoords[1] > 0;
    const bool hasDown = mCoords[1] < static_cast<int>(processTileY) - 1;

    const std::size_t computeOffsetX = haloZoneSize + (hasLeft ? 0 : haloZoneSize);
    const std::size_t computeOffsetY = haloZoneSize + (hasUp ? 0 : haloZoneSize);
    const std::size_t computeSizeX =
        transferTileX - (hasLeft ? 0 : haloZoneSize) - (hasRight ? 0 : haloZoneSize);
    const std::size_t computeSizeY =
        transferTileY - (hasUp ? 0 : haloZoneSize) - (hasDown ? 0 : haloZoneSize);

    if (hasUp && computeSizeX > 0) {
        updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
                   computeOffsetX, computeOffsetY, computeSizeX, haloZoneSize, localTileX);
    }

    if (hasDown && computeSizeX > 0) {
        updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
                   computeOffsetX, computeOffsetY + computeSizeY - haloZoneSize, computeSizeX,
                   haloZoneSize, localTileX);
    }

    const std::size_t verticalOffsetY = computeOffsetY + (hasUp ? haloZoneSize : 0);
    const std::size_t verticalSizeY =
        computeSizeY - (hasUp ? haloZoneSize : 0) - (hasDown ? haloZoneSize : 0);

    if (hasLeft && verticalSizeY > 0) {
        updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
                   computeOffsetX, verticalOffsetY, haloZoneSize, verticalSizeY, localTileX);
    }

    if (hasRight && verticalSizeY > 0) {
        updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
                   computeOffsetX + computeSizeX - haloZoneSize, verticalOffsetY, haloZoneSize,
                   verticalSizeY, localTileX);
    }
}

void ParallelHeatSolver::startHaloExchangeP2P(float *localData,
                                              std::array<MPI_Request, 8> &requests) {
    /**********************************************************************************************************************/
    /*                       Start the non-blocking halo zones exchange using P2P communication.
     */
    /*                         Use the requests array to return the requests from the function.
     */
    /*                            Don't forget to set the empty requests to MPI_REQUEST_NULL. */
    /**********************************************************************************************************************/
    requests.fill(MPI_REQUEST_NULL);

    int leftRank = MPI_PROC_NULL;
    int rightRank = MPI_PROC_NULL;
    int upRank = MPI_PROC_NULL;
    int downRank = MPI_PROC_NULL;

    MPI_Cart_shift(cartComm, 0, 1, &leftRank, &rightRank);
    MPI_Cart_shift(cartComm, 1, 1, &upRank, &downRank);

    const std::size_t sendLeft = haloZoneSize * localTileX + haloZoneSize;
    const std::size_t recvLeft = haloZoneSize * localTileX;
    const std::size_t sendRight = haloZoneSize * localTileX + transferTileX;
    const std::size_t recvRight = haloZoneSize * localTileX + transferTileX + haloZoneSize;
    const std::size_t sendUp = haloZoneSize * localTileX + haloZoneSize;
    const std::size_t recvUp = haloZoneSize;
    const std::size_t sendDown = transferTileY * localTileX + haloZoneSize;
    const std::size_t recvDown = (transferTileY + haloZoneSize) * localTileX + haloZoneSize;

    if (leftRank != MPI_PROC_NULL) {
        MPI_Isend(localData + sendLeft, 1, haloZoneVertical, leftRank, 0, cartComm, &requests[0]);
        MPI_Irecv(localData + recvLeft, 1, haloZoneVertical, leftRank, 0, cartComm, &requests[1]);
    }

    if (rightRank != MPI_PROC_NULL) {
        MPI_Isend(localData + sendRight, 1, haloZoneVertical, rightRank, 0, cartComm, &requests[2]);
        MPI_Irecv(localData + recvRight, 1, haloZoneVertical, rightRank, 0, cartComm, &requests[3]);
    }

    if (upRank != MPI_PROC_NULL) {
        MPI_Isend(localData + sendUp, 1, haloZoneHorizontal, upRank, 0, cartComm, &requests[4]);
        MPI_Irecv(localData + recvUp, 1, haloZoneHorizontal, upRank, 0, cartComm, &requests[5]);
    }

    if (downRank != MPI_PROC_NULL) {
        MPI_Isend(localData + sendDown, 1, haloZoneHorizontal, downRank, 0, cartComm, &requests[6]);
        MPI_Irecv(localData + recvDown, 1, haloZoneHorizontal, downRank, 0, cartComm, &requests[7]);
    }
}

void ParallelHeatSolver::startHaloExchangeRMA(float *localData, MPI_Win window) {
    /**********************************************************************************************************************/
    /*                       Start the non-blocking halo zones exchange using RMA communication.
     */
    /*                   Do not forget that you put/get the values to/from the target's opposite
     * side                     */
    /**********************************************************************************************************************/
    MPI_Win_fence(0, window);

    int leftRank = MPI_PROC_NULL;
    int rightRank = MPI_PROC_NULL;
    int upRank = MPI_PROC_NULL;
    int downRank = MPI_PROC_NULL;

    MPI_Cart_shift(cartComm, 0, 1, &leftRank, &rightRank);
    MPI_Cart_shift(cartComm, 1, 1, &upRank, &downRank);

    const std::size_t leftInterior = haloZoneSize * localTileX + haloZoneSize;
    const std::size_t rightInterior = haloZoneSize * localTileX + transferTileX;
    const std::size_t topInterior = haloZoneSize * localTileX + haloZoneSize;
    const std::size_t bottomInterior = transferTileY * localTileX + haloZoneSize;

    const std::size_t leftHalo = haloZoneSize * localTileX;
    const std::size_t rightHalo = haloZoneSize * localTileX + transferTileX + haloZoneSize;
    const std::size_t topHalo = haloZoneSize;
    const std::size_t bottomHalo = (transferTileY + haloZoneSize) * localTileX + haloZoneSize;

    if (leftRank != MPI_PROC_NULL) {
        MPI_Get(localData + leftHalo, 1, haloZoneVertical, leftRank, rightInterior, 1,
                haloZoneVertical, window);
    }
    if (rightRank != MPI_PROC_NULL) {
        MPI_Get(localData + rightHalo, 1, haloZoneVertical, rightRank, leftInterior, 1,
                haloZoneVertical, window);
    }
    if (upRank != MPI_PROC_NULL) {
        MPI_Get(localData + topHalo, 1, haloZoneHorizontal, upRank, bottomInterior, 1,
                haloZoneHorizontal, window);
    }
    if (downRank != MPI_PROC_NULL) {
        MPI_Get(localData + bottomHalo, 1, haloZoneHorizontal, downRank, topInterior, 1,
                haloZoneHorizontal, window);
    }
}

void ParallelHeatSolver::awaitHaloExchangeP2P(std::array<MPI_Request, 8> &requests) {
    /**********************************************************************************************************************/
    /*                       Wait for all halo zone exchanges to finalize using P2P
     * communication.
     */
    /**********************************************************************************************************************/

    MPI_Status statuses[8] = {};
    MPI_Waitall(8, requests.data(), statuses);
}

void ParallelHeatSolver::awaitHaloExchangeRMA(MPI_Win window) {
    /**********************************************************************************************************************/
    /*                       Wait for all halo zone exchanges to finalize using RMA
     * communication.
     */
    /**********************************************************************************************************************/
    MPI_Win_fence(0, window);
}

void ParallelHeatSolver::run(std::vector<float, AlignedAllocator<float>> &outResult) {
    std::array<MPI_Request, 8> requestsP2P{};
    std::vector<float, AlignedAllocator<float>> gatheredData;
    const bool hasLeft = mCoords[0] > 0;
    const bool hasRight = mCoords[0] < static_cast<int>(processTileX) - 1;
    const bool hasUp = mCoords[1] > 0;
    const bool hasDown = mCoords[1] < static_cast<int>(processTileY) - 1;
    const std::size_t computeOffsetX = haloZoneSize + (hasLeft ? 0 : haloZoneSize);
    const std::size_t computeOffsetY = haloZoneSize + (hasUp ? 0 : haloZoneSize);
    const std::size_t computeSizeX =
        transferTileX - (hasLeft ? 0 : haloZoneSize) - (hasRight ? 0 : haloZoneSize);
    const std::size_t computeSizeY =
        transferTileY - (hasUp ? 0 : haloZoneSize) - (hasDown ? 0 : haloZoneSize);
    const std::size_t innerOffsetX = computeOffsetX + (hasLeft ? haloZoneSize : 0);
    const std::size_t innerOffsetY = computeOffsetY + (hasUp ? haloZoneSize : 0);
    const std::size_t innerSizeX =
        computeSizeX - (hasLeft ? haloZoneSize : 0) - (hasRight ? haloZoneSize : 0);
    const std::size_t innerSizeY =
        computeSizeY - (hasUp ? haloZoneSize : 0) - (hasDown ? haloZoneSize : 0);

    if (mWorldRank == 0 && !mSimulationProps.useParallelIO() &&
        !mSimulationProps.getOutputFileName().empty()) {
        gatheredData.resize(mMaterialProps.getGridPointCount());
    }

    /**********************************************************************************************************************/
    /*                                         Scatter initial data. */
    /**********************************************************************************************************************/

    scatterTiles<float>(mMaterialProps.getInitialTemperature().data(),
                        temperatureBufferLocal[0].data());
    scatterTiles<float>(mMaterialProps.getDomainParameters().data(),
                        materialPropertiesLocal.data());
    scatterTiles<int>(mMaterialProps.getDomainMap().data(), materialTypesLocal.data());

    /**********************************************************************************************************************/
    /* Exchange halo zones of initial domain temperature and parameters using P2P communication.
     * Wait for them to finish. */
    /**********************************************************************************************************************/

    startHaloExchangeP2P(materialPropertiesLocal.data(), requestsP2P);
    awaitHaloExchangeP2P(requestsP2P);
    startHaloExchangeP2P(temperatureBufferLocal[0].data(), requestsP2P);
    awaitHaloExchangeP2P(requestsP2P);

    /**********************************************************************************************************************/
    /*                            Copy initial temperature to the second buffer. */
    /**********************************************************************************************************************/

    std::copy(temperatureBufferLocal[0].begin(), temperatureBufferLocal[0].end(),
              temperatureBufferLocal[1].begin());

    float middleColAvgTemp = 0.0f;
    double startTime = MPI_Wtime();

    // 3. Start main iterative simulation loop.
    for (std::size_t iter = 0; iter < mSimulationProps.getNumIterations(); ++iter) {
        const std::size_t oldIdx = iter % 2;       // Index of the buffer with old temperatures
        const std::size_t newIdx = (iter + 1) % 2; // Index of the buffer with new temperatures

        /**********************************************************************************************************************/
        /*                            Compute and exchange halo zones using P2P or RMA. */
        /**********************************************************************************************************************/
        computeHaloZones(temperatureBufferLocal[oldIdx].data(),
                         temperatureBufferLocal[newIdx].data());

        if (mSimulationProps.isRunParallelP2P()) {
            startHaloExchangeP2P(temperatureBufferLocal[newIdx].data(), requestsP2P);
        } else if (mSimulationProps.isRunParallelRMA()) {
            startHaloExchangeRMA(temperatureBufferLocal[newIdx].data(), windows[newIdx]);
        }

        /**********************************************************************************************************************/
        /*                           Compute the rest of the tile. Use updateTile method.                                     */
        /**********************************************************************************************************************/
        if (innerSizeX > 0 && innerSizeY > 0) {
            updateTile(temperatureBufferLocal[oldIdx].data(), temperatureBufferLocal[newIdx].data(),
                       materialPropertiesLocal.data(), materialTypesLocal.data(), innerOffsetX,
                       innerOffsetY, innerSizeX, innerSizeY, localTileX);
        }

        /**********************************************************************************************************************/
        /*                            Wait for all halo zone exchanges to finalize. */
        /**********************************************************************************************************************/
        if (mSimulationProps.isRunParallelP2P()) {
            awaitHaloExchangeP2P(requestsP2P);
        } else if (mSimulationProps.isRunParallelRMA()) {
            awaitHaloExchangeRMA(windows[newIdx]);
        }

        if (shouldStoreData(iter) && !mSimulationProps.getOutputFileName().empty()) {
            /**********************************************************************************************************************/
            /*                          Store the data into the output file using parallel or
             * sequential IO.                      */
            /**********************************************************************************************************************/
            if (mSimulationProps.useParallelIO()) {
                storeDataIntoFileParallel(mFileHandle, iter, temperatureBufferLocal[newIdx].data());
            } else {
                gatherTiles<float>(temperatureBufferLocal[newIdx].data(), gatheredData.data());
                if (mWorldRank == 0) {
                    storeDataIntoFileSequential(mFileHandle, iter, gatheredData.data());
                }
            }
        }

        if (shouldPrintProgress(iter) && shouldComputeMiddleColumnAverageTemperature()) {
            /**********************************************************************************************************************/
            /*                 Compute and print middle column average temperature and print
             * progress report.                     */
            /**********************************************************************************************************************/
            middleColAvgTemp = computeMiddleColumnAverageTemperatureParallel(
                temperatureBufferLocal[newIdx].data());

            int middleColRank;
            MPI_Comm_rank(middleColComm, &middleColRank);
            if (middleColRank == 0) {
                printProgressReport(iter, middleColAvgTemp);
            }
        }
    }

    const std::size_t resIdx =
        mSimulationProps.getNumIterations() % 2; // Index of the buffer with final temperatures

    double elapsedTime = MPI_Wtime() - startTime;

    /**********************************************************************************************************************/
    /*                                     Gather final domain temperature.                                               */
    /**********************************************************************************************************************/

    gatherTiles<float>(temperatureBufferLocal[resIdx].data(), outResult.data());

    /**********************************************************************************************************************/
    /*           Compute (sequentially) and report final middle column temperature average and
     * print final report.        */
    /**********************************************************************************************************************/

    if (mWorldRank == 0) {
        middleColAvgTemp = computeMiddleColumnAverageTemperatureSequential(outResult.data());
        printFinalReport(elapsedTime, middleColAvgTemp);
    }
}

bool ParallelHeatSolver::shouldComputeMiddleColumnAverageTemperature() const {
    /**********************************************************************************************************************/
    /*                Return true if rank should compute middle column average temperature. */
    /**********************************************************************************************************************/

    return middleColComm != MPI_COMM_NULL;
}

float ParallelHeatSolver::computeMiddleColumnAverageTemperatureParallel(
    const float *localData) const {
    /**********************************************************************************************************************/
    /*                  Implement parallel middle column average temperature computation. */
    /*                      Use OpenMP directives to accelerate the local computations. */
    /**********************************************************************************************************************/
    float localSum = 0.0f;

    const std::size_t globalMiddleCol = mMaterialProps.getEdgeSize() / 2;
    const std::size_t tileStartX = static_cast<std::size_t>(mCoords[0]) * transferTileX;
    const std::size_t localMiddleCol = haloZoneSize + (globalMiddleCol - tileStartX);

#pragma omp parallel for reduction(+ : localSum)
    for (std::size_t row = haloZoneSize; row < haloZoneSize + transferTileY; ++row) {
        localSum += localData[row * localTileX + localMiddleCol];
    }

    float globalSum = 0.0f;
    MPI_Allreduce(&localSum, &globalSum, 1, MPI_FLOAT, MPI_SUM, middleColComm);

    return globalSum / static_cast<float>(mMaterialProps.getEdgeSize());
}

float ParallelHeatSolver::computeMiddleColumnAverageTemperatureSequential(
    const float *globalData) const {
    /**********************************************************************************************************************/
    /*                  Implement sequential middle column average temperature computation. */
    /*                      Use OpenMP directives to accelerate the local computations. */
    /**********************************************************************************************************************/
    float middleColAvgTemp = 0.0f;
    const std::size_t edgeSize = mMaterialProps.getEdgeSize();

#pragma omp parallel for reduction(+ : middleColAvgTemp)
    for (std::size_t i = 0; i < edgeSize; ++i) {
        middleColAvgTemp += globalData[i * edgeSize + edgeSize / 2];
    }

    return middleColAvgTemp / static_cast<float>(edgeSize);
}

void ParallelHeatSolver::openOutputFileSequential() {
    // Create the output file for sequential access.
    mFileHandle = H5Fcreate(mSimulationProps.getOutputFileName(codeType).c_str(), H5F_ACC_TRUNC,
                            H5P_DEFAULT, H5P_DEFAULT);
    if (!mFileHandle.valid()) {
        throw std::ios::failure("Cannot create output file!");
    }
}

void ParallelHeatSolver::storeDataIntoFileSequential(hid_t fileHandle, std::size_t iteration,
                                                     const float *globalData) {
    storeDataIntoFile(fileHandle, iteration, globalData);
}

void ParallelHeatSolver::openOutputFileParallel() {
#ifdef H5_HAVE_PARALLEL
    Hdf5PropertyListHandle faplHandle(H5Pcreate(H5P_FILE_ACCESS));

    /**********************************************************************************************************************/
    /*                          Open output HDF5 file for parallel access with alignment. */
    /*      Set up faplHandle to use MPI-IO and alignment. The handle will automatically release
     * the resource.            */
    /**********************************************************************************************************************/

    H5Pset_fapl_mpio(faplHandle, MPI_COMM_WORLD, MPI_INFO_NULL);
    H5Pset_alignment(faplHandle, 0, 1 << 20);

    mFileHandle = H5Fcreate(mSimulationProps.getOutputFileName(codeType).c_str(), H5F_ACC_TRUNC,
                            H5P_DEFAULT, faplHandle);
    if (!mFileHandle.valid()) {
        throw std::ios::failure("Cannot create output file!");
    }
#else
    throw std::runtime_error("Parallel HDF5 support is not available!");
#endif /* H5_HAVE_PARALLEL */
}

void ParallelHeatSolver::storeDataIntoFileParallel(hid_t fileHandle,
                                                   [[maybe_unused]] std::size_t iteration,
                                                   [[maybe_unused]] const float *localData) {
    if (fileHandle == H5I_INVALID_HID) {
        return;
    }

#ifdef H5_HAVE_PARALLEL
    std::array gridSize{static_cast<hsize_t>(mMaterialProps.getEdgeSize()),
                        static_cast<hsize_t>(mMaterialProps.getEdgeSize())};

    // Create new HDF5 group in the output file
    std::string groupName =
        "Timestep_" + std::to_string(iteration / mSimulationProps.getWriteIntensity());

    Hdf5GroupHandle groupHandle(
        H5Gcreate(fileHandle, groupName.c_str(), H5P_DEFAULT, H5P_DEFAULT, H5P_DEFAULT));

    {
        /**********************************************************************************************************************/
        /*                                Compute the tile offsets and sizes. */
        /*               Note that the X and Y coordinates are swapped (but data not altered).
         */
        /**********************************************************************************************************************/

        const std::array<hsize_t, 2> tileOffset{static_cast<hsize_t>(mCoords[1] * transferTileY),
                                                static_cast<hsize_t>(mCoords[0] * transferTileX)};
        const std::array<hsize_t, 2> tileSize{static_cast<hsize_t>(transferTileY),
                                              static_cast<hsize_t>(transferTileX)};

        // Create new dataspace and dataset using it.
        static constexpr std::string_view dataSetName{"Temperature"};

        Hdf5PropertyListHandle datasetPropListHandle(H5Pcreate(H5P_DATASET_CREATE));

        /**********************************************************************************************************************/
        /*                            Create dataset property list to set up chunking. */
        /*                Set up chunking for collective write operation in
         * datasetPropListHandle variable.                   */
        /**********************************************************************************************************************/

        H5Pset_chunk(datasetPropListHandle, 2, tileSize.data());

        Hdf5DataspaceHandle dataSpaceHandle(H5Screate_simple(2, gridSize.data(), nullptr));
        Hdf5DatasetHandle dataSetHandle(H5Dcreate(groupHandle, dataSetName.data(), H5T_NATIVE_FLOAT,
                                                  dataSpaceHandle, H5P_DEFAULT,
                                                  datasetPropListHandle, H5P_DEFAULT));

        const std::array<hsize_t, 2> memSpaceSize{static_cast<hsize_t>(localTileY),
                                                  static_cast<hsize_t>(localTileX)};
        const std::array<hsize_t, 2> memOffset{static_cast<hsize_t>(haloZoneSize),
                                               static_cast<hsize_t>(haloZoneSize)};
        Hdf5DataspaceHandle memSpaceHandle(H5Screate_simple(2, memSpaceSize.data(), nullptr));

        /**********************************************************************************************************************/
        /*                Create memory dataspace representing tile in the memory (set up
         * memSpaceHandle).                    */
        /**********************************************************************************************************************/

        /**********************************************************************************************************************/
        /*              Select inner part of the tile in memory and matching part of the dataset
         * in the file                  */
        /*                           (given by position of the tile in global domain). */
        /**********************************************************************************************************************/

        H5Sselect_hyperslab(memSpaceHandle, H5S_SELECT_SET, memOffset.data(), nullptr,
                            tileSize.data(), nullptr);
        H5Sselect_hyperslab(dataSpaceHandle, H5S_SELECT_SET, tileOffset.data(), nullptr,
                            tileSize.data(), nullptr);

        Hdf5PropertyListHandle propListHandle(H5Pcreate(H5P_DATASET_XFER));

        /**********************************************************************************************************************/
        /*              Perform collective write operation, writting tiles from all processes at
         * once.                        */
        /*                                   Set up the propListHandle variable. */
        /**********************************************************************************************************************/

        H5Pset_dxpl_mpio(propListHandle, H5FD_MPIO_COLLECTIVE);

        H5Dwrite(dataSetHandle, H5T_NATIVE_FLOAT, memSpaceHandle, dataSpaceHandle, propListHandle,
                 localData);
    }

    {
        // 3. Store attribute with current iteration number in the group.
        static constexpr std::string_view attributeName{"Time"};
        Hdf5DataspaceHandle dataSpaceHandle(H5Screate(H5S_SCALAR));
        Hdf5AttributeHandle attributeHandle(H5Acreate2(groupHandle, attributeName.data(),
                                                       H5T_IEEE_F64LE, dataSpaceHandle, H5P_DEFAULT,
                                                       H5P_DEFAULT));
        const double snapshotTime = static_cast<double>(iteration);
        H5Awrite(attributeHandle, H5T_IEEE_F64LE, &snapshotTime);
    }
#else
    throw std::runtime_error("Parallel HDF5 support is not available!");
#endif /* H5_HAVE_PARALLEL */
}

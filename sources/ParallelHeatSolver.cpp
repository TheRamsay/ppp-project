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
    }
}

ParallelHeatSolver::~ParallelHeatSolver() {
    /**********************************************************************************************************************/
    /*                                  Call deinit* and dealloc* methods in correct order */
    /*                                             (should be in reverse order) */
    /**********************************************************************************************************************/
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
    MPI_Cart_create(MPI_COMM_WORLD, 2, dims, periods, true, &this->cartComm);

    // TODO: haha
    // int globalMid = nX / 2;
    // int color = MPI_UNDEFINED;
    // MPI_Comm_split(cartComm, color, mWorldRank, &middleColComm);
}

void ParallelHeatSolver::deinitGridTopology() {
    /**********************************************************************************************************************/
    /*      Deinitialize 2D grid topology and the middle column average temperature computation
     * communicator              */
    /**********************************************************************************************************************/
    MPI_Comm_free(&this->cartComm);
}

void ParallelHeatSolver::initDataDistribution() {
    /**********************************************************************************************************************/
    /*                 Initialize variables and MPI datatypes for data distribution (float and int).
     */
    /**********************************************************************************************************************/
    localTileX = this->transferTileX + this->haloZoneSize * 2;
    localTileY = this->transferTileY + this->haloZoneSize * 2;

    int dims[2] = {(int)this->globalTileX, (int)this->globalTileY};
    int tileDims[2] = {(int)transferTileX, (int)transferTileY};
    int start[2] = {0, 0};
    MPI_Datatype dumbTileInt;
    MPI_Datatype dumbTileFloat;
    MPI_Type_create_subarray(2, dims, tileDims, start, MPI_ORDER_C, MPI_INT, &dumbTileInt);
    MPI_Type_create_subarray(2, dims, tileDims, start, MPI_ORDER_C, MPI_FLOAT, &dumbTileFloat);

    MPI_Type_create_resized(dumbTileInt, 0, sizeof(int), &transferTileInt);
    MPI_Type_create_resized(dumbTileFloat, 0, sizeof(float), &transferTileFloat);

    MPI_Type_commit(&transferTileInt);
    MPI_Type_commit(&transferTileFloat);
}

void ParallelHeatSolver::deinitDataDistribution() {
    /**********************************************************************************************************************/
    /*                       Deinitialize variables and MPI datatypes for data distribution. */
    /**********************************************************************************************************************/
    MPI_Type_free(&transferTileInt);
    MPI_Type_free(&transferTileFloat);
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
    temperatureBufferLocal.resize(2);
    temperatureBufferLocal[0] = std::vector<float>(localTileSize);
    temperatureBufferLocal[1] = std::vector<float>(localTileSize);
}

void ParallelHeatSolver::deallocLocalTiles() {
    /**********************************************************************************************************************/
    /*                                   Deallocate local tiles (may be empty). */
    /**********************************************************************************************************************/
    // TODO: mlem
}

void ParallelHeatSolver::initHaloExchange() {
    /**********************************************************************************************************************/
    /*                            Initialize variables and MPI datatypes for halo exchange. */
    /*                    If mSimulationProps.isRunParallelRMA() flag is set to true, create RMA
     * windows.                 */
    /**********************************************************************************************************************/

    MPI_Datatype verticalHaloDumbFloat;
    MPI_Datatype verticalHaloDumbInt;

    //      localTileX
    // |---------------------|
    // |      tranfserTileX  |
    // |      |---------|    |
    // v      v        v     v
    // xx xx hv hv hv hv xx xx
    // xx xx hv hv hv hv xx xx
    // hh hh ll ll ll ll hh hh
    // hh hh ll ll ll ll hh hh
    // hh hh ll ll ll ll hh hh
    // hh hh ll ll ll ll hh hh
    // xx xx hv hv hv hv xx xx
    // xx xx hv hv hv hv xx xx
    MPI_Type_vector(haloZoneSize, transferTileX, localTileX, MPI_FLOAT, &haloZoneHorizontal);
    MPI_Type_vector(transferTileY, haloZoneSize, localTileX, MPI_FLOAT, &verticalHaloDumbFloat);

    MPI_Type_create_resized(verticalHaloDumbFloat, 0, sizeof(float), &haloZoneVertical);

    MPI_Type_commit(&haloZoneVertical);
    MPI_Type_commit(&haloZoneHorizontal);

    MPI_Aint windowSize = localTileY * localTileX * sizeof(float);
    MPI_Win_create(temperatureBufferLocal[0].data(), windowSize, sizeof(float), MPI_INFO_NULL,
                   cartComm, &window);
}

void ParallelHeatSolver::deinitHaloExchange() {
    /**********************************************************************************************************************/
    /*                            Deinitialize variables and MPI datatypes for halo exchange. */
    /**********************************************************************************************************************/
    MPI_Type_free(&haloZoneHorizontal);
    MPI_Type_free(&haloZoneVertical);
}

template <typename T> void ParallelHeatSolver::scatterTiles(const T *globalData, T *localData) {
    static_assert(std::is_same_v<T, int> || std::is_same_v<T, float>,
                  "Unsupported scatter datatype!");

    /**********************************************************************************************************************/
    /*                      Implement master's global tile scatter to each rank's local tile. */
    /*     The template T parameter is restricted to int or float type. You can choose the correct
     * MPI datatype like:     */
    /*                                                                                                                    */
    /*  const MPI_Datatype globalTileType = std::is_same_v<T, int> ? globalFloatTileType :
     * globalIntTileType;             */
    /*  const MPI_Datatype localTileType  = std::is_same_v<T, int> ? localIntTileType    :
     * localfloatTileType;            */
    /**********************************************************************************************************************/
    const MPI_Datatype tileType = std::is_same_v<T, int> ? transferTileInt : transferTileFloat;

    // TODO: root 0?
    std::vector<int> sendCounts(mWorldSize);
    std::vector<int> displs(mWorldSize);
    int coords[2] = {};

    if (mWorldRank == 0) {
        for (int i = 0; i < mWorldSize; i++) {
            sendCounts[i] = 1;
            MPI_Cart_coords(cartComm, i, 2, coords);
            displs[i] = (coords[1] * transferTileY) * globalTileX + coords[0] * transferTileX;
        }
    }

    // sendCounts has to offseted by halo zones
    MPI_Scatterv(globalData, sendCounts.data(), displs.data(), tileType,
                 &localData[localTileX * haloZoneSize + haloZoneSize], 1, tileType, 0, cartComm);
}

template <typename T> void ParallelHeatSolver::gatherTiles(const T *localData, T *globalData) {
    static_assert(std::is_same_v<T, int> || std::is_same_v<T, float>,
                  "Unsupported gather datatype!");

    /**********************************************************************************************************************/
    /*                      Implement each rank's local tile gather to master's rank global tile. */
    /*     The template T parameter is restricted to int or float type. You can choose the correct
     * MPI datatype like:     */
    /*                                                                                                                    */
    /*  const MPI_Datatype localTileType  = std::is_same_v<T, int> ? localIntTileType    :
     * localfloatTileType;            */
    /*  const MPI_Datatype globalTileType = std::is_same_v<T, int> ? globalFloatTileType :
     * globalIntTileType;             */
    /**********************************************************************************************************************/
    const MPI_Datatype tileType = std::is_same_v<T, int> ? transferTileInt : transferTileFloat;

    std::vector<int> recvCounts(mWorldSize);
    std::vector<int> displs(mWorldSize);
    int coords[2] = {};

    for (int i = 0; i < mWorldSize; i++) {
        recvCounts[i] = 1;
        MPI_Cart_coords(cartComm, i, 2, coords);
        displs[i] = (coords[1] * transferTileY) * globalTileX + coords[0] * transferTileX;
    }

    MPI_Gatherv(&localData[localTileX * haloZoneSize + haloZoneSize], 1, tileType, globalData,
                recvCounts.data(), displs.data(), tileType, 0, cartComm);
}

void ParallelHeatSolver::computeHaloZones(const float *oldTemp, float *newTemp) {
    /**********************************************************************************************************************/
    /*  Compute new temperatures in halo zones, so that copy operations can be overlapped with inner
     * region computation.  */
    /*                        Use updateTile method to compute new temperatures in halo zones. */
    /*                             TAKE CARE NOT TO COMPUTE THE SAME AREAS TWICE */
    /**********************************************************************************************************************/

    updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
               haloZoneSize, haloZoneSize, transferTileX, haloZoneSize, localTileX);

    updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
               haloZoneSize, haloZoneSize * 2, haloZoneSize, transferTileY - haloZoneSize,
               localTileX);

    updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
               localTileX - 2 * haloZoneSize, 2 * haloZoneSize, haloZoneSize,
               transferTileX - haloZoneSize, localTileX);

    updateTile(oldTemp, newTemp, materialPropertiesLocal.data(), materialTypesLocal.data(),
               2 * haloZoneSize, localTileY - 2 * haloZoneSize, transferTileX - 2 * haloZoneSize,
               haloZoneSize, localTileX);
}

void ParallelHeatSolver::startHaloExchangeP2P(float *localData,
                                              std::array<MPI_Request, 8> &requests) {
    /**********************************************************************************************************************/
    /*                       Start the non-blocking halo zones exchange using P2P communication. */
    /*                         Use the requests array to return the requests from the function. */
    /*                            Don't forget to set the empty requests to MPI_REQUEST_NULL. */
    /**********************************************************************************************************************/
    int rankSrc;

    MPI_Request *requestsPtr = requests.data();

    // 0 = right
    // 1 = up
    // 2 = left
    // 3 = down

    MPI_Datatype haloZoneTypes[] = {haloZoneVertical, haloZoneHorizontal, haloZoneVertical,
                                    haloZoneHorizontal};

    size_t recvOffsets[] = {2 * localTileX + (localTileX - haloZoneSize), haloZoneSize,
                            2 * localTileX, (localTileY - 2) * localTileX + haloZoneSize};
    size_t sendOffsets[] = {2 * localTileX + transferTileX, 2 * localTileX + haloZoneSize,
                            2 * localTileX + haloZoneSize,
                            (transferTileY - 2 * haloZoneSize) * localTileX + haloZoneSize};

    int dstRanks[4] = {};

    MPI_Cart_shift(cartComm, 0, 1, &rankSrc, &dstRanks[0]);
    MPI_Cart_shift(cartComm, 1, 1, &rankSrc, &dstRanks[1]);
    MPI_Cart_shift(cartComm, 0, -1, &rankSrc, &dstRanks[2]);
    MPI_Cart_shift(cartComm, 1, -1, &rankSrc, &dstRanks[3]);

    for (int i = 0; i < 4; i++) {
        auto haloZoneType = haloZoneTypes[i];
        auto recvOffset = recvOffsets[i];
        auto sendOffset = sendOffsets[i];
        auto dstRank = dstRanks[i];

        if (dstRank == MPI_PROC_NULL) {
            *(requestsPtr++) = MPI_REQUEST_NULL;
            *(requestsPtr++) = MPI_REQUEST_NULL;
        } else {
            MPI_Isend(localData + sendOffset, 1, haloZoneType, dstRank, 0, cartComm, requestsPtr++);
            MPI_Irecv(localData + recvOffset, 1, haloZoneType, dstRank, 0, cartComm, requestsPtr++);
        }
    }
}

void ParallelHeatSolver::startHaloExchangeRMA(float *localData, MPI_Win window) {
    /**********************************************************************************************************************/
    /*                       Start the non-blocking halo zones exchange using RMA communication. */
    /*                   Do not forget that you put/get the values to/from the target's opposite
     * side                     */
    /**********************************************************************************************************************/
    MPI_Win_fence(0, window);
    MPI_Datatype haloZoneTypes[] = {haloZoneVertical, haloZoneHorizontal, haloZoneVertical,
                                    haloZoneHorizontal};
    int rankSrc;

    size_t recvOffsets[] = {2 * localTileX + (localTileX - haloZoneSize), haloZoneSize,
                            2 * localTileX, (localTileY - 2) * localTileX + haloZoneSize};
    size_t sendOffsets[] = {2 * localTileX + transferTileX, 2 * localTileX + haloZoneSize,
                            2 * localTileX + haloZoneSize,
                            (transferTileY - 2 * haloZoneSize) * localTileX + haloZoneSize};

    int dstRanks[4] = {};

    MPI_Cart_shift(cartComm, 0, 1, &rankSrc, &dstRanks[0]);
    MPI_Cart_shift(cartComm, 1, 1, &rankSrc, &dstRanks[1]);
    MPI_Cart_shift(cartComm, 0, -1, &rankSrc, &dstRanks[2]);
    MPI_Cart_shift(cartComm, 1, -1, &rankSrc, &dstRanks[3]);

    for (int i = 0; i < 4; i++) {
        if (dstRanks[i] != MPI_PROC_NULL) {
            MPI_Put(localData + sendOffsets[i], 1, haloZoneTypes[i], dstRanks[i], recvOffsets[i], 1,
                    haloZoneTypes[i], window);
        }
    }
}

void ParallelHeatSolver::awaitHaloExchangeP2P(std::array<MPI_Request, 8> &requests) {
    /**********************************************************************************************************************/
    /*                       Wait for all halo zone exchanges to finalize using P2P communication.
     */
    /**********************************************************************************************************************/

    MPI_Status statuses[8] = {};
    MPI_Waitall(8, requests.data(), statuses);
}

void ParallelHeatSolver::awaitHaloExchangeRMA(MPI_Win window) {
    /**********************************************************************************************************************/
    /*                       Wait for all halo zone exchanges to finalize using RMA communication.
     */
    /**********************************************************************************************************************/
    MPI_Win_fence(0, window);
}

void ParallelHeatSolver::run(std::vector<float, AlignedAllocator<float>> &outResult) {
    std::array<MPI_Request, 8> requestsP2P{};

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
            startHaloExchangeRMA(temperatureBufferLocal[newIdx].data(), window);
        }

        /**********************************************************************************************************************/
        /*                           Compute the rest of the tile. Use updateTile method. */
        /**********************************************************************************************************************/

        updateTile(temperatureBufferLocal[oldIdx].data(), temperatureBufferLocal[newIdx].data(),
                   materialPropertiesLocal.data(), materialTypesLocal.data(), 2 * haloZoneSize,
                   2 * haloZoneSize, transferTileX - 2 * haloZoneSize,
                   transferTileY - 2 * haloZoneSize, localTileX);

        /**********************************************************************************************************************/
        /*                            Wait for all halo zone exchanges to finalize. */
        /**********************************************************************************************************************/

        awaitHaloExchangeP2P(requestsP2P);

        if (shouldStoreData(iter)) {
            /**********************************************************************************************************************/
            /*                          Store the data into the output file using parallel or
             * sequential IO.                      */
            /**********************************************************************************************************************/
            if (mSimulationProps.useParallelIO()) {
                storeDataIntoFileParallel(mFileHandle, iter, temperatureBufferLocal[newIdx].data());
            } else {
                storeDataIntoFileSequential(mFileHandle, iter,
                                            temperatureBufferLocal[newIdx].data());
            }
        }

        if (shouldPrintProgress(iter) && shouldComputeMiddleColumnAverageTemperature()) {
            /**********************************************************************************************************************/
            /*                 Compute and print middle column average temperature and print
             * progress report.                     */
            /**********************************************************************************************************************/
        }
    }

    const std::size_t resIdx =
        mSimulationProps.getNumIterations() % 2; // Index of the buffer with final temperatures

    /**********************************************************************************************************************/
    /*                                     Gather final domain temperature. */
    /**********************************************************************************************************************/

    gatherTiles<float>(temperatureBufferLocal[resIdx].data(), outResult.data());

    /**********************************************************************************************************************/
    /*           Compute (sequentially) and report final middle column temperature average and print
     * final report.        */
    /**********************************************************************************************************************/
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

    (void)localData;
    return 0.f;
}

float ParallelHeatSolver::computeMiddleColumnAverageTemperatureSequential(
    const float *globalData) const {
    /**********************************************************************************************************************/
    /*                  Implement sequential middle column average temperature computation. */
    /*                      Use OpenMP directives to accelerate the local computations. */
    /**********************************************************************************************************************/

    (void)globalData;
    return 0.f;
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
    Hdf5PropertyListHandle faplHandle{};

    /**********************************************************************************************************************/
    /*                          Open output HDF5 file for parallel access with alignment. */
    /*      Set up faplHandle to use MPI-IO and alignment. The handle will automatically release the
     * resource.            */
    /**********************************************************************************************************************/

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
        /*               Note that the X and Y coordinates are swapped (but data not altered). */
        /**********************************************************************************************************************/

        // Create new dataspace and dataset using it.
        static constexpr std::string_view dataSetName{"Temperature"};

        Hdf5PropertyListHandle datasetPropListHandle{};

        /**********************************************************************************************************************/
        /*                            Create dataset property list to set up chunking. */
        /*                Set up chunking for collective write operation in datasetPropListHandle
         * variable.                   */
        /**********************************************************************************************************************/

        Hdf5DataspaceHandle dataSpaceHandle(H5Screate_simple(2, gridSize.data(), nullptr));
        Hdf5DatasetHandle dataSetHandle(H5Dcreate(groupHandle, dataSetName.data(), H5T_NATIVE_FLOAT,
                                                  dataSpaceHandle, H5P_DEFAULT,
                                                  datasetPropListHandle, H5P_DEFAULT));

        Hdf5DataspaceHandle memSpaceHandle{};

        /**********************************************************************************************************************/
        /*                Create memory dataspace representing tile in the memory (set up
         * memSpaceHandle).                    */
        /**********************************************************************************************************************/

        /**********************************************************************************************************************/
        /*              Select inner part of the tile in memory and matching part of the dataset in
         * the file                  */
        /*                           (given by position of the tile in global domain). */
        /**********************************************************************************************************************/

        Hdf5PropertyListHandle propListHandle{};

        /**********************************************************************************************************************/
        /*              Perform collective write operation, writting tiles from all processes at
         * once.                        */
        /*                                   Set up the propListHandle variable. */
        /**********************************************************************************************************************/

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

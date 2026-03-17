cmake_minimum_required(VERSION 3.14)
project(orbslam3_native CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3")

if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
  set(CMAKE_INSTALL_PREFIX "${CMAKE_SOURCE_DIR}/result" CACHE PATH "" FORCE)
endif()

# Ensure the binary can find ORB-SLAM3 libraries at runtime
if(ORBSLAM3_DIR)
  set(CMAKE_INSTALL_RPATH "${ORBSLAM3_DIR}/lib")
  set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)
endif()

# ORB-SLAM3 installed prefix (pass -DORBSLAM3_DIR=<path>)
if(NOT ORBSLAM3_DIR)
  message(FATAL_ERROR "ORBSLAM3_DIR not set. Pass -DORBSLAM3_DIR=<path to installed orb-slam3>")
endif()

# dimos-lcm C++ message headers (for dimos_native_module.hpp dependencies)
include(FetchContent)
FetchContent_Declare(dimos_lcm
  GIT_REPOSITORY https://github.com/dimensionalOS/dimos-lcm.git
  GIT_TAG main
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(dimos_lcm)

# dimos_native_module.hpp location
if(NOT DIMOS_NATIVE_MODULE_DIR)
  set(DIMOS_NATIVE_MODULE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../../../hardware/sensors/lidar/common)
endif()

# Dependencies
find_package(OpenCV REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(Pangolin REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LCM REQUIRED lcm)

# Compile-time default vocab path (set by nix flake)
if(ORBSLAM3_DEFAULT_VOCAB)
  add_compile_definitions(ORBSLAM3_DEFAULT_VOCAB="${ORBSLAM3_DEFAULT_VOCAB}")
endif()

add_executable(orbslam3_native main.cpp)

target_include_directories(orbslam3_native PRIVATE
  ${ORBSLAM3_DIR}                    # for deps/DBoW2/... deps/g2o/...
  ${ORBSLAM3_DIR}/include            # for System.h etc.
  ${ORBSLAM3_DIR}/include/CameraModels
  ${ORBSLAM3_DIR}/deps/Sophus        # for sophus/se3.hpp etc.
  ${dimos_lcm_SOURCE_DIR}/generated/cpp_lcm_msgs
  ${DIMOS_NATIVE_MODULE_DIR}
  ${LCM_INCLUDE_DIRS}
  ${EIGEN3_INCLUDE_DIR}
  ${OpenCV_INCLUDE_DIRS}
)

target_link_libraries(orbslam3_native PRIVATE
  ${ORBSLAM3_DIR}/lib/liborb-slam3.so
  ${ORBSLAM3_DIR}/lib/libDBoW2.so
  ${ORBSLAM3_DIR}/lib/libg2o.so
  ${OpenCV_LIBS}
  ${Pangolin_LIBRARIES}
  ${LCM_LIBRARIES}
  -lboost_serialization
  -lcrypto
)

target_link_directories(orbslam3_native PRIVATE
  ${LCM_LIBRARY_DIRS}
)

install(TARGETS orbslam3_native DESTINATION bin)

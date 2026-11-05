cmake_minimum_required(VERSION 3.16)
project(coder_agent_cpp CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Debug)
endif()
add_compile_options(-g -O0 -Wall -Wextra)

# ---- yaml-cpp (vendored, third_party/yaml-cpp) ------------------------------
file(GLOB YAMLCPP_SRC third_party/yaml-cpp/src/*.cpp)
add_library(yaml-cpp STATIC ${YAMLCPP_SRC})
target_include_directories(yaml-cpp PUBLIC third_party/yaml-cpp/include)
target_compile_options(yaml-cpp PRIVATE -w)  # silence warnings in vendored code

# ---- core library -----------------------------------------------------------
file(GLOB CORE_SRC core/*.cpp)
add_library(core STATIC ${CORE_SRC})
target_include_directories(core PUBLIC ${CMAKE_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/third_party)
target_link_libraries(core PUBLIC yaml-cpp)

# ---- unit tests -------------------------------------------------------------
find_package(Threads REQUIRED)
enable_testing()
file(GLOB TEST_SRC tests/*.cpp)
foreach(t ${TEST_SRC})
  get_filename_component(name ${t} NAME_WE)
  add_executable(${name} ${t})
  target_link_libraries(${name} core Threads::Threads)
  add_test(NAME ${name} COMMAND ${name})
endforeach()

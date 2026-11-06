---
name: cmake
description: Create or modify CMake build files
keywords: cmake, CMakeLists, build system, target_link_libraries, find_package, 构建系统
globs: CMakeLists.txt,*.cmake
effort: medium
---
Task type: CMake.
- Use modern target-based CMake: add_library / add_executable, target_include_directories, target_link_libraries with PUBLIC/PRIVATE/INTERFACE.
- Set cmake_minimum_required and the C++ standard via CMAKE_CXX_STANDARD or target_compile_features; no global include_directories / link_libraries.
- Keep each module's CMakeLists.txt in its own directory and add it with add_subdirectory.
- Use out-of-source builds in build/ (cmake -S . -B build; cmake --build build -j).
- Do not hard-code absolute paths. Enable tests with enable_testing() / add_test when tests exist.

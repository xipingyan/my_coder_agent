---
name: cpp_impl
description: Implement or extend C++ code
keywords: c++, cpp, class, template, header, implement, 实现, 新增, 添加, 函数, 类
globs: *.cpp,*.cc,*.cxx,*.h,*.hpp
effort: medium
---
Task type: C++ implementation.
- Follow the project's existing C++ standard, naming and formatting; check CMakeLists.txt for the standard in use.
- Put declarations in headers and definitions in sources; keep headers light (forward declare, avoid heavy includes).
- Prefer RAII, std::unique_ptr / std::shared_ptr, std::string_view, std::span; no raw owning pointers, no using-namespace in headers.
- Mark const / noexcept / override where appropriate; pass large objects by const reference.
- Register new source files in the owning CMakeLists.txt.
- Build with cmake after the change and fix all warnings you introduced.
- If the build or the program fails: follow the `debug-cpp` skill (suspects first, one at a time, gdb after two misses). No trial and error.

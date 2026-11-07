---
name: cpp_debug
description: Diagnose and fix C++ build errors, crashes and wrong behaviour
keywords: crash, segfault, bug, error, undefined reference, 编译错误, 崩溃, 报错, 调试, 链接, link, sanitizer, 内存泄漏
effort: high
---
Task type: C++ debugging.
- Reproduce first: run the failing build or test and read the FIRST error, not the last.
- Find the root cause before editing; do not silence errors or add workarounds.
- Typical causes: missing include / link library in CMake, ODR violations, dangling references, uninitialised data, signed/unsigned mixups, lifetime of temporaries.
- For crashes consider building with -fsanitize=address,undefined in a separate build directory.
- Change one thing at a time and rebuild. Report the root cause in the final summary.
- Follow the `debug-cpp` skill strictly: write 2-3 suspects first, verify them one at a time (max 2 attempts each, revert what did not help), and switch to `debug_backtrace` (gdb) after two failed suspects, for any crash/hang, or when you cannot name a suspect.

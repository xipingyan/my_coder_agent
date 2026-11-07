---
name: debug-cpp
description: Use when a build, test or run of code you just wrote fails (compile/link error, crash, hang, wrong output). Disciplined debugging - list suspects from the new code, verify one by one, escalate to gdb. Read this BEFORE trying fixes.
triggers: error:|undefined reference|segmentation fault|core dumped|assertion|terminate called|AddressSanitizer|runtime error|stack smashing|fatal|aborted
---
# Debugging newly written code (C/C++)

Do NOT try random fixes. Follow these steps in order.

## 1. Read the failure
- Take the FIRST error (compiler cascades), note file:line and the exact message.
- Classify it: compile error / link error / crash (signal) / hang / wrong output / test failure.

## 2. List suspects BEFORE touching anything
Write 2-3 suspected causes in your reply, most likely first. Base them on the code you just wrote, not on guesses.
Use this exact format:
- Suspect 1: <cause> - evidence: <error text / line> - check: <one cheap check>
- Suspect 2: ...
Typical suspects: missing #include or declaration; missing source/library in CMakeLists.txt; signature mismatch between header and source; wrong namespace; unused/uninitialised variable; out-of-range index; dangling pointer/reference; null dereference; use-after-free; integer overflow; wrong loop bound; missing return.

## 3. Verify ONE suspect at a time
- Read the exact lines involved (read_file) and compare with the error.
- Make the smallest change for that suspect, rebuild, and look at the result.
- A suspect is DONE when its check passed or failed. Say which. Never apply two unrelated changes at once.
- Maximum 2 attempts per suspect. If it did not help, REVERT that change and go to the next suspect.

## 4. Escalate to gdb - do not keep guessing
Use the debugger when ANY of these is true:
- two suspects were checked and the problem remains;
- the program crashes (segfault, abort, "terminate called", sanitizer report), hangs, or prints wrong results while compiling cleanly;
- you cannot name a concrete suspect.

How (stdin is closed, so always batch mode):
- Rebuild with debug info and no optimisation: `cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug` (or add `-g -O0`).
- Use the `debug_backtrace` tool: program=`build/app`, optional args, optional `breakpoint` (e.g. `file.cpp:42` or a function name).
- Or by hand: `gdb -batch -nx -ex run -ex bt -ex "info args" -ex "info locals" --args ./build/app arg1`
- Hang: the tool interrupts after the timeout and shows where every thread is stuck.
- Memory errors alternative: build with `-fsanitize=address,undefined -g -O0` and run; the report names the line.

## 5. Read the backtrace
- Find the FIRST frame that is in YOUR code (not libc / std::).
- Look at its arguments and locals: null pointer? index out of range? moved-from object?
- State the new suspect from the evidence, then go back to step 3.

## 6. Finish
Once fixed: rebuild from clean, rerun the original command, and report in the final summary: root cause, the fix, and how you verified it.

---
name: windows-linux-scripts
description: Use when writing or fixing launch/test/release scripts that must run on BOTH Windows (cmd.exe .bat) and Linux (bash .sh), or when a .bat works in Git Bash but fails in cmd with cryptic errors like "X was unexpected at this time." Cross-platform script pitfalls and the safe .bat subset.
triggers: was unexpected at this time|start_gui.bat|\.bat|\.sh|cmd\.exe|Git Bash|line terminators|CRLF
---
# Cross-platform scripts: Windows .bat vs Linux .sh

This project keeps a `.sh` + `.bat` pair for every entry point (start_gui, run_tests, release).
They are NOT interchangeable: cmd.exe's batch parser is a 1980s line-based parser with real bugs.
Writing one and translating the other is where the failures come from.

## Golden rules for .bat files

1. **CRLF line endings, always.** cmd.exe mis-parses LF-only .bat files: lines get swallowed
   (e.g. a `cd gui` disappears, so electron starts in the repo root and dies with
   "Unable to find Electron app at ..."). Enforce via `.gitattributes`:
   ```
   *.bat text eol=crlf
   *.cmd text eol=crlf
   ```
   Note: an existing checkout does NOT auto-convert after the rule is added; re-checkout
   (`git rm --cached <file> && git checkout -- <file>`) or convert bytes manually.
   Verify with a byte check (grep tools often normalize CR), not by eye.

2. **Never use multi-line `if (...) (...)` blocks.** cmd drops quotes inside the block and
   leaves the `(` unclosed; a `-` from an echo line inside the block then surfaces as:
   `- was unexpected at this time.` (reproduced with a 4-line CRLF-clean file). Use labels:
   ```bat
   if not exist "dist\main\index.js" goto :build_missing
   ...
   :build_missing
   echo [start_gui] build output missing
   exit /b 1
   ```
   Single-line `if cond (echo ...)` is fine.

3. **Keep the rest of the .bat to the safe subset:**
   - `cd /d "%~dp0"` to anchor the script's own dir (bash: `cd "$(dirname "$0")"`).
   - `call npm run ...` (the `call` is required, otherwise the batch never returns).
   - `if errorlevel 1` after external commands; bash equivalent is just the next command / `set -e`.
   - Pass args with `%*` (bash: `"$@"` — note quoting of args with spaces differs).
   - `exit /b N` (bash: `exit N`).
   - Avoid: `&`/`&&` chains inside parens, `for /f`, delayed expansion `!var!`,
     `echo` lines containing `(`, `)`, `&`, `|`, `>` — they all re-enter the parser.

4. **Run the .bat from the RIGHT directory in tests.** Some parse errors only appear when the
   expected files exist (the parser "continues" past a broken block and hits the next line).
   Bisect by running the first half / second half of the script in its real working dir.

## bash-side pitfalls (the mirror image)

- `set -euo pipefail` has no .bat equivalent; mirror it with explicit `if errorlevel` checks.
- `exec cmd args "$@"` (bash) vs `cmd args %*` (bat): bat does not quote `%*` — args with
  spaces break; document that or parse `--root` manually.
- Windows paths: use backslashes in .bat, forward slashes in .sh; node/electron accept both,
  so prefer `/` in both when the target is a JS tool.
- Linux-only extras (nvm sourcing, `.desktop` icon entries, `--no-sandbox`) belong in the .sh
  only; guard with `uname` if shared.
- Electron on Windows: `--no-sandbox` is unnecessary; `--disable-gpu` keeps CDP smoke tests
  stable. The "Unable to find Electron app at ..." dialog means the CWD is not the app dir —
  pass an absolute path (`"%CD%"`), never a bare `.`.

## Verification checklist

- [ ] `git ls-files --eol <file>` shows `i/crlf w/crlf` (or byte-count CRLF == line count).
- [ ] No multi-line `if (...)` blocks remain in the .bat.
- [ ] Ran the .bat under real `cmd.exe` (spawn `cmd.exe /c script.bat`), not Git Bash.
- [ ] Ran the .sh under bash with `sh -n` / `bash -n` syntax check.
- [ ] Both scripts produce the same observable behavior (build ok, app starts, same exit codes).

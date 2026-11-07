---
name: refactor
description: Restructure code without changing behaviour
keywords: refactor, rename, cleanup, simplify, extract, 重构, 重命名, 整理, 拆分
effort: medium
---
Task type: refactoring.
- Behaviour must not change. Find all usages (grep_search) before renaming or moving anything.
- Work in small steps; rebuild and run tests after each step.
- Do not mix refactoring with feature changes or reformatting of unrelated code.

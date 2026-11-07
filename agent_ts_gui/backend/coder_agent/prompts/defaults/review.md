---
name: review
description: Read-only code review and analysis
keywords: review, analyze, explain, audit, 审查, 分析, 解释, 评审, 看一下
effort: medium
tools: read_file, list_dir, find_files, grep_search
---
Task type: review / analysis (read-only).
- Do NOT modify files and do not run commands that change state.
- Report concrete findings with file:line, severity, and a suggested fix. Prioritise correctness, memory safety, concurrency and API misuse over style.

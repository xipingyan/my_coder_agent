"""Build the backend as a standalone executable (PyInstaller, one-file).

Usage (from anywhere):
    python backend/build_standalone.py            # outputs backend/dist/<name>
    python backend/build_standalone.py OUTDIR     # outputs OUTDIR/<name>

The executable prints ``WS_URL=ws://127.0.0.1:<port>`` on stdout when ready,
exactly like ``python -m coder_agent.server``. The GUI main process spawns it
from ``resources/backend/`` in a packaged app (see gui/src/main/index.ts).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "coder-agent-server"


def main() -> None:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist"
    outdir.mkdir(parents=True, exist_ok=True)

    # package data shipped with the agent (prompts, defaults, skills);
    # PyInstaller uses ':' as the data separator on POSIX and os.pathsep on Windows
    sep = os.pathsep
    data_args = []
    for sub in ("prompts/defaults", "defaults", "skills"):
        data_args += ["--add-data", f"{HERE / 'coder_agent' / sub}{sep}coder_agent/{sub}"]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--name", NAME,
        "--distpath", str(outdir),
        "--workpath", str(outdir / "build"),
        "--specpath", str(outdir),
        *data_args,
        "--hidden-import", "coder_agent",
        str(HERE / "_standalone_entry.py"),
    ]
    print("running:", " ".join(cmd))
    subprocess.check_call(cmd)
    print(f"ok: {outdir / (NAME + '.exe' if sys.platform == 'win32' else NAME)}")


if __name__ == "__main__":
    main()

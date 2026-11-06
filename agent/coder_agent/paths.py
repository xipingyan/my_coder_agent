"""Well-known locations.

* user dir   (``~/.coder_agent``): config shared by every project
  (config.toml, approval.toml, prompts/, skills/, memory/).
* project cache (``<root>/.agent``): sessions, snapshots, project-level overrides.
* ``<root>/.github``: instructions / skills that belong to the repository.
"""

from __future__ import annotations

import os
from pathlib import Path


def user_dir() -> Path:
    env = os.environ.get("CODER_AGENT_HOME")
    return Path(env).expanduser() if env else Path.home() / ".coder_agent"


def project_cache_dir(root: Path) -> Path:
    return Path(root) / ".agent"


def github_dir(root: Path) -> Path:
    return Path(root) / ".github"

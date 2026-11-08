"""PyInstaller entry point: run the WS server as part of the `coder_agent`
package (server.py uses relative imports, so it cannot be the entry script)."""

import sys

from coder_agent.server import run

sys.exit(run(sys.argv[1:]))

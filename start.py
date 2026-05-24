import os
import sys
import logging

# Databricks Apps may start the process with cwd outside the Git checkout; pin to this file's tree.
_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

import uvicorn

port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
print(f"Starting FastAPI on port {port}", flush=True)
uvicorn.run("backend.main:app", host="0.0.0.0", port=port, log_level="info")

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

import uvicorn

port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
print(f"Starting FastAPI on port {port}", flush=True)
uvicorn.run("backend.main:app", host="0.0.0.0", port=port, log_level="info")

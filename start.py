import os
import uvicorn

port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
print(f"Starting FastAPI on port {port}")
uvicorn.run("backend.main:app", host="0.0.0.0", port=port)

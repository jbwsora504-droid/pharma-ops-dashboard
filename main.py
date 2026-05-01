"""
main.py
-------
Entry point for the Pharma Ops Dashboard.
Loads environment config and starts the FastAPI server.
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

import uvicorn
from src.dashboard.routes import app

if __name__ == "__main__":
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", 8000))
    uvicorn.run(app, host=host, port=port, log_level="info")

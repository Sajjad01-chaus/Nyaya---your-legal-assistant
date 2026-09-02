#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Set env vars BEFORE importing settings
os.environ["DATABASE_URL"] = "postgresql://nyaya_legal_assistant_db_user:Y9LsEPiKefUlQf8LofQQPnhLfyXpUnxc@dpg-dabv87u7bikc73ed1jg0-a/nyaya_legal_assistant_db"
os.environ["QDRANT_URL"] = "https://e7bb7e7d-2b96-42c8-9ade-a2d3427c2b87.us-east-1-1.aws.cloud.qdrant.io"
os.environ["QDRANT_API_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6YTljOTM5ZDYtOGI0OC00ODFhLTgxNjktMDI4YzhiOWQyMGE1In0.8XZdAElkoRpTS3b0xIYcamjkyZBtD9I4udNXsOgCNMU"

BACKEND = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import settings

print(f"DATABASE_URL_OVERRIDE: {settings.database_url_override}")
print(f"DATABASE_URL (computed): {settings.database_url}")
print(f"QDRANT_URL: {settings.qdrant_url}")
print(f"QDRANT_API_KEY: {settings.qdrant_api_key[:20]}...")

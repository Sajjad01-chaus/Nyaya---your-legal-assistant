#!/usr/bin/env python
import asyncio
from qdrant_client import AsyncQdrantClient

async def test():
    try:
        client = AsyncQdrantClient(
            url="https://e7bb7e7d-2b96-42c8-9ade-a2d3427c2b87.us-east-1-1.aws.cloud.qdrant.io",
            api_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6YTljOTM5ZDYtOGI0OC00ODFhLTgxNjktMDI4YzhiOWQyMGE1In0.8XZdAElkoRpTS3b0xIYcamjkyZBtD9I4udNXsOgCNMU",
            timeout=10
        )

        collections = await client.get_collections()
        print(f"[OK] Qdrant connected! Collections: {len(collections.collections)}")
        for col in collections.collections:
            count = await client.count(col.name)
            print(f"   - {col.name}: {count.count} vectors")

        await client.close()
        return 0
    except Exception as e:
        print(f"[ERROR] Qdrant connection failed: {type(e).__name__}: {e}")
        return 1

asyncio.run(test())

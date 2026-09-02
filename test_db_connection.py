#!/usr/bin/env python
import asyncio
import asyncpg

async def test():
    try:
        conn = await asyncpg.connect(
            host="dpg-dabv87u7bikc73ed1jg0-a.c.db.onrender.com",
            port=5432,
            user="nyaya_legal_assistant_db_user",
            password="Y9LsEPiKefUlQf8LofQQPnhLfyXpUnxc",
            database="nyaya_legal_assistant_db",
            ssl="require"
        )
        result = await conn.fetchval("SELECT 1")
        print(f"✅ Connection successful! Got: {result}")
        await conn.close()
        return 0
    except Exception as e:
        print(f"❌ Connection failed: {type(e).__name__}: {e}")
        return 1

asyncio.run(test())

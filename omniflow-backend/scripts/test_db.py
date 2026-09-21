import asyncio
import asyncpg

async def test():
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="omniflow",
            password="omniflow_dev_secret_change_me",
            database="omniflow_db",
        )
        print("✅ asyncpg connected!")
        version = await conn.fetchval("SELECT version()")
        print(f"   PostgreSQL: {version[:50]}")
        await conn.close()
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")

asyncio.run(test())

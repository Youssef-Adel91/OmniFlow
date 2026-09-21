"""Quick Postgres connectivity test."""
import asyncio
import asyncpg


async def test():
    configs = [
        {"host": "localhost", "port": 5432, "database": "omniflow_db",
         "user": "omniflow", "password": "omniflow_dev_secret_change_me"},
        {"host": "localhost", "port": 6432, "database": "omniflow_db",
         "user": "omniflow", "password": "omniflow_dev_secret_change_me"},
    ]
    for cfg in configs:
        try:
            conn = await asyncpg.connect(
                host=cfg["host"], port=cfg["port"],
                database=cfg["database"], user=cfg["user"],
                password=cfg["password"], ssl="disable", timeout=5,
            )
            rows = await conn.fetch("SELECT email, is_active FROM tenant_users LIMIT 5")
            await conn.close()
            print(f"[OK] Connected on port {cfg['port']}: {[dict(r) for r in rows]}")
        except Exception as e:
            print(f"[FAIL] Port {cfg['port']}: {e}")


asyncio.run(test())

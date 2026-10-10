"""Container healthcheck for Kafka consumer workers: `python -m src.shared.kafka.healthcheck <WorkerClassName>`.

Exit 0 when the worker refreshed its Redis heartbeat (`hb:worker:<Class>`, written every ~15 s, TTL 60 s) — i.e. its event
loop is alive and polling — otherwise exit 1. Process-liveness checks (is PID 1 still python?) cannot see a wedged consumer.
"""
from __future__ import annotations

import asyncio
import sys


async def _alive(name: str) -> bool:
    from src.shared.redis_client import redis_mgr

    await redis_mgr.start()
    try:
        return bool(await redis_mgr.get_raw(f"hb:worker:{name}"))
    finally:
        await redis_mgr.stop()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m src.shared.kafka.healthcheck <WorkerClassName>", file=sys.stderr)
        return 2
    try:
        return 0 if asyncio.run(asyncio.wait_for(_alive(argv[1]), timeout=8)) else 1
    except Exception:  # noqa: BLE001 - any failure to answer = unhealthy
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

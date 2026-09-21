"""Manually fix qdrant_point_id and investigate dispatcher outbound message state."""
import asyncio
import asyncpg
import httpx
import json


async def fix_and_verify():
    conn = await asyncpg.connect(
        host="localhost", port=6432,
        database="omniflow_db", user="omniflow",
        password="omniflow_dev_secret_change_me",
        ssl="disable", timeout=10,
    )

    # Fix 1: Set qdrant_point_id for the newly created villa listing
    listing_id = "fb344664-7ae4-470d-b120-57f3abd7d3aa"
    print(f"=== Fixing qdrant_point_id for listing {listing_id} ===")
    await conn.execute(
        "UPDATE property_listings SET qdrant_point_id = $1 WHERE listing_id = $2",
        listing_id, listing_id,
    )
    row = await conn.fetchrow(
        "SELECT listing_id, qdrant_point_id, property_type, city, district FROM property_listings WHERE listing_id = $1",
        listing_id,
    )
    print(f"  After fix: {dict(row)}")

    # Check all messages table
    print("\n=== Messages table — all records ===")
    rows = await conn.fetch("""
        SELECT m.message_id, m.sender_type, m.message_type,
               LEFT(m.text_content, 100) as text_preview,
               m.s3_media_url, m.llm_routing_tier,
               cu.unified_phone, m.created_at
        FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        JOIN customers cu    ON cu.customer_id    = c.customer_id
        ORDER BY m.created_at DESC
        LIMIT 20
    """)
    for r in rows:
        print(dict(r))

    # Check Qdrant collections via REST
    print("\n=== Qdrant collections check ===")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "http://localhost:6333/collections",
            headers={"api-key": "dev_qdrant_api_key_placeholder"},
            timeout=5,
        )
        print(f"  Status: {r.status_code}")
        collections = r.json().get("result", {}).get("collections", [])
        for c in collections:
            print(f"  Collection: {c['name']}")
            # List points
            r2 = await client.post(
                f"http://localhost:6333/collections/{c['name']}/points/scroll",
                headers={"api-key": "dev_qdrant_api_key_placeholder", "Content-Type": "application/json"},
                json={"limit": 3, "with_payload": True, "with_vector": False},
                timeout=5,
            )
            pts = r2.json().get("result", {}).get("points", [])
            print(f"    Points count (sample): {len(pts)}")
            for pt in pts[:2]:
                print(f"    Point: {pt.get('id')} | payload keys: {list(pt.get('payload', {}).keys())}")

    await conn.close()


asyncio.run(fix_and_verify())

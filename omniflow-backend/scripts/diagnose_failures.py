"""
Deep diagnostic script for the 3 E2E failures:
1. qdrant_point_id not populated (vector sync issue)
2. No ai_bot message in messages table
3. MinIO bucket missing
"""
import asyncio
import asyncpg
import boto3
import json
from botocore.exceptions import ClientError


async def investigate():
    # ── PgBouncer connection ──────────────────────────────────────────────────
    conn = await asyncpg.connect(
        host="localhost", port=6432,
        database="omniflow_db", user="omniflow",
        password="omniflow_dev_secret_change_me",
        ssl="disable", timeout=10,
    )

    print("\n=== 1. Latest property_listings (check qdrant_point_id) ===")
    rows = await conn.fetch("""
        SELECT listing_id, property_type, city, district, qdrant_point_id, created_at
        FROM property_listings
        ORDER BY created_at DESC
        LIMIT 5
    """)
    for r in rows:
        print(dict(r))

    print("\n=== 2. All messages (sender_type = ai_bot) ===")
    rows = await conn.fetch("""
        SELECT m.message_id, m.sender_type, m.message_type,
               m.text_content, m.s3_media_url, m.llm_routing_tier,
               m.created_at,
               cu.unified_phone
        FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        JOIN customers cu    ON cu.customer_id    = c.customer_id
        WHERE m.sender_type = 'ai_bot'
        ORDER BY m.created_at DESC
        LIMIT 5
    """)
    for r in rows:
        print(dict(r))

    print("\n=== 3. All messages for phone 966555777888 ===")
    rows = await conn.fetch("""
        SELECT m.message_id, m.sender_type, m.message_type,
               m.text_content, m.s3_media_url, m.created_at
        FROM messages m
        JOIN conversations c ON c.conversation_id = m.conversation_id
        JOIN customers cu    ON cu.customer_id    = c.customer_id
        WHERE cu.unified_phone LIKE '%966555777888%'
        ORDER BY m.created_at DESC
        LIMIT 10
    """)
    for r in rows:
        print(dict(r))

    print("\n=== 4. Customers table - recent records ===")
    rows = await conn.fetch("""
        SELECT customer_id, unified_phone, display_name, created_at
        FROM customers
        ORDER BY created_at DESC
        LIMIT 5
    """)
    for r in rows:
        print(dict(r))

    print("\n=== 5. Vector sync: check backend logs via DB ===")
    # Check if Qdrant collection exists
    print("Checking Qdrant via asyncpg (collection name from tenant_id)...")
    rows = await conn.fetch("""
        SELECT tenant_id FROM tenants LIMIT 3
    """)
    for r in rows:
        print(f"  tenant_id: {r['tenant_id']}")

    await conn.close()

    # ── MinIO bucket check ─────────────────────────────────────────────────────
    print("\n=== 6. MinIO bucket investigation ===")
    s3 = boto3.client(
        "s3",
        endpoint_url="http://localhost:9020",
        aws_access_key_id="omniflow_admin",
        aws_secret_access_key="minio_dev_secret_change_me",
        region_name="us-east-1",
    )
    try:
        buckets = s3.list_buckets().get("Buckets", [])
        print(f"  All buckets: {[b['Name'] for b in buckets]}")
        
        # Try to create the bucket if it doesn't exist
        bucket_name = "omniflow-media"
        bucket_names = [b["Name"] for b in buckets]
        if bucket_name not in bucket_names:
            print(f"  Bucket '{bucket_name}' not found. Creating...")
            s3.create_bucket(Bucket=bucket_name)
            print(f"  Bucket '{bucket_name}' CREATED!")
        else:
            print(f"  Bucket '{bucket_name}' EXISTS!")
            objs = s3.list_objects_v2(Bucket=bucket_name)
            print(f"  Objects: {[o['Key'] for o in objs.get('Contents', [])]}")
    except Exception as e:
        print(f"  MinIO error: {e}")


asyncio.run(investigate())

-- Reply leg of the load test: time from the customer's webhook message to the SENT AI reply, per conversation, for the test customers
-- (phones starting 96655 created by locustfile.py). Run against the TEST database after the run, as a role allowed to read all tenants.
WITH inbound AS (
  SELECT m.conversation_id, min(m.created_at) AS first_in
  FROM messages m JOIN conversations c USING (conversation_id) JOIN customers cu USING (customer_id)
  WHERE m.sender_type = 'customer' AND cu.display_name = 'Load Test' AND m.created_at > now() - interval '2 hours'
  GROUP BY 1
), reply AS (
  SELECT m.conversation_id, min(m.created_at) AS first_reply
  FROM messages m
  WHERE m.sender_type = 'ai_bot' AND m.delivery_status IN ('SENT','DELIVERED','READ') AND m.created_at > now() - interval '2 hours'
  GROUP BY 1
)
SELECT count(*)                                                                     AS conversations,
       count(reply.first_reply)                                                     AS answered,
       round(100.0 * count(reply.first_reply) / NULLIF(count(*), 0), 1)             AS answered_pct,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY extract(epoch FROM reply.first_reply - inbound.first_in))::numeric, 1) AS p50_seconds,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM reply.first_reply - inbound.first_in))::numeric, 1) AS p95_seconds
FROM inbound LEFT JOIN reply USING (conversation_id);

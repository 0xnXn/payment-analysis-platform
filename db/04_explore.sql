-- Evidence lookup. Your future MCP server must supply the authorised tenant.
SELECT * FROM payment_demo.payments
WHERE tenant_id='demo-alpha' AND payment_id='PAY-1042';

SELECT event_id, event_type, occurred_at, details
FROM payment_demo.payment_events
WHERE tenant_id='demo-alpha' AND payment_id='PAY-1042'
ORDER BY occurred_at, event_id;

-- Retrieval only. RAG is completed later when a model uses these passages.
WITH query AS (SELECT websearch_to_tsquery('english','upstream timeout') AS q)
SELECT runbook_id, version, section_id, title, section_text,
       ts_rank(search_vector, query.q) AS relevance
FROM payment_demo.runbook_sections CROSS JOIN query
WHERE search_vector @@ query.q
ORDER BY relevance DESC, runbook_id, section_id LIMIT 3;

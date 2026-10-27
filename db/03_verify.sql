-- Raises an error if fixtures or retrieval are incorrect.
DO $$
BEGIN
 IF (SELECT count(*) FROM payment_demo.payments
     WHERE payment_id ~ '^PAY-10(4[2-9]|[56][0-9]|7[01])$'
     AND tenant_id IN ('demo-alpha','demo-beta')) <> 30 THEN
   RAISE EXCEPTION 'Expected 30 demo payments';
 END IF;
 IF (SELECT count(*) FROM payment_demo.payment_events
     WHERE event_id ~ '^EVT-10(4[2-9]|[56][0-9]|7[01])-[0-9]{2}$') <> 90 THEN
   RAISE EXCEPTION 'Expected 90 demo events';
 END IF;
 IF (SELECT count(*) FROM payment_demo.runbook_sections WHERE version=1
     AND runbook_id IN ('RB-TIMEOUT','RB-ACK','RB-VALIDATION','RB-DUPLICATE','RB-EVIDENCE')) <> 10 THEN
   RAISE EXCEPTION 'Expected 10 sections across five runbooks';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM payment_demo.payment_events
    WHERE tenant_id='demo-alpha' AND payment_id='PAY-1042'
    AND event_type='UPSTREAM_TIMEOUT') THEN
   RAISE EXCEPTION 'PAY-1042 must have a timeout';
 END IF;
 IF EXISTS (SELECT 1 FROM payment_demo.payments
    WHERE tenant_id='demo-beta' AND payment_id='PAY-1042') THEN
   RAISE EXCEPTION 'Tenant-filtered lookup returned the wrong tenant';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM payment_demo.runbook_sections
    WHERE runbook_id='RB-TIMEOUT'
    AND search_vector @@ websearch_to_tsquery('english','upstream timeout')) THEN
   RAISE EXCEPTION 'Runbook retrieval did not find timeout guidance';
 END IF;
END $$;
SELECT 'Fixture and retrieval checks passed' AS result;
SELECT status, count(*) FROM payment_demo.payments GROUP BY status ORDER BY status;

-- Synthetic fixtures, frozen at 2026-10-16 01:00 UTC (12:00 Sydney).
-- Rerunnable: existing rows are preserved, never deleted or overwritten.
BEGIN;
INSERT INTO payment_demo.tenants VALUES
 ('demo-alpha','Synthetic Alpha'), ('demo-beta','Synthetic Beta')
ON CONFLICT DO NOTHING;

-- 30 payments, six of each scenario. Scenario labels are NOT model evidence.
-- PAY-1042 is the canonical unresolved-timeout example.
INSERT INTO payment_demo.payments
 (tenant_id,payment_id,amount,currency,status,created_at,updated_at)
SELECT CASE WHEN n < 20 THEN 'demo-alpha' ELSE 'demo-beta' END,
       'PAY-' || (1042+n), (12500+n*137)::numeric/100, 'AUD',
       CASE n%5 WHEN 1 THEN 'SETTLED' WHEN 2 THEN 'REJECTED'
                WHEN 3 THEN 'SETTLED' ELSE 'PENDING' END,
       timestamptz '2026-10-16 00:00:00+00',
       timestamptz '2026-10-16 00:00:00+00' +
       CASE n%5 WHEN 0 THEN interval '20 seconds'
                WHEN 1 THEN interval '45 minutes'
                WHEN 2 THEN interval '2 seconds'
                WHEN 3 THEN interval '5 seconds'
                ELSE interval '1 second' END
FROM generate_series(0,29) AS s(n)
ON CONFLICT DO NOTHING;

INSERT INTO payment_demo.payment_events
 (event_id,tenant_id,payment_id,event_type,occurred_at,recorded_at,details)
SELECT 'EVT-' || (1042+n) || '-' || e.suffix,
       CASE WHEN n < 20 THEN 'demo-alpha' ELSE 'demo-beta' END,
       'PAY-' || (1042+n), e.kind,
       timestamptz '2026-10-16 00:00:00+00' + e.offset_time,
       timestamptz '2026-10-16 00:00:00+00' + e.offset_time,
       e.payload
FROM generate_series(0,29) AS s(n)
CROSS JOIN LATERAL (
    VALUES
      ('01','PAYMENT_RECEIVED',interval '0 seconds',
       '{"source":"synthetic-client"}'::jsonb,true),
      ('02','SUBMITTED',interval '1 second',
       '{"destination":"synthetic-bank"}'::jsonb,n%5 IN (0,1,3,4)),
      ('03','UPSTREAM_TIMEOUT',interval '20 seconds',
       '{"error_code":"UPSTREAM_TIMEOUT","message":"No response within 19 seconds; downstream outcome unknown"}'::jsonb,n%5=0),
      ('03','ACK_DELAY_DETECTED',interval '30 minutes',
       '{"message":"No acknowledgement recorded at the monitoring threshold"}'::jsonb,n%5=1),
      ('04','SETTLEMENT_CONFIRMED',interval '45 minutes',
       '{"message":"Late acknowledgement confirms settlement"}'::jsonb,n%5=1),
      ('02','VALIDATION_REJECTED',interval '2 seconds',
       '{"error_code":"MISSING_BENEFICIARY_REFERENCE","field":"beneficiary_reference"}'::jsonb,n%5=2),
      ('03','SETTLEMENT_CONFIRMED',interval '4 seconds',
       '{"upstream_event_id":"synthetic-settlement-1"}'::jsonb,n%5=3),
      ('04','DUPLICATE_ACK_IGNORED',interval '5 seconds',
       '{"upstream_event_id":"synthetic-settlement-1","message":"Duplicate acknowledgement received; no second business operation"}'::jsonb,n%5=3)
) AS e(suffix,kind,offset_time,payload,include_event)
WHERE e.include_event
ON CONFLICT DO NOTHING;

INSERT INTO payment_demo.runbook_sections
 (runbook_id,version,section_id,title,section_text)
VALUES
 ('RB-TIMEOUT',1,'diagnosis','Upstream timeout: unknown payment outcome',
  'A timeout means the caller did not receive a response in time. It does not prove the payment failed. Retrieve the payment event timeline and check for a later acknowledgement or settlement confirmation. If neither is recorded, report the downstream outcome as unknown.'),
 ('RB-TIMEOUT',1,'handling','Upstream timeout: reconciliation and escalation',
  'Preserve payment and correlation identifiers. Request reconciliation with the downstream owner before any resubmission. Do not automatically retry a payment with an unknown outcome. An investigation ticket should identify the timeout event, missing evidence and next check.'),
 ('RB-ACK',1,'diagnosis','Delayed acknowledgement',
  'An acknowledgement may arrive after monitoring has reported a delay. Read the complete timeline before concluding the payment is still pending. A later settlement confirmation resolves the earlier missing-acknowledgement symptom.'),
 ('RB-ACK',1,'handling','Delayed acknowledgement: report current state',
  'If settlement is confirmed, report settled with a delayed acknowledgement and cite both events. If no acknowledgement exists, report the outcome as unknown and request reconciliation. Do not duplicate the payment.'),
 ('RB-VALIDATION',1,'diagnosis','Validation rejection: missing beneficiary reference',
  'MISSING_BENEFICIARY_REFERENCE means the required beneficiary_reference field was absent in this synthetic system. A VALIDATION_REJECTED event establishes rejection. Distinguish validation rejection from an upstream timeout.'),
 ('RB-VALIDATION',1,'handling','Validation rejection: correct input',
  'Ask the authorised operator to check the missing field against the original instruction. Describe the rejected field in the incident draft. Any corrected submission must follow the approved business process; the investigation agent cannot submit payments.'),
 ('RB-DUPLICATE',1,'diagnosis','Duplicate acknowledgement and idempotency',
  'A duplicate acknowledgement is a repeated notification, not evidence of a second payment. Compare upstream event identifiers and look for DUPLICATE_ACK_IGNORED. A prior settlement confirmation followed by an ignored duplicate indicates one recorded settlement.'),
 ('RB-DUPLICATE',1,'handling','Duplicate acknowledgement: avoid duplicate actions',
  'Report the duplicate notification separately from the payment outcome. Cite the original settlement and ignored-duplicate event. Use an idempotency key when creating investigation tickets so repeating an approved request does not create a second ticket.'),
 ('RB-EVIDENCE',1,'diagnosis','Insufficient evidence for pending payment',
  'A payment with only received and submitted events has insufficient evidence to establish success, rejection or a timeout. Absence of a recorded event is not proof that an event did not occur. State what is known and what evidence is missing.'),
 ('RB-EVIDENCE',1,'handling','Insufficient evidence: next investigation step',
  'Request downstream status or additional logs from an authorised operator. Report pending with unknown downstream outcome. Do not invent a root cause. Include the available event identifiers and missing evidence in a proposed investigation ticket.')
ON CONFLICT DO NOTHING;
COMMIT;

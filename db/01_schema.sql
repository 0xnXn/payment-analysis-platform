-- Run in a development database. Everything is isolated in payment_demo.
BEGIN;
CREATE SCHEMA IF NOT EXISTS payment_demo;

CREATE TABLE IF NOT EXISTS payment_demo.tenants (
    tenant_id text PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_demo.payments (
    tenant_id text NOT NULL REFERENCES payment_demo.tenants,
    payment_id text NOT NULL,
    amount numeric(12,2) NOT NULL CHECK (amount > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status text NOT NULL CHECK (status IN ('PENDING','SETTLED','REJECTED')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    PRIMARY KEY (tenant_id, payment_id)
);

CREATE TABLE IF NOT EXISTS payment_demo.payment_events (
    event_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    payment_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL CHECK (recorded_at >= occurred_at),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (tenant_id, payment_id)
        REFERENCES payment_demo.payments (tenant_id, payment_id)
);
CREATE INDEX IF NOT EXISTS payment_events_lookup
    ON payment_demo.payment_events (tenant_id, payment_id, occurred_at, event_id);

-- Shared synthetic operational guidance. No tenant-specific information here.
CREATE TABLE IF NOT EXISTS payment_demo.runbook_sections (
    runbook_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    section_id text NOT NULL,
    title text NOT NULL,
    section_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english'::regconfig, title), 'A') ||
        setweight(to_tsvector('english'::regconfig, section_text), 'B')
    ) STORED,
    PRIMARY KEY (runbook_id, version, section_id)
);
CREATE INDEX IF NOT EXISTS runbook_search
    ON payment_demo.runbook_sections USING gin (search_vector);
COMMIT;

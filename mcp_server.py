"""Local, single-user MCP learning server using the official SDK v2.

Read from top to bottom: configuration -> database -> tools -> resources -> run.
No arbitrary SQL tool; every database operation is a fixed, parameterised query.
"""
import json
import logging
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.rows import dict_row

load_dotenv(Path(__file__).with_name('.env.mcp'),override=True,)
TENANT = os.getenv('DEMO_TENANT', 'demo-alpha')

mcp = MCPServer(
    'Payment investigator',
    instructions='Synthetic payment data. Retrieve events before drawing conclusions. '
    'A timeout alone does not prove failure. Cite event and runbook identifiers.',
)


def connect_database():
    """Create a connection; IAM tokens are generated for EACH new connection.

    Express gateway certificates use public Amazon trust roots. certifi supplies
    those roots to libpq; a custom CA bundle can be supplied via PGSSLROOTCERT.
    The actual endpoint is used for both IAM signing and TLS hostname checking.
    """
    mode = os.getenv('PG_AUTH_MODE', 'password').lower()
    if mode == 'password':
        # Retains the original local Docker workflow when explicitly selected.
        return psycopg.connect(connect_timeout=45, row_factory=dict_row)
    if mode != 'iam':
        raise ValueError('PG_AUTH_MODE must be iam or password.')

    import boto3
    import certifi

    host = os.getenv('PGHOST', '').strip()
    user = os.getenv('PGUSER', '').strip()
    region = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION')
    if not host or not user or not region:
        raise ValueError('IAM requires PGHOST, PGUSER and AWS_REGION.')
    if os.getenv('PGSSLMODE', 'verify-full') != 'verify-full':
        raise ValueError('IAM mode requires PGSSLMODE=verify-full.')
    port = int(os.getenv('PGPORT', '5432'))
    root_cert = os.getenv('PGSSLROOTCERT') or certifi.where()
    root_cert = Path(root_cert).expanduser()
    if not root_cert.is_absolute():
        root_cert = Path(__file__).parent / root_cert
    if not root_cert.is_file():
        raise ValueError('The configured TLS CA bundle does not exist.')
    session = boto3.Session(
        profile_name=os.getenv('AWS_PROFILE') or None,
        region_name=region,
    )
    # Aurora can pause while idle. Allow time to resume; retry only connection
    # timeouts, never authentication/SQL errors. Two 45s attempts fit inside the
    # agent's 120s MCP request timeout, with room for the 5s SQL timeout.
    for attempt in range(2):
        token = session.client('rds').generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user, Region=region,
        )
        try:
            # Never print the token or include it in a connection URI.
            return psycopg.connect(
                host=host, port=port, dbname=os.getenv('PGDATABASE', 'postgres'),
                user=user, password=token, sslmode='verify-full',
                sslrootcert=str(root_cert), connect_timeout=45, row_factory=dict_row,
            )
        except psycopg.errors.ConnectionTimeout:
            if attempt == 1:
                raise ToolError(
                    'Database connection timed out after two attempts. '
                    'Evidence retrieval failed; payment existence and status are unknown.'
                ) from None
            logging.getLogger(__name__).warning(
                'Database connection timed out; retrying once. '
                'This does not establish whether a payment exists.'
            )



def query(sql: str, params: tuple = ()) -> list[dict]:
    """Connect using PG* settings; prevent writes within this transaction."""
    # IAM connection handling lives in connect_database().
    with connect_database() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '5s'")
        rows = conn.execute(sql, params).fetchall()
    # Convert Decimal and timestamps into JSON-compatible strings.
    return json.loads(json.dumps(rows, default=str))


@mcp.tool()
def list_payments(limit: int = 100, after_payment_id: str = '') -> dict:
    """List recorded payments/statuses in the configured tenant, ordered by ID.

    This is a read-only list, not an inferred bank outcome. Follow next_cursor
    with after_payment_id to read another page. No caller-supplied tenant is used.
    """
    if not 1 <= limit <= 100 or len(after_payment_id) > 64:
        raise ToolError('Use limit 1..100 and a cursor of at most 64 characters.')
    rows = query(
        'SELECT payment_id, amount, currency, status, created_at, updated_at '
        'FROM payment_demo.payments WHERE tenant_id=%s AND payment_id>%s '
        'ORDER BY payment_id LIMIT %s', (TENANT, after_payment_id, limit + 1),
    )
    return {'tenant': TENANT, 'payments': rows[:limit],
            'next_cursor': rows[limit-1]['payment_id'] if len(rows) > limit else None}


@mcp.tool()
def get_payment(payment_id: str) -> dict:
    """Look up a synthetic payment by ID in the configured demo tenant.

    Returns found=false when no accessible payment exists. Amount is a decimal
    string and timestamps include timezone offsets.
    """
    rows = query(
        'SELECT payment_id, amount, currency, status, created_at, updated_at '
        'FROM payment_demo.payments WHERE tenant_id=%s AND payment_id=%s',
        (TENANT, payment_id),
    )
    return {'found': bool(rows), 'payment': rows[0] if rows else None}


@mcp.tool()
def get_payment_events(payment_id: str) -> dict:
    """Read a payment's recorded events in time order, scoped to the demo tenant.

    Use event IDs as evidence references. An empty list is not proof of an
    outcome; the payment may be absent or outside the configured tenant.
    """
    rows = query(
        'SELECT event_id, event_type, occurred_at, recorded_at, details '
        'FROM payment_demo.payment_events WHERE tenant_id=%s AND payment_id=%s '
        'ORDER BY occurred_at, event_id LIMIT 100', (TENANT, payment_id),
    )
    return {'payment_id': payment_id, 'events': rows}


@mcp.tool()
def search_runbooks(query_text: str, limit: int = 3) -> dict:
    """Find shared synthetic runbook passages using English full-text search.

    Supply symptom words such as 'upstream timeout'. Returns source IDs,
    versions and passages for citation. No model or embeddings are used here.
    """
    if not query_text.strip() or len(query_text) > 500:
        raise ToolError('Provide a search query between 1 and 500 characters.')
    if not 1 <= limit <= 10:
        raise ToolError('limit must be between 1 and 10.')
    rows = query(
        "WITH q AS (SELECT websearch_to_tsquery('english', %s) AS term) "
        'SELECT runbook_id, version, section_id, title, section_text, '
        'ts_rank(search_vector,q.term) AS relevance '
        'FROM payment_demo.runbook_sections CROSS JOIN q '
        'WHERE search_vector @@ q.term '
        'ORDER BY relevance DESC,runbook_id,version,section_id LIMIT %s',
        (query_text, limit),
    )
    return {'passages': rows}


@mcp.resource('payment-demo://guide')
def data_guide() -> str:
    """Describe this dataset and its interpretation for a client to read."""
    return (
        'Synthetic fixtures as of 2026-10-16 01:00 UTC. '
        'Tables: payments, payment_events, runbook_sections in payment_demo. '
        'Payments have amount/currency/status and timestamps. Events have IDs, '
        'types, timestamps and JSON details. Runbook sections have document ID, '
        'version, section ID and text. Payment tools enforce the configured '
        'demo tenant. Runbooks are shared guidance. Timeout does not prove '
        'failure. This local server does not implement multi-user authentication.'
    )


@mcp.resource('runbook://{runbook_id}/{version}')
def read_runbook(runbook_id: str, version: int) -> str:
    """Read all sections of a shared runbook at a specific version."""
    rows = query(
        'SELECT runbook_id,version,section_id,title,section_text '
        'FROM payment_demo.runbook_sections WHERE runbook_id=%s AND version=%s '
        'ORDER BY section_id', (runbook_id, version),
    )
    if not rows:
        raise ToolError('Runbook version not found.')
    return json.dumps(rows, indent=2)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Local payment MCP server')
    parser.add_argument('--check-db', action='store_true',
                        help='Check the configured DB and tools, then exit.')
    args = parser.parse_args()
    if args.check_db:
        with connect_database() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            print('Database:', conn.info.dbname, '| User:', conn.info.user)
            print('TLS active:', bool(conn.pgconn.ssl_in_use))
            print('Authentication mode:', os.getenv('PG_AUTH_MODE', 'password'))
            print('Counts:', conn.execute(
                'SELECT (SELECT count(*) FROM payment_demo.tenants) AS tenants, '
                '(SELECT count(*) FROM payment_demo.payments) AS payments, '
                '(SELECT count(*) FROM payment_demo.payment_events) AS events, '
                '(SELECT count(*) FROM payment_demo.runbook_sections) AS sections'
            ).fetchone())
        payment = get_payment('PAY-1042')
        events = get_payment_events('PAY-1042')
        passages = search_runbooks('upstream timeout')['passages']
        if not payment['found'] or not events['events'] or not passages:
            raise SystemExit('Connection works, but expected synthetic fixtures are missing.')
        print('Payment/event/search tools passed against the configured database.')
    else:
        mcp.run(transport='streamable-http', host='0.0.0.0', port=8000)
        

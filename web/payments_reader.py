"""Read tenant-scoped payment pages from MCP, without a model call."""
from datetime import datetime, timezone, timedelta
import json
import os
from urllib.parse import quote

async def read_payments():
    from mcp import ClientSession
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    region = os.environ['AWS_REGION']
    arn = os.getenv('MCP_RUNTIME_ARN')
    url = (f'https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(arn, safe="")}/invocations?qualifier=DEFAULT' if arn else os.environ['MCP_URL'])
    if not url.startswith('https://'):
        raise RuntimeError('Hosted payment listing requires an HTTPS MCP endpoint.')
    payments = []
    tenant = None
    cursor = ''
    async with aws_iam_streamablehttp_client(endpoint=url, aws_service='bedrock-agentcore',
        aws_region=region, aws_profile=os.getenv('AWS_PROFILE') or None, timeout=120, terminate_on_close=False) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as client:
            await client.initialize()
            for _ in range(10):
                result = await client.call_tool('list_payments', {'limit': 100, 'after_payment_id': cursor})
                if result.isError:
                    raise RuntimeError('MCP payment listing failed')
                data = getattr(result, 'structuredContent', None)
                if not isinstance(data, dict):
                    text = next((c.text for c in result.content if getattr(c, 'type', '') == 'text'), '')
                    data = json.loads(text)
                tenant = data['tenant']
                payments.extend(data['payments'])
                next_cursor = data.get('next_cursor')
                if not next_cursor:
                    return {'payments': payments, 'tenant': tenant, 'as_of': datetime.now(timezone.utc).isoformat()}
                if next_cursor == cursor:
                    raise RuntimeError('MCP returned a repeated pagination cursor')
                cursor = next_cursor
    raise RuntimeError('Payment list exceeded the 1,000-row read limit')


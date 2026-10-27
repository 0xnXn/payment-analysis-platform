"""AWS HTTP API + SQS worker. Workspace password is fetched from encrypted SSM."""
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import asyncio
import base64
import hmac
import json
import logging
import os
import time
from uuid import UUID, uuid4
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from markdown_it import MarkdownIt

TENANT_ID = os.getenv('TENANT_ID', 'demo-alpha')
WORKSPACE_NAME = os.getenv('WORKSPACE_NAME', 'Alpha Operations')
log = logging.getLogger(__name__)
table = boto3.resource('dynamodb').Table(os.environ['JOBS_TABLE'])
sqs = boto3.client('sqs')
markdown = MarkdownIt('commonmark', {'html': False}).enable('table').enable('strikethrough')


def response(status, data):
    return {'statusCode': status, 'headers': {'content-type': 'application/json', 'cache-control': 'no-store'},
            'body': json.dumps(data, default=float)}


@lru_cache(maxsize=1)
def workspace_password():
    return boto3.client('ssm').get_parameter(
        Name=os.environ['ACCESS_PARAMETER'], WithDecryption=True,
    )['Parameter']['Value']


def api_handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('rawPath', '')
    if method == 'GET' and path == '/api/health':
        return response(200, {'status': 'ready', 'region': os.environ['AWS_REGION'], 'mode': 'async', 'auth_required': True})
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    credential = headers.get('authorization', '')
    expected = 'Bearer ' + workspace_password()
    if not hmac.compare_digest(credential.encode(), expected.encode()):
        return response(401, {'detail': 'Your workspace session has ended. Enter your access code to continue.'})
    if method == 'GET' and path == '/api/access':
        return response(200, {'status': 'ready', 'workspace': {
            'id': TENANT_ID, 'name': WORKSPACE_NAME, 'access': 'read_only',
            'environment': 'Test', 'region': os.environ['AWS_REGION'],
            'report_retention_hours': 24}})
    if method == 'GET' and path == '/api/payments':
        job_id = str(uuid4())
        table.put_item(Item={'job_id': job_id, 'kind': 'payments', 'tenant_id': TENANT_ID, 'status': 'queued',
            'created_at': datetime.now(timezone.utc).isoformat(), 'expires_at': int(time.time()) + 86400})
        try:
            sqs.send_message(QueueUrl=os.environ['QUEUE_URL'], MessageBody=json.dumps({'job_id': job_id}))
        except Exception:
            log.exception('Unable to queue payment list %s', job_id)
            return response(503, {'detail': 'Payment records could not be requested. Please refresh.'})
        return response(202, {'job_id': job_id, 'status': 'queued'})
    if method == 'POST' and path == '/api/investigations':
        if headers.get('content-type', '').split(';')[0] != 'application/json':
            return response(415, {'detail': 'Send the question as JSON.'})
        raw = event.get('body') or '{}'
        try:
            if event.get('isBase64Encoded'):
                raw = base64.b64decode(raw).decode()
            if len(raw) > 65000:
                raise ValueError()
            body = json.loads(raw)
            prompt = body.get('prompt')
            if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 10000:
                raise ValueError()
            # A browser can retry a submission with the same ID without rerunning it.
            job_id = str(UUID(body['request_id'])) if body.get('request_id') else str(uuid4())
        except (ValueError, TypeError, AttributeError):
            return response(422, {'detail': 'Use a question between 1 and 10,000 characters and a valid request ID.'})
        created = datetime.now(timezone.utc).isoformat()
        item = {'job_id': job_id, 'tenant_id': TENANT_ID, 'status': 'queued', 'prompt': prompt.strip(),
                'created_at': created, 'expires_at': int(time.time()) + 86400}
        try:
            table.put_item(Item=item, ConditionExpression='attribute_not_exists(job_id)')
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                existing = table.get_item(Key={'job_id': job_id}, ConsistentRead=True).get('Item')
                if not existing or existing.get('tenant_id') != TENANT_ID:
                    return response(404, {'detail': 'Investigation not found.'})
                return response(202, {'job_id': job_id, 'status': 'accepted'})
            raise
        try:
            sqs.send_message(QueueUrl=os.environ['QUEUE_URL'], MessageBody=json.dumps({'job_id': job_id}))
        except Exception:
            table.update_item(Key={'job_id': job_id}, UpdateExpression='SET #s=:s, detail=:d',
                ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'failed', ':d': 'The investigation could not be queued. Start a new investigation.'})
            log.exception('Unable to enqueue job %s', job_id)
            return response(503, {'detail': 'The investigation could not be queued. Try a new investigation.'})
        return response(202, {'job_id': job_id, 'status': 'queued'})
    if method == 'GET' and path.startswith('/api/investigations/'):
        try:
            job_id = str(UUID(event.get('pathParameters', {}).get('job_id', '')))
        except ValueError:
            return response(404, {'detail': 'Investigation not found.'})
        item = table.get_item(Key={'job_id': job_id}, ConsistentRead=True).get('Item')
        if not item or item.get('tenant_id') != TENANT_ID or int(item['expires_at']) <= time.time():
            return response(404, {'detail': 'Investigation not found or expired. Reports are retained for 24 hours.'})
        data = {k: v for k, v in item.items() if k != 'expires_at'}
        if item['status'] == 'completed' and item.get('kind') != 'payments':
            data['answer_html'] = markdown.render(item['answer'])
        elif item['status'] == 'running' and time.time() - int(item.get('started_at', time.time())) > 420:
            data.update(status='failed', detail='The investigation exceeded its time limit. Check AgentCore logs before retrying.')
        return response(200, data)
    return response(404, {'detail': 'Endpoint not found.'})


def invoke_runtime(payload, session_id):
    client = boto3.client('bedrock-agentcore', config=Config(connect_timeout=10, read_timeout=300, retries={'total_max_attempts': 1}))
    result = client.invoke_agent_runtime(agentRuntimeArn=os.environ['AGENT_RUNTIME_ARN'], qualifier='DEFAULT',
        runtimeSessionId=session_id, contentType='application/json', accept='application/json', payload=json.dumps(payload).encode())
    stream = result['response']
    try:
        raw = stream.read(2_000_001)
    finally:
        stream.close()
    if len(raw) > 2_000_000:
        raise ValueError('Response too large')
    return json.loads(raw)


def worker_handler(event, context):
    failures = []
    for record in event['Records']:
        job_id = json.loads(record['body'])['job_id']
        try:
            # SQS is at-least-once. Claim once to avoid duplicate agent invocations.
            table.update_item(Key={'job_id': job_id}, UpdateExpression='SET #s=:running, started_at=:t',
                ConditionExpression='#s=:queued AND tenant_id=:tenant', ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':running': 'running', ':queued': 'queued', ':tenant': TENANT_ID, ':t': int(time.time())})
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                continue
            failures.append({'itemIdentifier': record['messageId']})
            continue
        started = time.perf_counter()
        item = {}
        try:
            item = table.get_item(Key={'job_id': job_id}, ConsistentRead=True)['Item']
            if item.get('kind') == 'payments':
                listing = invoke_runtime({'action': 'list_payments'}, job_id)
                if listing.get('tenant') != TENANT_ID:
                    raise ValueError('MCP workspace does not match the configured tenant')
                if len(json.dumps(listing).encode()) > 200000:
                    raise ValueError('Payment list is too large to store')
                table.update_item(Key={'job_id': job_id}, UpdateExpression='SET #s=:s, payments=:p, tenant=:t, as_of=:a',
                    ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'completed',
                        ':p': listing['payments'], ':t': listing['tenant'], ':a': listing['as_of']})
                continue
            answer = invoke_runtime({'prompt': item['prompt']}, job_id).get('answer')
            if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > 200000:
                raise ValueError('Agent response was empty or too large')
            table.update_item(Key={'job_id': job_id}, UpdateExpression='SET #s=:s, answer=:a, elapsed_seconds=:e, request_id=:r, session_id=:r',
                ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'completed', ':a': answer.strip(), ':e': str(round(time.perf_counter() - started, 2)), ':r': job_id})
        except Exception:
            log.exception('Agent investigation failed for job %s', job_id)
            try:
                table.update_item(Key={'job_id': job_id}, UpdateExpression='SET #s=:s, detail=:d',
                    ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'failed', ':d': ('Payment records could not be loaded. Check MCP logs. Reference: ' if item.get('kind') == 'payments' else 'The agent could not complete this investigation. Check AgentCore logs. Reference: ') + job_id})
            except Exception:
                failures.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failures}

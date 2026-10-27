"""Local web backend: browser -> FastAPI -> deployed AgentCore HTTP runtime."""
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ProfileNotFound, ReadTimeoutError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / '.env.web')
log = logging.getLogger('payment_web')
markdown = MarkdownIt('commonmark', {'html': False}).enable('table').enable('strikethrough')
busy = Lock()
app = FastAPI(title='Payment investigation desk', docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=10000)

    @field_validator('prompt')
    @classmethod
    def trim_prompt(cls, value):
        if not value.strip():
            raise ValueError('Enter a question before starting an investigation.')
        return value.strip()


@app.middleware('http')
async def local_request_boundary(request: Request, call_next):
    if request.method == 'POST':
        origin = request.headers.get('origin')
        allowed_origins = {f'http://{request.headers.get("host")}', f'https://{request.headers.get("host")}'}
        if origin and origin not in allowed_origins or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            return JSONResponse({'detail': 'Send the question as JSON.'}, status_code=415)
    response = await call_next(request)
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


@lru_cache(maxsize=1)
def runtime_client():
    session = boto3.Session(
        profile_name=os.getenv('AWS_PROFILE') or None,
        region_name=os.getenv('AWS_REGION', 'ap-southeast-2'),
    )
    # Avoid automatically repeating a billable model investigation after a timeout.
    return session.client('bedrock-agentcore', config=Config(
        connect_timeout=10, read_timeout=300, retries={'total_max_attempts': 1},
    ))


@app.get('/api/access')
def workspace_context():
    return {'status': 'ready', 'workspace': {'id': 'demo-alpha', 'name': 'Alpha Operations',
        'access': 'read_only', 'environment': 'Local', 'region': os.getenv('AWS_REGION', 'ap-southeast-2'),
        'report_retention_hours': 24}}


@app.get('/api/health')
def health():
    return {'status': 'ready' if os.getenv('AGENT_RUNTIME_ARN') else 'configuration_needed',
            'region': os.getenv('AWS_REGION', 'ap-southeast-2')}


def invoke_agent(prompt, session_id):
    arn = os.getenv('AGENT_RUNTIME_ARN', '').strip()
    if not arn:
        raise HTTPException(503, 'Set AGENT_RUNTIME_ARN in .env.web and restart the backend.')
    response = runtime_client().invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        qualifier=os.getenv('AGENT_RUNTIME_QUALIFIER', 'DEFAULT'),
        contentType='application/json',
        accept='application/json',
        payload=json.dumps({'prompt': prompt}).encode('utf-8'),
    )
    body = response['response']
    try:
        raw = body.read(2_000_001)
    finally:
        body.close()
    if len(raw) > 2_000_000:
        raise ValueError('Agent response exceeded the display limit.')
    data = json.loads(raw)
    answer = data.get('answer') if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Agent did not return a non-empty answer.')
    return answer.strip()


@app.post('/api/investigations')
def investigate(body: InvestigationRequest):
    if not busy.acquire(blocking=False):
        raise HTTPException(429, 'An investigation is already running. Wait for it to finish.')
    session_id = str(uuid4())
    request_id = str(uuid4())
    started = perf_counter()
    try:
        answer = invoke_agent(body.prompt, session_id)
        return {
            'answer': answer,
            # Raw HTML is disabled; Markdown links also use markdown-it URL validation.
            'answer_html': markdown.render(answer),
            'request_id': request_id,
            'session_id': session_id,
            'elapsed_seconds': round(perf_counter() - started, 2),
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except (NoCredentialsError, ProfileNotFound):
        raise HTTPException(503, 'AWS credentials are unavailable. Sign in to your backend AWS profile and retry.') from None
    except ReadTimeoutError:
        raise HTTPException(504, 'The agent did not respond within five minutes. Check AgentCore logs before retrying; it may still be running.') from None
    except ClientError as error:
        code = error.response.get('Error', {}).get('Code', '')
        log.warning('AgentCore request %s failed: %s', request_id, code)
        if code in {'AccessDeniedException', 'AccessDenied', 'UnauthorizedException'}:
            raise HTTPException(502, 'The backend cannot invoke the agent. Check IAM access to the agent runtime and its endpoint.') from None
        if code in {'ExpiredTokenException', 'ExpiredToken', 'UnrecognizedClientException'}:
            raise HTTPException(503, 'Your backend AWS session has expired. Sign in to your AWS profile again.') from None
        raise HTTPException(502, f'AgentCore could not complete the request. Check its logs. Reference: {request_id}') from None
    except (BotoCoreError, ValueError, KeyError):
        log.exception('Agent response failed; reference %s', request_id)
        raise HTTPException(502, f'No usable answer was received from the agent. Check AgentCore logs. Reference: {request_id}') from None
    finally:
        busy.release()


@app.get('/api/payments')
def payments():
    import asyncio
    from web.payments_reader import read_payments
    try:
        return asyncio.run(read_payments())
    except Exception:
        log.exception('MCP payments listing failed')
        raise HTTPException(502, 'Payment records could not be loaded. Check the MCP server logs.') from None


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')

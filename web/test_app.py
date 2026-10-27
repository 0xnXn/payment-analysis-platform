"""Check the web/API boundary without incurring inference charges."""
import io
import json
from unittest.mock import Mock

from botocore.exceptions import ClientError, ReadTimeoutError
from fastapi.testclient import TestClient
import pytest

from web import app as module


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME_ARN', 'arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/testAgent-1234567890')
    return TestClient(module.app)


def test_forwards_prompt_and_formats_safe_report(api, monkeypatch):
    report = '## Payment PAY-1042\n\n| Field | Value |\n| --- | --- |\n| Status | PENDING |\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))'
    bodies = []
    def invoke(**kwargs):
        stream = io.BytesIO(json.dumps({'answer': report}).encode())
        bodies.append(stream)
        return {'response': stream}
    client = Mock()
    client.invoke_agent_runtime.side_effect = invoke
    monkeypatch.setattr(module, 'runtime_client', lambda: client)
    response = api.post('/api/investigations', json={'prompt': '  Investigate PAY-1042  '})
    assert response.status_code == 200
    body = response.json()
    assert '<table>' in body['answer_html']
    assert '<script>' not in body['answer_html']
    assert 'href="javascript:' not in body['answer_html']
    assert body['answer'] == report
    assert bodies[0].closed
    args = client.invoke_agent_runtime.call_args.kwargs
    assert json.loads(args['payload']) == {'prompt': 'Investigate PAY-1042'}
    assert len(args['runtimeSessionId']) >= 33
    api.post('/api/investigations', json={'prompt': 'Another investigation'})
    assert client.invoke_agent_runtime.call_args.kwargs['runtimeSessionId'] != args['runtimeSessionId']


@pytest.mark.parametrize('payload', [{'prompt': ' '}, {'prompt': 'x' * 10001}, {'prompt': 123}, {'prompt': 'ok', 'runtime': 'override'}])
def test_rejects_invalid_questions(api, monkeypatch, payload):
    invoke = Mock()
    monkeypatch.setattr(module, 'invoke_agent', invoke)
    assert api.post('/api/investigations', json=payload).status_code == 422
    invoke.assert_not_called()


def test_blocks_cross_site_invocation(api, monkeypatch):
    invoke = Mock()
    monkeypatch.setattr(module, 'invoke_agent', invoke)
    assert api.post('/api/investigations', json={'prompt': 'hello'}, headers={'Origin': 'https://other.example'}).status_code == 403
    invoke.assert_not_called()


def test_sanitizes_aws_errors_and_releases_busy_lock(api, monkeypatch):
    invoke = Mock(side_effect=ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'private AWS detail'}}, 'InvokeAgentRuntime'))
    monkeypatch.setattr(module, 'invoke_agent', invoke)
    response = api.post('/api/investigations', json={'prompt': 'hello'})
    assert response.status_code == 502
    assert 'private AWS detail' not in response.text
    invoke.side_effect = None
    invoke.return_value = 'Ready again'
    assert api.post('/api/investigations', json={'prompt': 'hello'}).status_code == 200


def test_timeout_warns_that_remote_request_may_continue(api, monkeypatch):
    monkeypatch.setattr(module, 'invoke_agent', Mock(side_effect=ReadTimeoutError(endpoint_url='https://example.com')))
    response = api.post('/api/investigations', json={'prompt': 'hello'})
    assert response.status_code == 504
    assert 'may still be running' in response.json()['detail']


def test_invalid_response_closes_stream(api, monkeypatch):
    stream = io.BytesIO(b'{"error":"internal error"}')
    client = Mock()
    client.invoke_agent_runtime.return_value = {'response': stream}
    monkeypatch.setattr(module, 'runtime_client', lambda: client)
    assert api.post('/api/investigations', json={'prompt': 'hello'}).status_code == 502
    assert stream.closed


def test_health_and_frontend_need_no_aws_call(api, monkeypatch):
    client = Mock(side_effect=AssertionError('AWS should not be called'))
    monkeypatch.setattr(module, 'runtime_client', client)
    assert api.get('/api/health').json()['status'] == 'ready'
    response = api.get('/')
    assert response.status_code == 200
    assert 'id="query-form"' in response.text
    assert "script-src 'self'" in response.headers['content-security-policy']

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from botocore.exceptions import ClientError
import pytest


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv('JOBS_TABLE','test-jobs')
    monkeypatch.setenv('TENANT_ID','demo-alpha')
    monkeypatch.setenv('QUEUE_URL','https://sqs.example/test')
    monkeypatch.setenv('ACCESS_PARAMETER','/test/password')
    monkeypatch.setenv('AWS_REGION','ap-southeast-2')
    monkeypatch.setenv('AGENT_RUNTIME_ARN','arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/test-1234567890')
    table=Mock(); queue=Mock(); ssm=Mock(); agent=Mock()
    ssm.get_parameter.return_value={'Parameter':{'Value':'test-workspace-password'}}
    monkeypatch.setattr('boto3.resource',lambda name:Mock(Table=lambda name:table))
    monkeypatch.setattr('boto3.client',lambda name,**kwargs:{'sqs':queue,'ssm':ssm,'bedrock-agentcore':agent}[name])
    spec=importlib.util.spec_from_file_location('test_cloud_handlers',Path(__file__).with_name('handlers.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module,table,queue,agent


def event(method,path,body=None,authenticated=True):
    return {'requestContext':{'http':{'method':method}},'rawPath':path,
        'headers':{'content-type':'application/json',**({'authorization':'Bearer test-workspace-password'} if authenticated else {})},
        'body':json.dumps(body) if body is not None else ''}


def test_unauthenticated_request_cannot_create_job(cloud):
    module,table,queue,_=cloud
    assert module.api_handler(event('POST','/api/investigations',{'prompt':'test'},False),None)['statusCode']==401
    table.put_item.assert_not_called(); queue.send_message.assert_not_called()


def test_job_is_queued_and_submission_deduplicated(cloud):
    module,table,queue,_=cloud
    job=str(uuid4())
    result=module.api_handler(event('POST','/api/investigations',{'prompt':'  PAY-1042  ','request_id':job}),None)
    assert result['statusCode']==202
    assert table.put_item.call_args.kwargs['Item']['prompt']=='PAY-1042'
    assert json.loads(queue.send_message.call_args.kwargs['MessageBody'])=={'job_id':job}
    table.put_item.side_effect=ClientError({'Error':{'Code':'ConditionalCheckFailedException'}},'PutItem')
    queue.reset_mock()
    table.get_item.return_value={'Item':{'tenant_id':'demo-alpha'}}
    assert module.api_handler(event('POST','/api/investigations',{'prompt':'PAY-1042','request_id':job}),None)['statusCode']==202
    queue.send_message.assert_not_called()


@pytest.mark.parametrize('body',[{'prompt':' '},{'prompt':123},{'prompt':'x'*10001},{'prompt':'ok','request_id':'invalid'}])
def test_invalid_jobs_do_not_reach_queue(cloud,body):
    module,table,queue,_=cloud
    assert module.api_handler(event('POST','/api/investigations',body),None)['statusCode']==422
    queue.send_message.assert_not_called()


def test_result_is_escaped_and_expired_reports_are_hidden(cloud):
    module,table,_,_=cloud
    job=str(uuid4()); request=event('GET','/api/investigations/'+job)
    request['pathParameters']={'job_id':job}
    table.get_item.return_value={'Item':{'tenant_id':'demo-alpha','job_id':job,'status':'completed','expires_at':9999999999,'answer':'<script>alert(1)</script>\n\n## Report'}}
    result=json.loads(module.api_handler(request,None)['body'])
    assert '<script>' not in result['answer_html'] and '<h2>Report</h2>' in result['answer_html']
    table.get_item.return_value['Item']['expires_at']=0
    assert module.api_handler(request,None)['statusCode']==404


def test_worker_invokes_agent_and_closes_stream(cloud):
    module,table,_,agent=cloud
    job=str(uuid4()); table.get_item.return_value={'Item':{'tenant_id':'demo-alpha','prompt':'Investigate PAY-1042'}}
    stream=io.BytesIO(b'{"answer":"## Recorded status\\nPENDING"}')
    agent.invoke_agent_runtime.return_value={'response':stream}
    request={'Records':[{'messageId':'message-1','body':json.dumps({'job_id':job})}]}
    assert module.worker_handler(request,None)=={'batchItemFailures':[]}
    kwargs=agent.invoke_agent_runtime.call_args.kwargs
    assert kwargs['runtimeSessionId']==job and json.loads(kwargs['payload'])=={'prompt':'Investigate PAY-1042'}
    assert stream.closed
    assert table.update_item.call_args.kwargs['ExpressionAttributeValues'][':s']=='completed'


def test_duplicate_queue_delivery_does_not_invoke_agent(cloud):
    module,table,_,agent=cloud
    table.update_item.side_effect=ClientError({'Error':{'Code':'ConditionalCheckFailedException'}},'UpdateItem')
    module.worker_handler({'Records':[{'messageId':'m','body':json.dumps({'job_id':str(uuid4())})}]},None)
    agent.invoke_agent_runtime.assert_not_called()


def test_payment_listing_requires_password(cloud):
    module,table,queue,_=cloud
    assert module.api_handler(event('GET','/api/payments',authenticated=False),None)['statusCode']==401
    table.put_item.assert_not_called(); queue.send_message.assert_not_called()
    assert module.api_handler(event('GET','/api/payments'),None)['statusCode']==202
    assert table.put_item.call_args.kwargs['Item']['kind']=='payments'


def test_payment_worker_uses_existing_runtime_data_action(cloud,monkeypatch):
    from unittest.mock import AsyncMock
    module,table,_,agent=cloud
    job=str(uuid4())
    table.get_item.return_value={'Item':{'tenant_id':'demo-alpha','kind':'payments'}}
    listing={'tenant':'demo-alpha','payments':[{'payment_id':'PAY-1042','status':'PENDING'}],'as_of':'2026-10-20T09:00:00+00:00'}
    agent.invoke_agent_runtime.return_value={'response':io.BytesIO(json.dumps(listing).encode())}
    assert module.worker_handler({'Records':[{'messageId':'m','body':json.dumps({'job_id':job})}]},None)=={'batchItemFailures':[]}
    assert json.loads(agent.invoke_agent_runtime.call_args.kwargs['payload'])=={'action':'list_payments'}
    assert table.update_item.call_args.kwargs['ExpressionAttributeValues'][':p']==listing['payments']


def test_payment_job_result_is_not_treated_as_a_report(cloud):
    module,table,_,_=cloud
    job=str(uuid4()); request=event('GET','/api/investigations/'+job)
    request['pathParameters']={'job_id':job}
    table.get_item.return_value={'Item':{'tenant_id':'demo-alpha','job_id':job,'kind':'payments','status':'completed','expires_at':9999999999,'payments':[{'payment_id':'PAY-1042','status':'PENDING'}]}}
    result=module.api_handler(request,None)
    assert result['statusCode']==200
    assert json.loads(result['body'])['payments'][0]['status']=='PENDING'
    assert 'answer_html' not in json.loads(result['body'])


def test_workspace_context_is_authenticated_and_contains_no_credential(cloud):
    module,_,_,_=cloud
    assert module.api_handler(event('GET','/api/access',authenticated=False),None)['statusCode']==401
    result=module.api_handler(event('GET','/api/access'),None)
    workspace=json.loads(result['body'])['workspace']
    assert workspace['id']=='demo-alpha' and workspace['access']=='read_only'
    assert 'test-workspace-password' not in result['body']


def test_job_creation_uses_server_tenant_not_request_tenant(cloud):
    module,table,_,_=cloud
    result=module.api_handler(event('POST','/api/investigations',{'prompt':'PAY-1042','tenant_id':'demo-beta'}),None)
    assert result['statusCode']==202
    assert table.put_item.call_args.kwargs['Item']['tenant_id']=='demo-alpha'


def test_jobs_from_another_tenant_are_not_returned(cloud):
    module,table,_,_=cloud
    job=str(uuid4()); request=event('GET','/api/investigations/'+job)
    request['pathParameters']={'job_id':job}
    table.get_item.return_value={'Item':{'tenant_id':'demo-beta','expires_at':9999999999,'status':'completed','answer':'Other workspace data'}}
    result=module.api_handler(request,None)
    assert result['statusCode']==404 and 'Other workspace data' not in result['body']


def test_mismatched_mcp_tenant_is_not_saved_as_payments(cloud):
    module,table,_,agent=cloud
    job=str(uuid4());table.get_item.return_value={'Item':{'tenant_id':'demo-alpha','kind':'payments'}}
    agent.invoke_agent_runtime.return_value={'response':io.BytesIO(json.dumps({'tenant':'demo-beta','payments':[{'payment_id':'PAY-1062'}]}).encode())}
    module.worker_handler({'Records':[{'messageId':'m','body':json.dumps({'job_id':job})}]},None)
    values=table.update_item.call_args.kwargs['ExpressionAttributeValues']
    assert values[':s']=='failed' and ':p' not in values

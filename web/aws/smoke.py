"""Live deployment check. Password stays in memory and is never printed."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4
import boto3

REGION='ap-southeast-2'
session=boto3.Session(profile_name='personal-dev',region_name=REGION)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('command',choices=['start','poll','health'])
parser.add_argument('--job')
parser.add_argument('--cloudfront',action='store_true')
args=parser.parse_args()
if args.cloudfront:
    endpoint=json.loads(Path(__file__).with_name('deployment.json').read_text())['WebsiteURL']
else:
    resources=session.client('cloudformation').list_stack_resources(StackName='paymentdesk-web')['StackResourceSummaries']
    api=next(x['PhysicalResourceId'] for x in resources if x['LogicalResourceId']=='HttpApi')
    endpoint=f'https://{api}.execute-api.{REGION}.amazonaws.com'

def request(path,method='GET',data=None,authenticated=True):
    headers={'Content-Type':'application/json'}
    if authenticated:
        password=session.client('ssm').get_parameter(Name='/paymentdesk/web/workspace-password',WithDecryption=True)['Parameter']['Value']
        headers['Authorization']='Bearer '+password
    payload=json.dumps(data).encode() if data is not None else None
    req=Request(endpoint+path,data=payload,headers=headers,method=method)
    try:
        with urlopen(req,timeout=30) as response:
            return response.status,json.load(response)
    except HTTPError as error:
        return error.code,json.load(error)

if args.command=='health':
    code,data=request('/api/health',authenticated=False)
    print(json.dumps({'http_status':code,'health':data}))
elif args.command=='start':
    denied,_=request('/api/investigations/'+str(uuid4()),authenticated=False)
    assert denied==401, f'Unauthenticated request returned {denied}'
    job_id=str(uuid4())
    code,data=request('/api/investigations','POST',{'prompt':'Investigate payment PAY-1042. What happened?','request_id':job_id})
    assert code==202,(code,data)
    print(json.dumps({'unauthenticated_status':denied,'submission_status':code,'job_id':data['job_id']}))
elif args.command=='poll':
    code,data=request('/api/investigations/'+args.job)
    result={'http_status':code,'status':data.get('status'),'job_id':args.job}
    if data.get('status')=='completed':
        result.update(elapsed_seconds=data.get('elapsed_seconds'),answer_preview=data['answer'][:220],
            cites_payment='PAY-1042' in data['answer'],cites_event='EVT-1042' in data['answer'],
            cites_runbook='RB-TIMEOUT' in data['answer'],has_formatted_report='<h' in data.get('answer_html',''))
    if data.get('detail'):result['detail']=data['detail']
    print(json.dumps(result))

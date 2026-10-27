"""Deploy the payment desk to the existing personal-dev AWS account."""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import tempfile
import zipfile

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
REGION = 'ap-southeast-2'
STACK = 'paymentdesk-web'
AGENT = 'arn:aws:bedrock-agentcore:ap-southeast-2:142531840386:runtime/paymentagent-iG2aAxFqJ0'
PARAMETER = '/paymentdesk/web/workspace-password'


def session():
    return boto3.Session(profile_name='personal-dev', region_name=REGION)


def package():
    with tempfile.TemporaryDirectory(prefix='paymentdesk-lambda-') as work:
        target = Path(work) / 'package'
        subprocess.run(['uv','pip','install','--quiet','--python-platform','aarch64-manylinux2014',
            '--python-version','3.13','--only-binary=:all:','--target',str(target),
            'boto3>=1.43.103,<2','markdown-it-py>=3,<5'],check=True)
        (target/'handlers.py').write_bytes((ROOT/'handlers.py').read_bytes())
        with zipfile.ZipFile(ROOT/'backend.zip','w',zipfile.ZIP_DEFLATED) as z:
            for source in sorted(target.rglob('*')):
                if source.is_file() and '__pycache__' not in source.parts:
                    item = zipfile.ZipInfo(source.relative_to(target).as_posix())
                    item.create_system = 3
                    item.external_attr = 0o100644 << 16
                    item.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(item,source.read_bytes())
    print('Packaged Lambda code and Linux ARM64/Python 3.13 dependencies.')


def submit():
    s = session()
    account = s.client('sts').get_caller_identity()['Account']
    if account != '142531840386':
        raise RuntimeError('Unexpected AWS account; deployment stopped.')
    cf = s.client('cloudformation')
    template = (ROOT/'template.json').read_text(encoding='utf-8')
    cf.validate_template(TemplateBody=template)
    bucket = f'paymentdesk-artifacts-{account}-{REGION}'
    s3 = s.client('s3')
    try:
        s3.head_bucket(Bucket=bucket,ExpectedBucketOwner=account)
    except ClientError as error:
        if error.response['Error']['Code'] not in ['404','NoSuchBucket','NotFound']:
            raise
        s3.create_bucket(Bucket=bucket,CreateBucketConfiguration={'LocationConstraint':REGION})
        s3.put_public_access_block(Bucket=bucket,PublicAccessBlockConfiguration={k:True for k in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']})
        s3.put_bucket_encryption(Bucket=bucket,ServerSideEncryptionConfiguration={'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]})
    blob = (ROOT/'backend.zip').read_bytes()
    digest = hashlib.sha256(blob).hexdigest()[:20]
    key = f'backend/{digest}.zip'
    s3.put_object(Bucket=bucket,Key=key,Body=blob,ContentType='application/zip',ExpectedBucketOwner=account)
    ssm = s.client('ssm')
    try:
        ssm.get_parameter(Name=PARAMETER)
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name=PARAMETER,Type='SecureString',Value=secrets.token_urlsafe(32),
            Description='Shared payment desk workspace password. Do not embed in frontend files.',Tier='Standard')
    parameters = [{'ParameterKey':k,'ParameterValue':v} for k,v in {
        'CodeBucket':bucket,'CodeKey':key,'AgentRuntimeArn':AGENT,'AccessParameter':PARAMETER}.items()]
    try:
        existing = cf.describe_stacks(StackName=STACK)['Stacks'][0]
    except ClientError as error:
        if 'does not exist' not in error.response['Error']['Message']:
            raise
        existing = None
    args = dict(StackName=STACK,TemplateBody=template,Parameters=parameters,Capabilities=['CAPABILITY_IAM'])
    if existing:
        if existing['StackStatus'].endswith('_IN_PROGRESS'):
            print('Deployment already in progress:',existing['StackStatus'])
            return
        try:
            result = cf.update_stack(**args)
        except ClientError as error:
            if 'No updates are to be performed' not in error.response['Error']['Message']:
                raise
            print('Infrastructure is already current.')
            return
    else:
        result = cf.create_stack(**args,Tags=[{'Key':'Project','Value':'PaymentDesk'}])
    print('CloudFormation deployment submitted:',result['StackId'])


def status():
    cf = session().client('cloudformation')
    stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
    print('Stack status:',stack['StackStatus'])
    for item in cf.describe_stack_events(StackName=STACK)['StackEvents'][:12]:
        print(item['LogicalResourceId'],item['ResourceStatus'],item.get('ResourceStatusReason',''))
    if stack['StackStatus'] in ['CREATE_COMPLETE','UPDATE_COMPLETE']:
        outputs = {x['OutputKey']:x['OutputValue'] for x in stack['Outputs']}
        (ROOT/'deployment.json').write_text(json.dumps(outputs,indent=2),encoding='utf-8')
        print(json.dumps(outputs,indent=2))


def publish():
    outputs = json.loads((ROOT/'deployment.json').read_text(encoding='utf-8'))
    s = session(); s3=s.client('s3')
    static = PROJECT/'web'/'static'
    for source in sorted(static.rglob('*')):
        if not source.is_file(): continue
        relative = source.relative_to(static).as_posix()
        key = 'index.html' if relative=='index.html' else 'static/'+relative
        s3.put_object(Bucket=outputs['FrontendBucket'],Key=key,Body=source.read_bytes(),
            ContentType={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}.get(source.suffix, mimetypes.guess_type(source.name)[0] or 'application/octet-stream'),
            CacheControl='no-cache' if source.suffix in ['.html','.js'] else 'public, max-age=300')
    s.client('cloudfront').create_invalidation(DistributionId=outputs['DistributionId'],
        InvalidationBatch={'Paths':{'Quantity':1,'Items':['/*']},'CallerReference':secrets.token_hex(16)})
    print('Published frontend:',outputs['WebsiteURL'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['package','submit','status','publish'])
    command=parser.parse_args().command
    {'package':package,'submit':submit,'status':status,'publish':publish}[command]()

"""Produce explicit CloudFormation resources; no broad managed IAM policies."""
import json
from pathlib import Path

def ref(name): return {'Ref': name}
def att(name, attr): return {'Fn::GetAtt': [name, attr]}
def sub(value): return {'Fn::Sub': value}
def resource(kind, properties, **extra): return {'Type': kind, 'Properties': properties, **extra}
def statement(actions, resources): return {'Effect': 'Allow', 'Action': actions, 'Resource': resources}
trust = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
resources = {}
r = resources
r['FrontendBucket'] = resource('AWS::S3::Bucket', {
    'PublicAccessBlockConfiguration': {k: True for k in ['BlockPublicAcls','BlockPublicPolicy','IgnorePublicAcls','RestrictPublicBuckets']},
    'BucketEncryption': {'ServerSideEncryptionConfiguration': [{'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
    'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
}, DeletionPolicy='Retain', UpdateReplacePolicy='Retain')
r['JobsTable'] = resource('AWS::DynamoDB::Table', {
    'BillingMode': 'PAY_PER_REQUEST', 'AttributeDefinitions': [{'AttributeName': 'job_id', 'AttributeType': 'S'}],
    'KeySchema': [{'AttributeName': 'job_id', 'KeyType': 'HASH'}],
    'TimeToLiveSpecification': {'AttributeName': 'expires_at', 'Enabled': True}, 'SSESpecification': {'SSEEnabled': True},
})
r['DeadLetterQueue'] = resource('AWS::SQS::Queue', {'MessageRetentionPeriod': 345600, 'SqsManagedSseEnabled': True})
r['JobQueue'] = resource('AWS::SQS::Queue', {'VisibilityTimeout': 2160, 'MessageRetentionPeriod': 86400, 'SqsManagedSseEnabled': True,
    'RedrivePolicy': {'deadLetterTargetArn': att('DeadLetterQueue','Arn'), 'maxReceiveCount': 2}})
for name in ['Api','Worker']:
    r[name+'Logs'] = resource('AWS::Logs::LogGroup', {'LogGroupName': sub('${AWS::StackName}-'+name.lower()).copy(), 'RetentionInDays': 7})
    r[name+'Logs']['Properties']['LogGroupName'] = sub('/aws/lambda/${AWS::StackName}-'+name.lower())
api_permissions = [
    statement(['logs:CreateLogStream','logs:PutLogEvents'], att('ApiLogs','Arn')),
    statement(['dynamodb:GetItem','dynamodb:PutItem','dynamodb:UpdateItem'], att('JobsTable','Arn')),
    statement(['sqs:SendMessage'], att('JobQueue','Arn')),
    statement(['ssm:GetParameter'], sub('arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter${AccessParameter}')),
]
worker_permissions = [
    statement(['logs:CreateLogStream','logs:PutLogEvents'], att('WorkerLogs','Arn')),
    statement(['dynamodb:GetItem','dynamodb:UpdateItem'], att('JobsTable','Arn')),
    statement(['sqs:ReceiveMessage','sqs:DeleteMessage','sqs:GetQueueAttributes'], att('JobQueue','Arn')),
    statement(['bedrock-agentcore:InvokeAgentRuntime'], [ref('AgentRuntimeArn'),sub('${AgentRuntimeArn}/runtime-endpoint/DEFAULT')]),
]
for name, permissions in [('Api',api_permissions),('Worker',worker_permissions)]:
    r[name+'Role'] = resource('AWS::IAM::Role', {'AssumeRolePolicyDocument': trust,
        'Policies': [{'PolicyName': 'ApplicationAccess', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': permissions}}]})
    r[name+'Function'] = resource('AWS::Lambda::Function', {
        'FunctionName': sub('${AWS::StackName}-'+name.lower()), 'Runtime': 'python3.13', 'Architectures': ['arm64'],
        'Handler': 'handlers.'+name.lower()+'_handler', 'MemorySize': 256, 'Timeout': 20 if name=='Api' else 360,
        'Role': att(name+'Role','Arn'), 'Code': {'S3Bucket': ref('CodeBucket'), 'S3Key': ref('CodeKey')},
        'Environment': {'Variables': {'JOBS_TABLE': ref('JobsTable'), 'TENANT_ID': 'demo-alpha', 'WORKSPACE_NAME': 'Alpha Operations', **({'ACCESS_PARAMETER': ref('AccessParameter'), 'QUEUE_URL': ref('JobQueue')} if name=='Api' else {'AGENT_RUNTIME_ARN': ref('AgentRuntimeArn')})}},
    })
r['JobConsumer'] = resource('AWS::Lambda::EventSourceMapping', {'EventSourceArn': att('JobQueue','Arn'), 'FunctionName': ref('WorkerFunction'),
    'BatchSize': 1, 'FunctionResponseTypes': ['ReportBatchItemFailures'], 'ScalingConfig': {'MaximumConcurrency': 2}})
r['HttpApi'] = resource('AWS::ApiGatewayV2::Api', {'Name': sub('${AWS::StackName}-api'), 'ProtocolType': 'HTTP'})
r['ApiIntegration'] = resource('AWS::ApiGatewayV2::Integration', {'ApiId': ref('HttpApi'), 'IntegrationType': 'AWS_PROXY',
    'IntegrationUri': att('ApiFunction','Arn'), 'PayloadFormatVersion': '2.0', 'TimeoutInMillis': 25000})
route_names=[]
for index, route in enumerate(['GET /api/health','GET /api/access','POST /api/investigations','GET /api/investigations/{job_id}','GET /api/payments']):
    name='Route'+str(index); route_names.append(name)
    r[name]=resource('AWS::ApiGatewayV2::Route', {'ApiId': ref('HttpApi'), 'RouteKey': route, 'Target': sub('integrations/${ApiIntegration}')})
r['DefaultStage']=resource('AWS::ApiGatewayV2::Stage', {'ApiId': ref('HttpApi'), 'StageName': '$default', 'AutoDeploy': True,
    'DefaultRouteSettings': {'ThrottlingBurstLimit': 5, 'ThrottlingRateLimit': 2}}, DependsOn=route_names)
r['InvokeApiPermission']=resource('AWS::Lambda::Permission', {'Action':'lambda:InvokeFunction','FunctionName':ref('ApiFunction'),
    'Principal':'apigateway.amazonaws.com','SourceArn':sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*')})
r['OriginAccess']=resource('AWS::CloudFront::OriginAccessControl', {'OriginAccessControlConfig': {'Name':sub('${AWS::StackName}-s3'),
    'OriginAccessControlOriginType':'s3','SigningBehavior':'always','SigningProtocol':'sigv4'}})
for name, ttl in [('StaticCache',300),('ApiCache',0)]:
    r[name]=resource('AWS::CloudFront::CachePolicy', {'CachePolicyConfig': {'Name': sub('${AWS::StackName}-'+name),
        'DefaultTTL':ttl, 'MinTTL':0,'MaxTTL':3600 if ttl else 0,
        'ParametersInCacheKeyAndForwardedToOrigin': {'EnableAcceptEncodingGzip':bool(ttl),'EnableAcceptEncodingBrotli':bool(ttl),
            'HeadersConfig':{'HeaderBehavior':'none'}, 'CookiesConfig':{'CookieBehavior':'none'}, 'QueryStringsConfig':{'QueryStringBehavior':'none'}}}})
r['ApiOriginPolicy']=resource('AWS::CloudFront::OriginRequestPolicy', {'OriginRequestPolicyConfig': {'Name':sub('${AWS::StackName}-api-headers'),
    'HeadersConfig':{'HeaderBehavior':'allExcept','Headers':['host']}, 'CookiesConfig':{'CookieBehavior':'none'}, 'QueryStringsConfig':{'QueryStringBehavior':'none'}}})
r['SecurityHeaders']=resource('AWS::CloudFront::ResponseHeadersPolicy', {'ResponseHeadersPolicyConfig': {'Name':sub('${AWS::StackName}-security'),
    'SecurityHeadersConfig': {'ContentTypeOptions':{'Override':True},'FrameOptions':{'FrameOption':'DENY','Override':True},
        'ReferrerPolicy':{'ReferrerPolicy':'no-referrer','Override':True}, 'StrictTransportSecurity':{'AccessControlMaxAgeSec':31536000,'IncludeSubdomains':True,'Override':True},
        'ContentSecurityPolicy': {'ContentSecurityPolicy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",'Override':True}}}})
r['Distribution']=resource('AWS::CloudFront::Distribution', {'DistributionConfig': {'Enabled':True, 'HttpVersion':'http2', 'IPV6Enabled':True,
    'DefaultRootObject':'index.html','PriceClass':'PriceClass_All','ViewerCertificate':{'CloudFrontDefaultCertificate':True},
    'Origins':[{'Id':'frontend','DomainName':att('FrontendBucket','RegionalDomainName'),'OriginAccessControlId':ref('OriginAccess'),'S3OriginConfig':{'OriginAccessIdentity':''}},
        {'Id':'api','DomainName':sub('${HttpApi}.execute-api.${AWS::Region}.amazonaws.com'),'CustomOriginConfig':{'OriginProtocolPolicy':'https-only','OriginSSLProtocols':['TLSv1.2']}}],
    'DefaultCacheBehavior': {'TargetOriginId':'frontend','ViewerProtocolPolicy':'redirect-to-https','AllowedMethods':['GET','HEAD','OPTIONS'],
        'CachePolicyId':ref('StaticCache'),'ResponseHeadersPolicyId':ref('SecurityHeaders'),'Compress':True},
    'CacheBehaviors':[{'PathPattern':'/api/*','TargetOriginId':'api','ViewerProtocolPolicy':'https-only',
        'AllowedMethods':['GET','HEAD','OPTIONS','POST','PUT','PATCH','DELETE'],'CachedMethods':['GET','HEAD'],
        'CachePolicyId':ref('ApiCache'),'OriginRequestPolicyId':ref('ApiOriginPolicy'),'ResponseHeadersPolicyId':ref('SecurityHeaders'),'Compress':True}],
}})
r['FrontendPolicy']=resource('AWS::S3::BucketPolicy', {'Bucket':ref('FrontendBucket'),'PolicyDocument':{'Version':'2012-10-17','Statement':[
    {'Effect':'Allow','Principal':{'Service':'cloudfront.amazonaws.com'},'Action':'s3:GetObject','Resource':sub('${FrontendBucket.Arn}/*'),
        'Condition':{'StringEquals':{'AWS:SourceArn':sub('arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}')}}},
    {'Effect':'Deny','Principal':'*','Action':'s3:*','Resource':[att('FrontendBucket','Arn'),sub('${FrontendBucket.Arn}/*')],
        'Condition':{'Bool':{'aws:SecureTransport':'false'}}},
]}})
template={'AWSTemplateFormatVersion':'2010-09-09','Description':'Payment desk: private S3/CloudFront, shared workspace password, asynchronous Lambda/AgentCore investigations.',
    'Parameters':{k:{'Type':'String'} for k in ['CodeBucket','CodeKey','AgentRuntimeArn','AccessParameter']},'Resources':r,
    'Outputs':{'WebsiteURL':{'Value':sub('https://${Distribution.DomainName}')},'FrontendBucket':{'Value':ref('FrontendBucket')},
        'DistributionId':{'Value':ref('Distribution')},'ApiURL':{'Value':att('HttpApi','ApiEndpoint')},'JobsTable':{'Value':ref('JobsTable')},
        'WorkspacePasswordParameter':{'Value':ref('AccessParameter')}}}
if __name__=='__main__':
    (Path(__file__).parent/'template.json').write_text(json.dumps(template,indent=2),encoding='utf-8')
    print('Generated CloudFormation template with',len(resources),'resources.')

# Host the Strands application in AgentCore Runtime

The MCP server is already hosted. Create a SECOND runtime named PaymentAgent for
the Strands application. The two runtimes serve different protocols:

Client -> PaymentAgent (HTTP, Strands) -> PaymentMCP (MCP) -> Aurora/runbooks
                       |
                       +-> Bedrock model inference

## 1. Package (already built)

`payment-agent.zip` contains `agent.py`, `agent_runtime.py`, and Linux ARM64
dependencies for Python 3.12. Local `.env` files and database scripts are excluded.
You do not need Docker for this ZIP deployment.

To rebuild after editing code, run in this directory:

```powershell
python build_agent_package.py
```

The builder needs `uv` on PATH and network access to download packages. It only
builds the ZIP; it does not upload, create roles, or deploy AWS resources.

## 2. Create the agent runtime in the AWS console

Select Sydney / ap-southeast-2, then Amazon Bedrock AgentCore -> Runtime/Agents
-> Host Agent. Choose local ZIP upload and configure:

| Setting | Value |
|---|---|
| Name | PaymentAgent |
| Source | payment-agent.zip |
| Runtime | Python 3.12 |
| Entry point | agent_runtime.py |
| Protocol | HTTP (the existing PaymentMCP runtime uses MCP) |
| Network | Public |
| Inbound authentication | IAM / SigV4 |
| Execution role | Create a new AgentCore execution role, or select a dedicated one |

Public network mode permits the outbound AWS service calls used here. IAM still
protects incoming requests. Do not reuse the database credentials in this runtime.

Set these environment variables in the hosting configuration:

| Name | Value |
|---|---|
| AWS_REGION | ap-southeast-2 |
| BEDROCK_MODEL_ID | minimax.minimax-m2.5 |
| MCP_AUTH_MODE | iam |
| MCP_URL | https://bedrock-agentcore.ap-southeast-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aap-southeast-2%3A142531840386%3Aruntime%2FPaymentMCP-m89sKCARJd/invocations?qualifier=DEFAULT |

Do not set AWS_PROFILE, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, or AWS_SESSION_TOKEN.
The runtime receives temporary credentials from its execution role. `.env.agent`
continues to apply only when running the local CLI.

## 3. Give that execution role application permissions

The console-generated execution role supplies the base hosting permissions
(including logs and code artifact access). Verify it trusts
`bedrock-agentcore.amazonaws.com`, with your account as the source account.

In IAM -> Roles -> the NEW PaymentAgent execution role -> Add permissions ->
Create inline policy -> JSON, paste `agent-application-policy.json`.
Name the policy PaymentAgentApplicationAccess.

This additional policy grants:
- InvokeAgentRuntime on the existing PaymentMCP runtime and its DEFAULT endpoint.
- InvokeModel on the current MiniMax model in Sydney (streaming is disabled in code).

It is an application policy, not a complete replacement for the role's hosting
policy or trust relationship. The new agent needs no direct PostgreSQL permissions:
PaymentMCP keeps responsibility for connecting to Aurora.

If you later change the model, update the model ARN in the policy. Cross-region
inference profiles require additional permissions on the profile and destination
models. Do not use a foundation-model ARN for an inference profile.

## 4. Invoke the new runtime

Wait for READY. Copy the NEW PaymentAgent ARN from the console, not the PaymentMCP ARN.
Run the following from PowerShell using an environment with boto3 installed:

```powershell
@'
import boto3, json, uuid
from botocore.config import Config

session = boto3.Session(profile_name='personal-dev', region_name='ap-southeast-2')
client = session.client('bedrock-agentcore', config=Config(read_timeout=300))
response = client.invoke_agent_runtime(
    agentRuntimeArn='PASTE_NEW_PAYMENT_AGENT_ARN',
    runtimeSessionId=str(uuid.uuid4()),
    qualifier='DEFAULT',
    contentType='application/json',
    payload=json.dumps({'prompt': 'Investigate payment PAY-1042. What happened?'}).encode(),
)
print(response['response'].read().decode())
'@ | python -
```

Here personal-dev authenticates YOUR LOCAL invocation. Inside AWS, the execution
role authenticates calls to Bedrock and MCP. Your local caller also needs
InvokeAgentRuntime permission on the NEW agent runtime and its DEFAULT endpoint.

Expected response: `{"answer": "...payment investigation..."}`.
Each request starts a fresh investigation; conversation history is not persisted.

## Troubleshooting

- AccessDenied before the handler runs: check the local caller's permission on
  the new agent runtime and endpoint.
- AccessDenied on MCP tool discovery: check the new agent execution role's
  permission on PaymentMCP and its DEFAULT endpoint.
- Bedrock AccessDenied: check the model ARN, region, role permission and model access.
- Invalid AWS profile: remove AWS_PROFILE from the hosted environment.
- Database tool timeout: inspect PaymentMCP logs and Aurora availability. A healthy
  agent HTTP endpoint alone does not prove its downstream database is reachable.
- Import/architecture errors: use the generated ZIP and select Python 3.12; do not
  ZIP your Windows virtual environment.

## References

- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html

## Fix for database connection timeouts and misleading missing-record answers

CloudWatch recorded ConnectionTimeout in PaymentMCP. An independent MCP check
retrieved PAY-1042 events successfully. Aurora is configured to pause after 300
idle seconds at a minimum capacity of zero; resume latency is a likely cause,
not a proven explanation of every timeout.

The updated MCP allows 45 seconds per connection attempt and retries once only
on ConnectionTimeout. Authentication and SQL errors are not retried. The updated
agent prompt distinguishes a successful found=false response from a tool failure.

Both replacement ZIPs have been built locally. They have NOT been deployed.

1. Open the existing PaymentMCP runtime -> Update hosting -> upload payment-mcp.zip.
   Keep Python 3.13, entrypoint main.py, protocol MCP, and all existing environment
   variables, IAM roles and network settings. Wait for READY.
2. Open the existing PaymentAgent runtime -> Update hosting -> upload payment-agent.zip.
   Keep Python 3.12, entrypoint agent_runtime.py, protocol HTTP, and all existing
   environment variables, IAM roles and network settings. Wait for READY.
3. Start a new test session and investigate PAY-1042 again. Its recorded events
   include PAYMENT_RECEIVED, SUBMITTED and UPSTREAM_TIMEOUT; the downstream outcome
   remains unknown. A database connection timeout is a separate infrastructure error.

Rebuild commands after future changes:

```powershell
python build_agent_package.py --mcp
python build_agent_package.py
```

AWS guidance on pause/resume latency and connection timeouts:
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html

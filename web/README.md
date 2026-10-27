# Payment investigation website

For the AWS deployment and shared workspace password, see [aws/README.md](aws/README.md).

Run from `C:\Users\nixon\Desktop\Project\MCP`.

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python.exe -m pip install -r web\requirements.txt
.\.venv-web\Scripts\python.exe -m uvicorn web.app:app --host 127.0.0.1 --port 8085
```

Open http://127.0.0.1:8085. If `.venv-web` has already been prepared, only the
last command is needed. The PowerShell execution policy does not need changing;
these commands invoke the virtual environment's Python directly.

## Configuration

The backend loads `.env.web` in the project root (not the agent or MCP environment).
Existing process environment variables take precedence. `.env.web` is ignored by Git.

```dotenv
AWS_REGION=ap-southeast-2
AWS_PROFILE=personal-dev
AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-southeast-2:142531840386:runtime/paymentagent-iG2aAxFqJ0
AGENT_RUNTIME_QUALIFIER=DEFAULT
```

This ARN is the Strands HTTP application, not PaymentMCP. No access keys are
stored in the page or application files. Boto3 uses the selected local AWS
profile; refresh/sign in to that profile if its credentials expire. For an SSO
profile, use `aws sso login --profile personal-dev`.

The backend identity needs `bedrock-agentcore:InvokeAgentRuntime` on:

- `arn:aws:bedrock-agentcore:ap-southeast-2:142531840386:runtime/paymentagent-iG2aAxFqJ0`
- `arn:aws:bedrock-agentcore:ap-southeast-2:142531840386:runtime/paymentagent-iG2aAxFqJ0/runtime-endpoint/DEFAULT`

The hosted Strands execution role keeps its separate permission to call Bedrock
and PaymentMCP. The website backend does not query PostgreSQL directly.

## Request flow

1. `static/app.js` POSTs `{"prompt":"Investigate payment PAY-1042. What happened?"}`
   to `/api/investigations` on the same server that serves the frontend.
2. `app.py` validates the input, creates a new session UUID and invokes the AWS
   runtime using Boto3. It sends exactly `{"prompt": ...}` to your existing agent.
3. AgentCore runs your Strands application, which calls Bedrock and MCP as needed.
4. The backend reads `{"answer":"..."}`, renders Markdown with raw HTML disabled,
   and returns the report, duration, timestamp, and request/session references.
5. The browser displays it and keeps up to ten reports in memory until refresh.

Health route: `GET /api/health` checks that the backend is configured. It does not
call AWS or claim that the database and agent are healthy.

The client waits up to ten minutes, while the AWS read timeout is five minutes.
An investigation can be slow after inactivity. SDK request retries are disabled
to avoid silently repeating a model invocation; the existing MCP database retry
still applies inside the deployed MCP server.

## Scope

This is a local learning application. It binds to loopback, restricts Host and
cross-site browser requests, and keeps AWS credentials on the server. It does
not implement application user login. The separate AWS deployment adds a shared workspace password, asynchronous jobs,
and Lambda execution roles. AWS_PROFILE remains local-only. Do not upload this web app to either existing AgentCore ZIP.

There is no Node build step: FastAPI serves the HTML, CSS and JavaScript.
Dependencies are separate from both MCP and Strands to avoid SDK version conflicts.

## Checks

```powershell
.\.venv-web\Scripts\python.exe -m pip install pytest httpx
.\.venv-web\Scripts\python.exe -m pytest web\test_app.py -q
```

Tests use a fake AWS response; the normal website always calls the real agent.

AWS SDK reference:
https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html

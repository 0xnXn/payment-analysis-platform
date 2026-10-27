# AWS deployment: paymentdesk-web

Account: `142531840386`; region: `ap-southeast-2`.
This deployment uses the existing `paymentagent-iG2aAxFqJ0` AgentCore runtime.
It does not redeploy Strands, MCP or Aurora.

## Architecture

```
Browser -> CloudFront -> private S3 (frontend files)
                 |
                 +-> /api/* -> API Gateway HTTP API -> API Lambda
                                                      |    |
                                                      |    +-> DynamoDB (job status/results)
                                                      +-> SQS -> Worker Lambda -> AgentCore
```

The API immediately returns a job ID. The browser polls for completion, avoiding
the HTTP API's 30-second integration limit. The worker can run for six minutes;
its AWS read timeout is five minutes. Reports expire after 24 hours. DynamoDB TTL
deletion is eventual; the API refuses to serve expired reports immediately.

The API accepts one shared workspace password. Public account registration and
individual sign-in accounts are not used. All password holders share the same
workspace access. The password is generated on first deployment and stored as a
Standard SecureString parameter at `/paymentdesk/web/workspace-password`.
No credential is embedded in the frontend. The browser keeps the entered
password only in sessionStorage for the lifetime of its tab.

## Get the workspace password

Run on your own terminal; do not commit the output or put it in a URL:

```powershell
aws ssm get-parameter --name /paymentdesk/web/workspace-password --with-decryption --query Parameter.Value --output text --region ap-southeast-2 --profile personal-dev
```

You can also view it in AWS Systems Manager -> Parameter Store -> the parameter
above -> Show decrypted value. Paste it into the site's workspace password box.

## Deployment files

- `handlers.py`: password check, job API, worker and safe report rendering.
- `make_template.py`: generates the full `template.json` CloudFormation template.
- `deploy.py`: packages dependencies, submits infrastructure and uploads frontend.
- `deployment.json`: generated non-secret resource outputs and website URL.
- `test_handlers.py`: isolated API/worker checks; no AWS charges.

Run from the project root:

```powershell
.\.venv-web\Scripts\python.exe web\aws\make_template.py
.\.venv-web\Scripts\python.exe web\aws\deploy.py package
.\.venv-web\Scripts\python.exe web\aws\deploy.py submit
.\.venv-web\Scripts\python.exe web\aws\deploy.py status
```

Wait for CREATE_COMPLETE or UPDATE_COMPLETE, then publish frontend files:

```powershell
.\.venv-web\Scripts\python.exe web\aws\deploy.py publish
```

For frontend-only changes, run `publish` without rebuilding the Lambda package.
The existing local FastAPI application remains available for local development.

## Protection and operations

- Private S3 bucket; CloudFront Origin Access Control can read its objects.
- HTTPS, CSP and other browser security headers on the frontend and API responses.
- API responses are never cached. Authorization is forwarded to the API origin.
- Constant-time shared password comparison. Only health is public.
- API throttling: two requests/second, burst five. Worker concurrency at most two.
- Narrow IAM roles: API can access only its job table, queue and password
  parameter; worker can invoke only the existing agent runtime/default endpoint.
- SQS duplicate deliveries do not repeat an already claimed agent invocation.
- A job stuck after a worker crash is not automatically reinvoked, to avoid
  duplicate model execution; inspect logs and submit a new job if necessary.
- Failed agent invocations are recorded as failed jobs, without exposing internal
  errors or credentials in the browser.
- Lambda CloudWatch logs have seven-day retention; SQS has a dead-letter queue.
- AWS resources and AgentCore/model invocations can incur usage charges. No
  always-running backend server is introduced by this deployment.

The Lambda password cache lasts for the execution environment. If you rotate
the SSM value, recycle/update the API Lambda execution environments as part of
the rotation so the old value stops being accepted by warm instances.

## Remove the deployment

Delete the `paymentdesk-web` CloudFormation stack when you no longer need it.
The frontend S3 bucket is deliberately retained; inspect and remove its objects
and bucket separately. The artifact bucket and SSM password parameter were
bootstrapped outside the stack and also remain until you remove them. Existing
AgentCore runtimes and Aurora are independent of this stack.

## Tests

```powershell
.\.venv-web\Scripts\python.exe -m pytest web\test_app.py web\aws\test_handlers.py -q
```

## Payments screen

Open the website with `#payments` or choose Payments in the sidebar. The screen
shows recorded payment statuses, amount/currency, created/updated UTC timestamps,
search, status filtering, and a refresh button. Investigate opens the selected
payment in the question composer; Start investigation runs the agent.

A password-protected GET /api/payments queues a read-only data job. The existing
worker invokes the existing AgentCore application with {"action":"list_payments"}.
That action bypasses Strands/model inference and calls MCP list_payments directly.
MCP enforces its configured demo-alpha tenant and uses a fixed parameterized SQL
query with cursor pagination. No additional Lambda IAM permission is required.
Payment rows are fetched anew on refresh; the timestamp shows when the snapshot
was retrieved. Status is the stored status, not an inferred bank outcome.

The current dataset has 20 accessible payments: 8 PENDING, 8 SETTLED, 4 REJECTED.

## Workspace operations interface

The interface identifies the active workspace using authenticated metadata from
GET /api/access. The deployment currently exposes Alpha Operations (demo-alpha)
with read-only access; the Test environment label reflects the synthetic dataset.
The existing shared workspace access code remains the access mechanism. There
are no individual users, tenant switching or role-based permission management.

Features include payment details, status quick views, sorting, filtered CSV
export and a workspace information dialog. CSV exports include only currently
filtered rows already loaded for the active workspace. CSV cells are escaped,
including spreadsheet formula prefixes. Lock workspace aborts browser requests,
removes the access code from sessionStorage and clears loaded payments/reports.
It does not cancel work already running on AWS or revoke the shared access code.

New jobs have a server-assigned tenant_id. The API rejects retrieval of jobs
outside that tenant, and the worker claims only jobs with the configured tenant.
The listing worker verifies the MCP result's tenant before storing payment rows.
Jobs from before this change without tenant metadata are no longer retrievable.
MCP still enforces payment-query tenant scope through its server configuration.

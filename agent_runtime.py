"""AgentCore HTTP entrypoint. Local CLI remains available in agent.py."""
import asyncio
import os

from bedrock_agentcore import BedrockAgentCoreApp
from agent import investigate
try:
    from payments_reader import read_payments
except ModuleNotFoundError:
    from web.payments_reader import read_payments

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload):
    if not isinstance(payload, dict):
        raise ValueError('Request must be a JSON object.')
    if os.getenv('MCP_AUTH_MODE', '').lower() != 'iam':
        raise RuntimeError('Hosted agent requires MCP_AUTH_MODE=iam.')
    if os.getenv('AWS_PROFILE'):
        raise RuntimeError('Remove AWS_PROFILE: hosted agents use their execution role.')
    # A deterministic data endpoint: uses existing MCP access, without model inference.
    if payload.get('action') == 'list_payments':
        return await read_payments()
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Provide a non-empty string in prompt.')
    if len(prompt) > 10000:
        raise ValueError('prompt must be at most 10000 characters.')
    # MCPClient and the Bedrock call are synchronous. Keep the HTTP event loop free.
    # investigate creates fresh agent state and closes its MCP connection per request.
    answer = await asyncio.to_thread(investigate, prompt.strip())
    return {'answer': answer}


if __name__ == '__main__':
    app.run()

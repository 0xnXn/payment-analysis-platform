"""Build an AgentCore ZIP. Default: agent; --mcp: existing Python 3.13 MCP runtime."""
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parent
mcp_build = '--mcp' in sys.argv[1:]
if any(arg != '--mcp' for arg in sys.argv[1:]):
    raise SystemExit('Usage: python build_agent_package.py [--mcp]')
version = '3.13' if mcp_build else '3.12'
requirements = 'requirements.txt' if mcp_build else 'requirements-runtime.txt'
sources = {'mcp_server.py': 'main.py'} if mcp_build else {
    'agent.py': 'agent.py', 'agent_runtime.py': 'agent_runtime.py',
    'web/payments_reader.py': 'payments_reader.py',
}
output = root / ('payment-mcp.zip' if mcp_build else 'payment-agent.zip')

with tempfile.TemporaryDirectory(prefix='payment-agent-build-') as work:
    target = Path(work) / 'package'
    subprocess.run([
        'uv', 'pip', 'install',
        '--python-platform', 'aarch64-manylinux2014', '--python-version', version,
        '--only-binary=:all:',
        '--target', str(target), '-r', str(root / requirements),
    ], check=True)
    # Explicit source allowlist: never package .env files, credentials or DB scripts.
    for source_name, destination_name in sources.items():
        (target / destination_name).write_bytes((root / source_name).read_bytes())
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(target.rglob('*')):
            if not source.is_file() or '__pycache__' in source.parts:
                continue
            info = zipfile.ZipInfo(source.relative_to(target).as_posix())
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
print(f'Created {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)')
print('AWS settings: Python 3.13, entrypoint main.py, protocol MCP.' if mcp_build else
      'AWS settings: Python 3.12, entrypoint agent_runtime.py, protocol HTTP.')

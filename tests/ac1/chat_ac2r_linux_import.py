"""Import the reviewed Chat ZIP in Lambda Python 3.11, with network disabled."""
import os
from pathlib import Path
import sys
import zipfile

root = Path('/tmp/ac2r-chat')
with zipfile.ZipFile('/artifact.zip') as archive:
    for name in archive.namelist():
        assert (root/name).resolve().is_relative_to(root)
    archive.extractall(root)
sys.path.insert(0,str(root))
os.environ.update(AWS_EC2_METADATA_DISABLED='true', AWS_DEFAULT_REGION='us-east-1',
    AWS_REGION='us-east-1', SOPHIA_TOOL_EXECUTION='disabled')
from src.handlers.domains.chat_agents import handler
from src.shared.sophia_tool_execution import selected_execution
from src.shared.sophia_agentcore_client import AgentCoreExecution
assert callable(handler)
assert selected_execution().__class__.__name__=='UnavailableExecution'
assert 'src.shared.sophia_mcp_client' not in sys.modules
print('CHAT_ISOLATED_LINUX_IMPORT=PASS')

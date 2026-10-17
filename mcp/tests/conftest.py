import pytest
from xoc_mcp_lab import testing
from xoc_mcp_lab.wiring import Wiring

@pytest.fixture
def wired():
    with Wiring() as wiring:
        from xoc_mcp_lab.adapter import Adapter
        yield wiring,lambda profile="admin_tenant_a":Adapter(profile,wiring)
@pytest.fixture
def backend():
    assert testing.BACKEND is not None, "Use run_tests.py with an explicit backend"
    return testing.BACKEND

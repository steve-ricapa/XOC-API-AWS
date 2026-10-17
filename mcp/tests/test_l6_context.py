import asyncio
import sys
import pytest


@pytest.mark.parametrize('case', ['C09','C10','C13','C21','C22'])
def test_l6_context_acceptance(case):
    if sys.platform != 'linux':
        pytest.skip('Installed Linux boundary acceptance')
    from xoc_mcp_lab_l6.acceptance import run_case
    row = asyncio.run(run_case(case))
    assert row['passed'], (case, row.get('error_type'), row.get('error_code'), row.get('failure_location'))

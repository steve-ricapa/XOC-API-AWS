import asyncio
import sys
import pytest


@pytest.mark.parametrize('case', ['C17','C20','C23','C24'])
def test_l6_lifecycle_acceptance(case):
    if sys.platform != 'linux':
        pytest.skip('Installed Linux supervisor lifecycle acceptance')
    from xoc_mcp_lab_l6.acceptance import run_case
    row = asyncio.run(run_case(case))
    assert row['passed'], (case, row.get('error_type'), row.get('error_code'), row.get('failure_location'))

"""Linux installed recovery acceptance; portable skips are not credited."""
import asyncio
import sys
import pytest


@pytest.mark.parametrize('case', [f'C{i:02}' for i in (1,2,3,4,5,6,7,8,11,12,14,15,16,18,19)])
def test_l6_recovery_acceptance(case):
    if sys.platform != 'linux':
        pytest.skip('Owned Linux process recovery requires installed candidate image')
    from xoc_mcp_lab_l6.acceptance import run_case
    row = asyncio.run(run_case(case))
    assert row['passed'], (case, row.get('error_type'), row.get('error_code'), row.get('failure_location'))

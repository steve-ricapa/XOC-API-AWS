"""Negative control: the HTTP tripwire must reject legacy import/spawn paths."""
import importlib
import subprocess
from no_stdio import FORBIDDEN, evidence


def check():
    checks = 0
    for module in sorted(FORBIDDEN):
        for load in (__import__, importlib.import_module):
            try:
                load(module)
            except RuntimeError as error:
                assert str(error) == "FT_STDIO_FORBIDDEN"
                checks += 1
            else:
                raise AssertionError("FT_TRIPWIRE_DID_NOT_REJECT_IMPORT")
    try:
        subprocess.Popen(["never-executed", "-m", "xoc_mcp_lab.verified_server"])
    except RuntimeError as error:
        assert str(error) == "FT_NON_HTTP_CHILD_FORBIDDEN"
        checks += 1
    else:
        raise AssertionError("FT_TRIPWIRE_DID_NOT_REJECT_SPAWN")
    assert subprocess.Popen[bytes]  # Preserve SDK annotation compatibility.
    checks += 1
    return dict(passed=checks == 8, checks=checks, negative_control=True,
                detected_attempts=len(evidence("/tmp/ft-http-guard")["violations"]))

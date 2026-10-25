"""Read JSON information from explicitly configured public HTTPS API endpoints."""
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from src.remediation.intune import RemoteError, request_json


def query_information(base_url, path, *, allowed_hosts, bearer_token=None, transport=request_json):
    """GET only. Private enterprise APIs need a local appliance connector instead."""
    base = urlsplit(base_url)
    if (base.scheme != "https" or base.username or base.password or base.port not in (None, 443)
            or not base.hostname or base.hostname not in set(allowed_hosts)
            or base.query or base.fragment or base.path not in ("", "/")):
        raise ValueError("Configure an allowlisted HTTPS origin without credentials")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("An origin-relative API path is required")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("API path must remain on the configured origin")
    addresses = socket.getaddrinfo(base.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Public API queries cannot target private or reserved addresses")
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = "Bearer " + bearer_token
    status, value = transport("GET", urlunsplit(("https", base.netloc, parsed.path, parsed.query, "")), headers=headers)
    if status != 200 or not isinstance(value, (dict, list)):
        raise RemoteError("Information endpoint did not return a JSON object or array")
    return value

import re


OFFICIAL_INTEGRATION_TYPES = [
    "openvas",
    "insightvm",
    "nessus",
    "qualys",
    "tenable",
    "rapid7",
    "zabbix",
    "uptime_kuma",
    "wazuh",
    "nmap",
    "other",
]

_OFFICIAL_INTEGRATION_TYPES_SET = set(OFFICIAL_INTEGRATION_TYPES)

_INTEGRATION_TYPE_ALIASES = {
    "openvas_scanner": "openvas",
    "insightvm_rapid7": "insightvm",
    "nessus_scanner": "nessus",
    "zabbix_monitor": "zabbix",
    "uptimekuma": "uptime_kuma",
    "wazuh_siem": "wazuh",
    "otro": "other",
}


def _normalize_integration_type_key(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


def normalize_integration_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_key = _normalize_integration_type_key(str(value))
    if not normalized_key:
        return None
    canonical = _INTEGRATION_TYPE_ALIASES.get(normalized_key, normalized_key)
    if canonical in _OFFICIAL_INTEGRATION_TYPES_SET:
        return canonical
    return None

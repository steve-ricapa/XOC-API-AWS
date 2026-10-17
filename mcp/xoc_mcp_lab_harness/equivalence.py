"""Reference and installed results; normalize ONLY request_id and audit_id."""
import argparse
import json
import sys
from xoc_mcp_lab.isolation import Isolation, backend_path, configure_logs


def cases():
    from xoc_mcp_lab.fixtures import PROFILES
    from xoc_mcp_lab.wiring import Wiring
    results = []
    with Wiring() as w:
        from xoc_mcp_lab.adapter import Adapter
        for profile in PROFILES:
            for name, args in (("xoc.tickets.list", {}), ("xoc.tickets.get", {"ticket_id": "lab-ticket-a1"}),
                               ("xoc.documents.list", {}), ("xoc.notifications.unread_count", {})):
                adapter = Adapter(profile, w)
                result = adapter.call(name, args).structuredContent
                results.append(dict(profile=profile, name=name, arguments=args,
                    result={k: v for k, v in result.items() if k not in {"request_id", "audit_id"}},
                    handlers=adapter.handler_calls, reads=adapter.events[-1]["reads"]))
        for name, args in (("xoc.tickets.get", {"ticket_id": "lab-ticket-b1"}),
                ("xoc.tickets.list", {"tenantId": 91002}), ("xoc.tickets.list", {"user_id": 92003}),
                ("xoc.evil.execute", {}), ("xoc.dashboard.summary", {}), ("xoc.integrations.summary", {}),
                ("xoc.tickets.create", {}), ("xoc.tickets.list", {"limit": None}),
                ("xoc.tickets.list", {"status": "pending", "limit": 1})):
            adapter = Adapter("admin_tenant_a", w)
            result = adapter.call(name, args).structuredContent
            results.append(dict(profile="admin_tenant_a", name=name, arguments=args,
                result={k: v for k, v in result.items() if k not in {"request_id", "audit_id"}},
                handlers=adapter.handler_calls, reads=adapter.events[-1]["reads"]))
        assert w.provider_attempts == w.write_attempts == 0
    return dict(normalized_fields=["request_id", "audit_id"], cases=results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend")
    args = parser.parse_args()
    with Isolation():
        configure_logs()
        path = backend_path(args.backend)
        sys.path.insert(0, str(path))
        print(json.dumps(cases(), indent=2))


if __name__ == "__main__": main()

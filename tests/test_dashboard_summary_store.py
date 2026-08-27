from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.integrations.summary_store import build_dashboard_summary, build_vulnerability_summary, build_zabbix_summary
from src.persistence.models import AgentApiKey, Base, ScanSummary, ScanSummaryNoc, Tenant


class DashboardSummaryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        self.session.add(Tenant(id=7, name="Mi Fibra", plan_status="ACTIVE"))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()

    def test_vulnerability_summary_treats_agent_key_as_configured(self) -> None:
        agent = AgentApiKey(tenant_id=7, name="k3", integration_type="nessus", api_key_hash="hash", is_active=True)
        self.session.add(agent)
        self.session.flush()
        self.session.add(
            ScanSummary(
                tenant_id=7,
                agent_api_key_id=agent.id,
                scan_id="NE-1",
                scanner_type="nessus",
                summary_type="vulnerability",
                status="completed",
                critical_count=1,
                high_count=2,
                medium_count=3,
                low_count=4,
                info_count=5,
                cvss_max=9.5,
                total_hosts=10,
                scan_name="Nessus Real-time Sync",
                scanned_at=datetime(2026, 8, 26, 22, 3, 44),
            )
        )
        self.session.commit()

        result = build_vulnerability_summary(self.session, 7, "nessus")

        self.assertTrue(result["configured"])
        self.assertTrue(result["active"])
        self.assertTrue(result["has_data"])
        self.assertEqual("k3", result["agent_name"])
        self.assertEqual(1, result["vulnerabilities"]["critical"])

    def test_noc_summary_treats_agent_key_as_configured(self) -> None:
        agent = AgentApiKey(tenant_id=7, name="kz", integration_type="zabbix", api_key_hash="hash", is_active=True)
        self.session.add(agent)
        self.session.flush()
        self.session.add(
            ScanSummaryNoc(
                tenant_id=7,
                agent_api_key_id=agent.id,
                scan_id="ZA-1",
                scanner_type="zabbix",
                summary_type="noc_health",
                status="completed",
                critical_count=1,
                high_count=4,
                medium_count=10,
                low_count=20,
                info_count=15,
                cvss_max=0.0,
                total_hosts=10,
                scan_name="Zabbix Real-time Sync",
                scanned_at=datetime(2026, 8, 26, 23, 3, 45),
            )
        )
        self.session.commit()

        result = build_zabbix_summary(self.session, 7)

        self.assertTrue(result["configured"])
        self.assertTrue(result["active"])
        self.assertTrue(result["has_data"])
        self.assertEqual(50, result["alerts"])

    def test_dashboard_summary_counts_agent_key_only_integrations(self) -> None:
        agent = AgentApiKey(tenant_id=7, name="k3", integration_type="nessus", api_key_hash="hash", is_active=True)
        self.session.add(agent)
        self.session.commit()

        result = build_dashboard_summary(self.session, 7)

        self.assertEqual(1, result["summary"]["total_integrations_configured"])
        self.assertTrue(result["nessus"]["configured"])
        self.assertTrue(result["nessus"]["active"])


if __name__ == "__main__":
    unittest.main()

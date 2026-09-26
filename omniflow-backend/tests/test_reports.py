"""Report downloads must never sign another tenant's object or an arbitrary URL."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.shared.core.config import Settings
from src.shared.services import report_download


class ReportDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(minio_endpoint="http://localhost:9020", s3_vault_bucket="test-vault")
        self.report = SimpleNamespace(tenant_id=uuid.uuid4(), customer_id=uuid.uuid4(), s3_url=None)
        self.key = f"tenant-{self.report.tenant_id}/reports/{self.report.customer_id}/report.pdf"

    async def test_valid_object_gets_expiring_url(self):
        self.report.s3_url = f"http://localhost:9020/test-vault/{self.key}"
        with patch.object(report_download, "get_settings", return_value=self.settings), \
                patch.object(report_download.s3_mgr, "generate_presigned_url", AsyncMock(return_value="signed-url")) as sign:
            self.assertEqual(await report_download.report_download_url(self.report), "signed-url")
            sign.assert_awaited_once_with("test-vault", self.key, expires_in=900)

    async def test_foreign_tenant_customer_host_and_traversal_are_rejected(self):
        invalid = [
            f"http://attacker.invalid/test-vault/{self.key}",
            f"s3://test-vault/tenant-{uuid.uuid4()}/reports/{self.report.customer_id}/file.pdf",
            f"s3://test-vault/tenant-{self.report.tenant_id}/reports/{uuid.uuid4()}/file.pdf",
            f"s3://test-vault/{self.key}/%2e%2e/other.pdf",
        ]
        with patch.object(report_download, "get_settings", return_value=self.settings), \
                patch.object(report_download.s3_mgr, "generate_presigned_url", AsyncMock()) as sign:
            for location in invalid:
                with self.subTest(location=location):
                    self.report.s3_url = location
                    with self.assertRaises(ValueError):
                        await report_download.report_download_url(self.report)
            sign.assert_not_awaited()

    async def test_report_without_file_has_no_download(self):
        self.assertIsNone(await report_download.report_download_url(self.report))

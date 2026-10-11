"""HTTP client for ZATCA's Fatoora e-invoicing API (onboarding, compliance checks, clearance, reporting).

Endpoint paths and headers follow ZATCA's public developer documentation; they have NOT been exercised against ZATCA from this repository.
Safety: the default environment is `sandbox`. The `production` environment is refused unless BOTH `einvoice_env=production` AND
`einvoice_allow_production_submit=true` are configured — a single typo or leaked env var cannot send real invoices.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from src.shared.core.config import get_settings

URLS = {
    "sandbox": "https://gw-fatoora.zatca.gov.sa/e-invoicing/developer-portal",
    "simulation": "https://gw-fatoora.zatca.gov.sa/e-invoicing/simulation",
    "production": "https://gw-fatoora.zatca.gov.sa/e-invoicing/core",
}


class EInvoiceError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.retryable, self.status, self.body = retryable, status, body


@dataclass(frozen=True)
class Credentials:
    binary_security_token: str      # base64 of the CSID certificate, as ZATCA returns it
    secret: str

    def basic(self) -> str:
        return "Basic " + base64.b64encode(f"{self.binary_security_token}:{self.secret}".encode()).decode()


def base_url(env: str | None = None) -> str:
    cfg = get_settings()
    env = env or cfg.einvoice_env
    if env not in URLS:
        raise EInvoiceError(f"unknown e-invoicing environment {env!r}")
    if env == "production" and not (cfg.einvoice_env == "production" and cfg.einvoice_allow_production_submit):
        raise EInvoiceError("production submission is locked (needs einvoice_env=production AND einvoice_allow_production_submit=true)")
    return (cfg.einvoice_base_url or URLS[env]).rstrip("/")


class ZatcaClient:
    def __init__(self, *, env: str | None = None, client: httpx.AsyncClient | None = None):
        self.env = env or get_settings().einvoice_env
        self._client = client
        self._url = base_url(self.env)               # raises when the environment is locked

    async def _post(self, path: str, body: dict, *, creds: Credentials | None = None, headers: dict | None = None) -> dict:
        h = {"Accept": "application/json", "Content-Type": "application/json", "Accept-Version": "V2", "Accept-Language": "en", **(headers or {})}
        if creds is not None:
            h["Authorization"] = creds.basic()
        try:
            if self._client is not None:
                r = await self._client.post(self._url + path, json=body, headers=h, timeout=30)
            else:
                async with httpx.AsyncClient() as c:
                    r = await c.post(self._url + path, json=body, headers=h, timeout=30)
        except httpx.HTTPError as exc:
            raise EInvoiceError(f"ZATCA unreachable: {type(exc).__name__}", retryable=True) from exc
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:500]}
        if r.status_code >= 500 or r.status_code == 429:
            raise EInvoiceError(f"ZATCA error {r.status_code}", retryable=True, status=r.status_code, body=data)
        if r.status_code in (401, 403):
            raise EInvoiceError("ZATCA rejected the credentials", status=r.status_code, body=data)
        # 200 = accepted, 202 = accepted with warnings, 400 = invoice rejected (validation results in the body), 409 = duplicate
        if r.status_code == 400:
            raise EInvoiceError("ZATCA rejected the document", status=400, body=data)
        if r.status_code == 409:
            raise EInvoiceError("ZATCA says this document was already submitted", status=409, body=data)
        if r.status_code >= 300 and r.status_code not in (200, 201, 202):
            raise EInvoiceError(f"unexpected ZATCA status {r.status_code}", status=r.status_code, body=data)
        return {"http_status": r.status_code, **data}

    # ── onboarding ──────────────────────────────────────────────────────────────
    async def request_compliance_csid(self, csr_b64: str, otp: str) -> dict:
        return await self._post("/compliance", {"csr": csr_b64}, headers={"OTP": otp})

    async def compliance_check(self, creds: Credentials, *, invoice_hash: str, uuid: str, signed_xml: bytes) -> dict:
        return await self._post("/compliance/invoices", {"invoiceHash": invoice_hash, "uuid": uuid, "invoice": base64.b64encode(signed_xml).decode()}, creds=creds)

    async def request_production_csid(self, compliance_creds: Credentials, compliance_request_id: str) -> dict:
        return await self._post("/production/csids", {"compliance_request_id": compliance_request_id}, creds=compliance_creds)

    # ── operations ──────────────────────────────────────────────────────────────
    async def clear(self, creds: Credentials, *, invoice_hash: str, uuid: str, signed_xml: bytes) -> dict:
        """B2B (standard) invoices: ZATCA validates and stamps BEFORE the invoice may be shared with the buyer."""
        return await self._post("/invoices/clearance/single", {"invoiceHash": invoice_hash, "uuid": uuid, "invoice": base64.b64encode(signed_xml).decode()},
                                creds=creds, headers={"Clearance-Status": "1"})

    async def report(self, creds: Credentials, *, invoice_hash: str, uuid: str, signed_xml: bytes) -> dict:
        """B2C (simplified) invoices: reported within 24 hours of issue."""
        return await self._post("/invoices/reporting/single", {"invoiceHash": invoice_hash, "uuid": uuid, "invoice": base64.b64encode(signed_xml).decode()},
                                creds=creds, headers={"Clearance-Status": "0"})

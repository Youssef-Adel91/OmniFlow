from __future__ import annotations

from src.shared.core.config import get_settings
from src.shared.services.billing.providers.base import PaymentProvider, ProviderError

_override: PaymentProvider | None = None       # tests inject a fake here


def set_provider_for_tests(p: PaymentProvider | None) -> None:
    global _override
    _override = p


def get_provider() -> PaymentProvider:
    if _override is not None:
        return _override
    name = get_settings().payment_provider.lower()
    if name == "moyasar":
        from src.shared.services.billing.providers.moyasar import MoyasarProvider

        return MoyasarProvider()
    if name == "sandbox":
        from src.shared.services.billing.providers.sandbox import SandboxProvider

        return SandboxProvider()
    raise ProviderError(f"Unknown payment provider {name!r}", code="unknown_provider")

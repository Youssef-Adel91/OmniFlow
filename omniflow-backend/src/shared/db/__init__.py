"""
shared/db/__init__.py — Public surface of the database package.

Import order matters for Alembic mapper registration:
  base       → must be first (defines Base / mixins)
  models     → registers all ORM classes against Base.metadata
  repository → data access layer (depends on models)

Usage in Alembic env.py:
    from src.shared.db import Base
    import src.shared.db.models  # noqa: F401 — force mapper registration
    target_metadata = Base.metadata
"""
from src.shared.db.base import Base, TimestampMixin, TenantScopedMixin
from src.shared.db import models  # noqa: F401 — registers all mappers with Base
from src.shared.db.repository import (
    BaseRepository,
    CustomerRepository,
    ConversationRepository,
    TenantRepository,
    TenantUserRepository,
    PropertyListingRepository,
)

__all__ = [
    # Base layer
    "Base",
    "TimestampMixin",
    "TenantScopedMixin",
    # Repositories
    "BaseRepository",
    "CustomerRepository",
    "ConversationRepository",
    "TenantRepository",
    "TenantUserRepository",
    "PropertyListingRepository",
]

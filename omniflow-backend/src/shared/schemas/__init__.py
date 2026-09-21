"""
shared/schemas/__init__.py — Public surface of the schemas package.

Import all schema classes from here in FastAPI endpoint files:
    from src.shared.schemas import TenantResponse, CustomerCreate, ...
"""
from src.shared.schemas.schemas import (
    # Tenant
    TenantCreate,
    TenantUpdate,
    TenantResponse,
    TenantSummary,
    # Customer
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerSummary,
    # Message
    MessageCreate,
    MessageUpdate,
    MessageResponse,
    # TenantUser
    TenantUserCreate,
    TenantUserUpdate,
    TenantUserResponse,
    # Conversation
    ConversationResponse,
    # CustomerReport
    CustomerReportResponse,
    # PropertyListing
    PropertyListingCreate,
    PropertyListingUpdate,
    PropertyListingResponse,
    PropertyListingPage,
)

__all__ = [
    "TenantCreate",
    "TenantUpdate",
    "TenantResponse",
    "TenantSummary",
    "CustomerCreate",
    "CustomerUpdate",
    "CustomerResponse",
    "CustomerSummary",
    "MessageCreate",
    "MessageUpdate",
    "MessageResponse",
    "TenantUserCreate",
    "TenantUserUpdate",
    "TenantUserResponse",
    "ConversationResponse",
    "CustomerReportResponse",
    # PropertyListing
    "PropertyListingCreate",
    "PropertyListingUpdate",
    "PropertyListingResponse",
    "PropertyListingPage",
]

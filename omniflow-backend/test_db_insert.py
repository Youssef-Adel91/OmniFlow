import asyncio
from src.shared.db.session import get_system_session
from src.shared.db.models import Tenant, TenantUser
from src.shared.core.enums import SubscriptionStatus, OnboardingStatus, TenantUserRole

async def main():
    try:
        async with get_system_session() as session:
            new_tenant = Tenant(
                business_name="Dev Workspace",
                fal_license_number="DEV-12345678",
                status=SubscriptionStatus.TRIAL,
                onboarding_status=OnboardingStatus.PENDING_SELECTION
            )
            session.add(new_tenant)
            await session.flush()
            
            user = TenantUser(
                clerk_id="user_123",
                tenant_id=new_tenant.tenant_id,
                full_name="Local Dev User",
                email="dev@example.com",
                role=TenantUserRole.ADMIN,
                hashed_password="clerk_managed",
            )
            session.add(user)
            await session.commit()
            print("SUCCESS")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())

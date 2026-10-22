import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import repository, stripe_gateway
from app.billing.models import CustomerPortalResponse, Subscription, SubscriptionResponse
from app.config import settings
from app.organization import service as organization_service
from app.organization.service import get_member_count, get_pending_invitation_count

logger = logging.getLogger(__name__)


async def sync_stripe_to_postgres(stripe_customer_id: str, db: AsyncSession) -> dict:
    """Idempotent sync from Stripe to Postgres (single source of truth)."""
    try:
        logger.info(f"Syncing Stripe customer {stripe_customer_id}")

        org = await organization_service.get_organization_by_customer(db, stripe_customer_id)

        if not org:
            logger.error(f"No organization found for customer {stripe_customer_id}")
            raise ValueError(f"Organization not found for customer {stripe_customer_id}")

        subscriptions = stripe_gateway.list_subscriptions(
            customer=stripe_customer_id,
            limit=10,
            status="all",
        )

        # Prefer active/trialing subscriptions over canceled
        active_sub = None
        latest_sub = None

        for sub in subscriptions.data:
            if sub.status in ["active", "trialing"]:
                active_sub = sub
                break
            if latest_sub is None or sub.created > latest_sub.created:
                latest_sub = sub

        stripe_sub = active_sub or latest_sub

        if not stripe_sub:
            logger.info(f"No subscription for customer {stripe_customer_id}")
            org.subscription_status = "none"
            await db.commit()
            return {"status": "none", "organization_id": org.id}

        # Find seat item by matching either tier price ID
        # Use dict-style access: Stripe SDK v14 StripeObject inherits from dict,
        # so `stripe_sub.items` resolves to dict's built-in items() method.
        seat_item = None
        for item in stripe_sub["items"]["data"]:
            if item["price"]["id"] in [
                settings.STRIPE_PRICE_ID_TIER1,
                settings.STRIPE_PRICE_ID_TIER2,
            ]:
                seat_item = item
                break

        if not seat_item:
            logger.error(f"No seat item found in subscription {stripe_sub.id}")
            raise ValueError(f"Subscription {stripe_sub.id} missing seat price item")

        seats = seat_item["quantity"]

        org.subscription_status = stripe_sub.status
        org.max_users = seats

        subscription = await repository.get_by_stripe_id(db, stripe_sub.id)

        # Stripe API 2025+: current_period_start/end moved from subscription to items
        period_start = datetime.fromtimestamp(seat_item["current_period_start"], tz=timezone.utc)
        period_end = datetime.fromtimestamp(seat_item["current_period_end"], tz=timezone.utc)

        if not subscription:
            subscription = Subscription(
                id=str(uuid4()),
                organization_id=org.id,
                stripe_subscription_id=stripe_sub.id,
                stripe_customer_id=stripe_customer_id,
                stripe_price_id=seat_item["price"]["id"],
                status=stripe_sub.status,
                seats=seats,
                current_period_start=period_start,
                current_period_end=period_end,
                cancel_at_period_end=stripe_sub.cancel_at_period_end,
            )
            repository.save(db, subscription)
        else:
            subscription.status = stripe_sub.status
            subscription.seats = seats
            subscription.stripe_price_id = seat_item["price"]["id"]
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end
            subscription.cancel_at_period_end = stripe_sub.cancel_at_period_end

        await db.commit()

        logger.info(f"Synced org {org.id}: status={org.subscription_status}, seats={org.max_users}")

        return {
            "status": org.subscription_status,
            "organization_id": org.id,
            "seats": org.max_users,
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Sync failed for customer {stripe_customer_id}: {e}")
        raise


async def create_checkout_session(
    user_id: str,
    email: str,
    organization_id: str,
    seats: int,
    first_name: str,
    last_name: str,
    price_tier: str,
    db: AsyncSession,
) -> dict[str, str]:
    """
    Create Stripe checkout session for existing organization.

    Organization must already exist (created via /organization/create).
    This endpoint only handles Stripe customer + checkout session creation.
    """
    # Get existing organization
    org = await organization_service.get_organization_by_id(db, organization_id)
    if not org:
        raise HTTPException(404, "Organization not found")

    # Verify user is owner of this organization
    membership = await organization_service.get_owner_membership_for_user(
        db, organization_id, user_id
    )

    if not membership:
        raise HTTPException(403, "Not authorized: You must be the owner of this organization")

    # Update seats if different from org max_users
    if seats != org.max_users:
        org.max_users = seats

    # Set subscription status to pending while checkout in progress
    org.subscription_status = "pending"

    # Commit org updates
    try:
        await db.commit()
        await db.refresh(org)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update organization: {e}")
        raise HTTPException(500, f"Failed to update organization: {str(e)}")

    customer_id = org.stripe_customer_id

    if not customer_id:
        try:
            customer = stripe_gateway.create_customer(
                email=email,
                name=f"{first_name} {last_name}",
                metadata={
                    "organization_id": org.id,
                    "organization_name": org.name,
                },
            )
            customer_id = customer.id
        except Exception as e:
            logger.error(f"Stripe customer creation failed for org {org.id}: {e}")
            org.subscription_status = "failed"
            await db.commit()
            raise HTTPException(500, f"Failed to create Stripe customer: {str(e)}")

        org.stripe_customer_id = customer_id

        try:
            await db.commit()
            await db.refresh(org)
        except Exception as e:
            await db.rollback()
            try:
                stripe_gateway.delete_customer(customer_id)
            except Exception:
                logger.exception("Failed to delete Stripe customer %s during rollback", customer_id)
            logger.error(f"Failed to link Stripe customer: {e}")
            raise HTTPException(500, f"Failed to link Stripe customer: {str(e)}")

    # Use the price tier selected by the user
    price_id = (
        settings.STRIPE_PRICE_ID_TIER1 if price_tier == "tier1" else settings.STRIPE_PRICE_ID_TIER2
    )

    # Validate price ID format
    if not price_id or not price_id.startswith("price_"):
        logger.error(f"Invalid Stripe price ID: {price_id} for tier {price_tier}")
        org.subscription_status = "failed"
        await db.commit()
        raise HTTPException(
            500,
            f"Invalid Stripe price configuration. Please contact support. (Tier: {price_tier})",
        )

    try:
        idempotency_key = f"checkout_{org.id}_{int(datetime.now(timezone.utc).timestamp())}"

        logger.info(f"Creating Stripe checkout for org {org.id} with price {price_id}")

        checkout_session = stripe_gateway.create_checkout(
            customer=customer_id,
            mode="subscription",
            line_items=[
                {
                    "price": price_id,
                    "quantity": seats,
                }
            ],
            success_url=f"{settings.FRONTEND_SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=settings.FRONTEND_CANCEL_URL,
            idempotency_key=idempotency_key,
        )

        logger.info(f"Checkout session created: {checkout_session.id}")
    except Exception as e:
        logger.error(f"Checkout session creation failed for org {org.id}: {e}")
        org.subscription_status = "failed"
        await db.commit()
        raise HTTPException(500, f"Failed to create checkout session: {str(e)}")

    return {
        "checkout_url": checkout_session.url or "",
        "session_id": checkout_session.id or "",
        "organization_id": org.id,
    }


async def sync_after_checkout(session_id: str, user_id: str, db: AsyncSession) -> dict:
    """Verify checkout session and sync subscription from Stripe."""
    try:
        session = stripe_gateway.retrieve_checkout(session_id)
    except Exception as e:
        logger.error(f"Failed to retrieve session {session_id}: {e}")
        raise HTTPException(400, f"Invalid session: {str(e)}")

    if session.status != "complete":
        raise HTTPException(400, f"Session not complete: {session.status}")

    customer_id = session.customer
    if not customer_id:
        raise HTTPException(400, "No customer in session")

    org = await organization_service.get_organization_by_customer(db, customer_id)

    if not org:
        raise HTTPException(404, "Organization not found for session")

    membership = await organization_service.get_owner_membership_for_user(db, org.id, user_id)
    is_owner = membership is not None

    if not is_owner:
        raise HTTPException(403, "Not authorized for this organization")

    result = await sync_stripe_to_postgres(customer_id, db)

    return result


async def get_subscription_by_org(organization_id: str, db: AsyncSession) -> Optional[Subscription]:
    """Get organization subscription."""
    return await repository.get_by_organization(db, organization_id)


async def cancel_subscription(subscription_id: str, at_period_end: bool = True) -> None:
    """Cancel subscription in Stripe."""
    if at_period_end:
        stripe_gateway.modify_subscription(
            subscription_id,
            cancel_at_period_end=True,
        )
    else:
        stripe_gateway.cancel_subscription(subscription_id)


async def get_customer_portal_url(customer_id: str, return_url: str) -> str:
    """Generate Stripe Customer Portal URL."""
    portal_session = stripe_gateway.create_portal(
        customer=customer_id,
        return_url=return_url,
    )
    return portal_session.url


async def get_organization_subscription(
    context_organization, context_subscription, db: AsyncSession
):
    """Get organization subscription details (owner only)."""

    if not context_subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    current_members = await get_member_count(db, context_organization.id)
    pending_invitations = await get_pending_invitation_count(db, context_organization.id)
    reserved_seats = current_members + pending_invitations
    available_seats = max(0, context_subscription.seats - reserved_seats)
    excess_users = max(0, current_members - context_subscription.seats)

    now = datetime.now(timezone.utc)
    within_paid_period = now < context_subscription.current_period_end

    return SubscriptionResponse(
        id=context_subscription.id,
        organization_id=context_subscription.organization_id,
        status=context_subscription.status,
        seats=context_subscription.seats,
        current_period_end=context_subscription.current_period_end,
        cancel_at_period_end=context_subscription.cancel_at_period_end,
        current_members=current_members,
        available_seats=available_seats,
        excess_users=excess_users,
        within_paid_period=within_paid_period,
    )


async def get_stripe_customer_portal(context_organization, db: AsyncSession):
    """Get Stripe Customer Portal URL (admin only)."""

    organization = context_organization

    if not organization.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer found")

    return_url = f"{settings.FRONTEND_URL}/settings/organization"
    portal_url = await get_customer_portal_url(organization.stripe_customer_id, return_url)

    return CustomerPortalResponse(url=portal_url)


async def get_latest_subscription_by_org(organization_id: str, db: AsyncSession):
    return await repository.get_latest_by_organization(db, organization_id)


TRACKED_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice_payment.paid",  # Newer Stripe event fired alongside invoice.paid
]


async def process_webhook_event(event: dict, db: AsyncSession):
    """Extract customer_id per event type and sync."""

    event_type = event["type"]

    if event_type not in TRACKED_EVENTS:
        logger.info(f"Ignoring event: {event_type}")
        return

    event_object = event["data"]["object"]
    customer_id = None

    # Extract customer_id based on event type
    if event_type == "checkout.session.completed":
        # Checkout Session object
        customer_id = event_object.get("customer")

    elif event_type.startswith("customer.subscription."):
        # Subscription object
        customer_id = event_object.get("customer")

    elif event_type.startswith("invoice.") or event_type.startswith("invoice_payment."):
        # Invoice / InvoicePayment object
        customer_id = event_object.get("customer")

    if not customer_id:
        logger.warning(f"No customer in {event_type}")
        return

    logger.info(f"Processing {event_type} for customer {customer_id}")

    await sync_stripe_to_postgres(customer_id, db)

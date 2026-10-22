import logging

from fastapi import APIRouter, HTTPException, Request

from app.billing import stripe_gateway
from app.billing.models import WebhookStatusResponse
from app.billing.service import process_webhook_event
from app.postgres import get_db_session

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)

# Events we track (all subscription-related)


@router.post("/stripe", response_model=WebhookStatusResponse)
async def stripe_webhook(request: Request):
    """Stripe webhook: treat Stripe as source of truth and sync Postgres from Stripe."""

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(400, "Missing stripe-signature")

    try:
        event = stripe_gateway.construct_event(payload, sig_header)
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    except stripe_gateway.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")

    async for db in get_db_session():
        await process_webhook_event(event, db)

    return {"status": "success"}

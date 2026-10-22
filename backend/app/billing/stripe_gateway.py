"""Stripe SDK boundary. Calls retain their existing synchronous behavior."""

import stripe
from stripe import SignatureVerificationError as SignatureVerificationError

from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY


def list_subscriptions(**parameters):
    return stripe.Subscription.list(**parameters)


def create_customer(**parameters):
    return stripe.Customer.create(**parameters)


def delete_customer(customer_id: str):
    return stripe.Customer.delete(customer_id)


def create_checkout(**parameters):
    return stripe.checkout.Session.create(**parameters)


def retrieve_checkout(session_id: str):
    return stripe.checkout.Session.retrieve(session_id)


def modify_subscription(subscription_id: str, **parameters):
    return stripe.Subscription.modify(subscription_id, **parameters)


def cancel_subscription(subscription_id: str):
    return stripe.Subscription.cancel(subscription_id)


def create_portal(**parameters):
    return stripe.billing_portal.Session.create(**parameters)


def construct_event(payload: bytes, signature: str):
    return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)

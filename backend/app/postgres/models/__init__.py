"""PostgreSQL models for application state (not analytics data).

This module explicitly registers domain SQLModel classes for Alembic migrations. Runtime callers import models from their owning domains.
"""

from app.billing.models import Subscription
from app.chat.models import Conversation, Message
from app.crm.import_models import (
    CrmContactImport,
    CrmContactList,
    CrmOwnerNote,
    CrmOwnerWorkspace,
    CrmPropertyContactClaim,
)
from app.crm.inbound_models import (
    CrmInboundActivity,
    CrmInboundContact,
    CrmInboundEnquiry,
    CrmInboundImport,
    CrmInboundMatch,
    CrmInboundNote,
)
from app.crm.models import CrmAgentAreaPreference, CrmLeadNote, CrmLeadWorkspace, CrmSavedLead
from app.feasibility.models import FeasibilitySavedStudy
from app.news.models import NewsArticle
from app.organization.models import Organization, OrganizationInvitation, OrganizationMembership
from app.token_usage.models import UserTokenUsage

__all__ = [
    # Auth & User Management
    "Organization",
    "OrganizationMembership",
    "OrganizationInvitation",
    "UserTokenUsage",
    # Billing
    "Subscription",
    # News
    "NewsArticle",
    # Chat
    "Conversation",
    "Message",
    # CRM
    "CrmSavedLead",
    "CrmLeadWorkspace",
    "CrmLeadNote",
    "CrmAgentAreaPreference",
    "CrmContactImport",
    "CrmContactList",
    "CrmPropertyContactClaim",
    "CrmOwnerWorkspace",
    "CrmOwnerNote",
    "CrmInboundImport",
    "CrmInboundContact",
    "CrmInboundEnquiry",
    "CrmInboundNote",
    "CrmInboundActivity",
    "CrmInboundMatch",
    # Feasibility
    "FeasibilitySavedStudy",
]

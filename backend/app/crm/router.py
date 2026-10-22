import json
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dependencies import AuthContext, require_active_subscription
from app.crm.import_models import (
    CrmContactImportRename,
    CrmContactImportResponse,
    CrmContactListDetail,
    CrmContactListSummary,
    CrmDroppedRowsResponse,
    CrmImportPropertiesResponse,
    CrmLeadImportedContact,
    CrmOwnerBulkUpdate,
    CrmOwnerProfile,
    CrmOwnerRegistryResponse,
    CrmOwnerWorkspaceSummary,
)
from app.crm.imports import (
    delete_contact_import,
    list_contact_imports,
    list_dropped_rows,
    list_import_properties,
    process_contact_import,
    rename_contact_import,
)
from app.crm.inbound import (
    add_inbound_activity,
    add_inbound_note,
    create_inbound_enquiry,
    get_inbound_enquiry,
    list_inbound_enquiries,
    update_inbound_enquiry,
)
from app.crm.inbound_imports import (
    delete_inbound_import,
    list_inbound_imports,
    preview_inbound_import,
    process_inbound_import,
)
from app.crm.inbound_models import (
    InboundActivityCreate,
    InboundEnquiryCreate,
    InboundEnquiryListResponse,
    InboundEnquiryResponse,
    InboundEnquiryUpdate,
    InboundImportPreviewResponse,
    InboundImportResponse,
    InboundMatchesResponse,
    InboundMatchResponse,
    InboundMatchUpdate,
    InboundNoteCreate,
)
from app.crm.matching import (
    get_inbound_matches,
    refresh_inbound_matches,
    update_inbound_match_state,
)
from app.crm.models import (
    CrmAreaPreferencesResponse,
    CrmAreaPreferencesUpdate,
    CrmDashboardResponse,
    CrmLeadDetailResponse,
    CrmNoteCreate,
    CrmPropertyReportResponse,
    CrmRenewalArea,
    CrmRenewalLeadsResponse,
    CrmRenewalsResponse,
    CrmSavedLeadResponse,
    CrmWorkspaceResponse,
    CrmWorkspaceSummary,
    CrmWorkspaceUpdate,
)
from app.crm.opportunities import get_dashboard, get_lead_detail
from app.crm.owners import (
    get_contact_list,
    get_owner_profile,
    get_owner_registry,
    list_contact_lists,
    list_lead_imported_contacts,
)
from app.crm.renewal_leads import (
    get_area_preferences,
    list_renewal_areas,
    list_renewal_leads,
    list_renewals,
    open_renewal_lead,
    until_disconnected,
    update_area_preferences,
)
from app.crm.reports import get_property_report
from app.crm.workspaces import (
    add_lead_note,
    bulk_update_owner_workspaces,
    delete_lead_note,
    delete_saved_lead,
    get_lead_workspace,
    list_lead_workspaces,
    list_owner_workspaces,
    list_saved_leads,
    save_lead,
    update_lead_workspace,
)
from app.postgres import get_db_session

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])


def _auth_ids(auth: AuthContext) -> tuple[str, str]:
    return str(auth.user.id), str(auth.organization.id)


@router.post("/inbound/enquiries", response_model=InboundEnquiryResponse, status_code=201)
async def crm_create_inbound_enquiry(
    payload: InboundEnquiryCreate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a manually entered buyer, tenant, seller, or landlord enquiry."""
    user_id, organization_id = _auth_ids(context)
    return await create_inbound_enquiry(
        db, payload=payload, user_id=user_id, organization_id=organization_id
    )


@router.get("/inbound/enquiries", response_model=InboundEnquiryListResponse)
async def crm_inbound_enquiries(
    intent_group: str | None = Query(default=None, pattern="^(demand|instructions)$"),
    workspace: str | None = Query(default=None, pattern="^(inbox|unqualified|qualified|closed)$"),
    status: str | None = Query(default=None, max_length=40),
    search: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List organization-visible inbound enquiries and review exceptions."""
    _, organization_id = _auth_ids(context)
    return await list_inbound_enquiries(
        db,
        organization_id=organization_id,
        intent_group=intent_group,
        workspace=workspace,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/inbound/enquiries/{enquiry_id}", response_model=InboundEnquiryResponse)
async def crm_inbound_enquiry(
    enquiry_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    return await get_inbound_enquiry(db, enquiry_id, organization_id)


@router.patch("/inbound/enquiries/{enquiry_id}", response_model=InboundEnquiryResponse)
async def crm_update_inbound_enquiry(
    enquiry_id: int,
    payload: InboundEnquiryUpdate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, organization_id = _auth_ids(context)
    return await update_inbound_enquiry(
        db,
        enquiry_id=enquiry_id,
        organization_id=organization_id,
        user_id=user_id,
        payload=payload,
    )


@router.post("/inbound/enquiries/{enquiry_id}/notes", response_model=InboundEnquiryResponse)
async def crm_add_inbound_note(
    enquiry_id: int,
    payload: InboundNoteCreate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, organization_id = _auth_ids(context)
    return await add_inbound_note(
        db,
        enquiry_id=enquiry_id,
        organization_id=organization_id,
        user_id=user_id,
        body=payload.body,
    )


@router.post("/inbound/enquiries/{enquiry_id}/activities", response_model=InboundEnquiryResponse)
async def crm_add_inbound_activity(
    enquiry_id: int,
    payload: InboundActivityCreate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, organization_id = _auth_ids(context)
    return await add_inbound_activity(
        db,
        enquiry_id=enquiry_id,
        organization_id=organization_id,
        user_id=user_id,
        activity_type=payload.activity_type,
        body=payload.body,
    )


@router.get("/inbound/enquiries/{enquiry_id}/matches", response_model=InboundMatchesResponse)
async def crm_inbound_matches(
    enquiry_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    return await get_inbound_matches(db, enquiry_id, organization_id)


@router.post(
    "/inbound/enquiries/{enquiry_id}/matches/refresh",
    response_model=InboundMatchesResponse,
)
async def crm_refresh_inbound_matches(
    enquiry_id: int,
    expand_units: bool = Query(default=False),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    return await refresh_inbound_matches(
        db,
        enquiry_id=enquiry_id,
        organization_id=organization_id,
        expand_units=expand_units,
    )


@router.patch("/inbound/matches/{match_id}", response_model=InboundMatchResponse)
async def crm_update_inbound_match(
    match_id: int,
    payload: InboundMatchUpdate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    return await update_inbound_match_state(
        db, match_id=match_id, organization_id=organization_id, state=payload.state
    )


@router.post("/inbound/imports", response_model=InboundImportResponse, status_code=201)
async def crm_inbound_import(
    file: UploadFile = File(...),
    name: str = Form(default="", max_length=200),
    campaign_context: str = Form(default="", max_length=4_000),
    mapping_json: str = Form(default="", max_length=50_000),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Normalize a campaign or CRM lead export into actionable enquiries."""
    user_id, organization_id = _auth_ids(context)
    try:
        mapping_override = json.loads(mapping_json) if mapping_json else None
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid import mapping") from exc
    return await process_inbound_import(
        db,
        upload=file,
        name=name,
        user_id=user_id,
        organization_id=organization_id,
        campaign_context=campaign_context,
        mapping_override=mapping_override,
    )


@router.post("/inbound/imports/preview", response_model=InboundImportPreviewResponse)
async def crm_preview_inbound_import(
    file: UploadFile = File(...),
    campaign_context: str = Form(default="", max_length=4_000),
    context: AuthContext = Depends(require_active_subscription),
):
    """Inspect headers and samples without storing contacts or creating enquiries."""
    return await preview_inbound_import(file, campaign_context)


@router.get("/inbound/imports", response_model=list[InboundImportResponse])
async def crm_inbound_imports(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    return await list_inbound_imports(db, organization_id)


@router.delete("/inbound/imports/{import_id}", status_code=204)
async def crm_delete_inbound_import(
    import_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    _, organization_id = _auth_ids(context)
    await delete_inbound_import(db, import_id, organization_id)
    return Response(status_code=204)


@router.post(
    "/contact-imports",
    response_model=CrmContactImportResponse,
    status_code=201,
)
async def crm_contact_import(
    file: UploadFile = File(...),
    name: str = Form(default="", max_length=200),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Upload, normalize, verify, and publish an owner-contact workbook."""
    user_id, organization_id = _auth_ids(context)
    return await process_contact_import(
        db,
        upload=file,
        name=name,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.get(
    "/contact-imports",
    response_model=list[CrmContactImportResponse],
)
async def crm_contact_imports(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List contact imports for the signed-in organization."""
    _, organization_id = _auth_ids(context)
    return await list_contact_imports(db, organization_id=organization_id)


@router.patch(
    "/contact-imports/{import_id}",
    response_model=CrmContactImportResponse,
)
async def crm_rename_contact_import(
    import_id: int,
    payload: CrmContactImportRename,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Rename an import and its corresponding Saved folder."""
    _, organization_id = _auth_ids(context)
    return await rename_contact_import(
        db,
        import_id=import_id,
        name=payload.name,
        organization_id=organization_id,
    )


@router.delete("/contact-imports/{import_id}", status_code=204)
async def crm_delete_contact_import(
    import_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete one source import, its Saved folder, and its imported claims."""
    _, organization_id = _auth_ids(context)
    await delete_contact_import(
        db,
        import_id=import_id,
        organization_id=organization_id,
    )
    return Response(status_code=204)


@router.get(
    "/contact-imports/{import_id}/dropped-rows",
    response_model=CrmDroppedRowsResponse,
)
async def crm_contact_import_dropped_rows(
    import_id: int,
    reason: str | None = Query(
        default=None,
        pattern="^(no_name|not_buyer_seller|not_a_sale|no_unit)$",
    ),
    search: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Rows of one import that were not kept, with the file's values and the reason.

    Read from the dropped-rows log saved at upload; `available` is false for imports made
    before the log existed.
    """
    _, organization_id = _auth_ids(context)
    return await list_dropped_rows(
        db,
        import_id=import_id,
        organization_id=organization_id,
        reason=reason,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/contact-imports/{import_id}/properties",
    response_model=CrmImportPropertiesResponse,
)
async def crm_contact_import_properties(
    import_id: int,
    search: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Distinct properties (building + unit) of one import, each with its kept rows."""
    _, organization_id = _auth_ids(context)
    return await list_import_properties(
        db,
        import_id=import_id,
        organization_id=organization_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/contact-lists",
    response_model=list[CrmContactListSummary],
)
async def crm_contact_lists(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List organization owner-contact folders."""
    _, organization_id = _auth_ids(context)
    return await list_contact_lists(db, organization_id=organization_id)


@router.get(
    "/contact-lists/{list_id}",
    response_model=CrmContactListDetail,
)
async def crm_contact_list(
    list_id: int,
    status: str | None = Query(
        default=None,
        pattern=(
            "^(verified_current_owner|probable_current_owner|unit_linked_unverified|"
            "former_owner|conflicting_claim|insufficient_property_data|"
            "registry_not_observed|unmatched)$"
        ),
    ),
    search: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return one curated owner-contact folder and its verification results."""
    _, organization_id = _auth_ids(context)
    return await get_contact_list(
        db,
        list_id=list_id,
        organization_id=organization_id,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/owner-registry",
    response_model=CrmOwnerRegistryResponse,
)
async def crm_owner_registry(
    scope: str = Query(
        default="all",
        pattern="^(all|rental_ready|registry|prior_owners)$",
    ),
    list_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(
        default=None,
        pattern=(
            "^(verified_current_owner|probable_current_owner|unit_linked_unverified|"
            "former_owner|conflicting_claim|insufficient_property_data|"
            "registry_not_observed|unmatched)$"
        ),
    ),
    search: str | None = Query(default=None, max_length=160),
    lease_window: str | None = Query(
        default=None,
        pattern="^(expired|expiring_30|expiring_60|expiring_90)$",
    ),
    portfolio_only: bool = Query(default=False),
    min_rent_aed: int | None = Query(default=None, ge=0, le=10_000_000_000),
    max_rent_aed: int | None = Query(default=None, ge=0, le=10_000_000_000),
    min_value_aed: int | None = Query(default=None, ge=0, le=100_000_000_000),
    max_value_aed: int | None = Query(default=None, ge=0, le=100_000_000_000),
    min_properties: int | None = Query(default=None, ge=1, le=10_000),
    next_event_days: int | None = Query(default=None, ge=0, le=3_650),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return deduplicated owner profiles grouped into actionable CRM folders.

    `min_properties` keeps owners with at least that many units; `next_event_days` keeps owners
    whose next lease ends between today and that many days from now.
    """
    _, organization_id = _auth_ids(context)
    return await get_owner_registry(
        db,
        organization_id=organization_id,
        list_id=list_id,
        scope=scope,
        status=status,
        search=search,
        lease_window=lease_window,
        portfolio_only=portfolio_only,
        min_rent_aed=min_rent_aed,
        max_rent_aed=max_rent_aed,
        min_value_aed=min_value_aed,
        max_value_aed=max_value_aed,
        limit=limit,
        offset=offset,
        min_properties=min_properties,
        next_event_days=next_event_days,
    )


@router.get(
    "/owner-workspaces",
    response_model=list[CrmOwnerWorkspaceSummary],
)
async def crm_owner_workspaces(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List agent state and note summaries for owner profiles."""
    user_id, organization_id = _auth_ids(context)
    return await list_owner_workspaces(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.patch(
    "/owner-workspaces/bulk",
    response_model=list[CrmOwnerWorkspaceSummary],
)
async def crm_bulk_update_owner_workspaces(
    payload: CrmOwnerBulkUpdate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Apply pipeline, highlight, or note updates to selected owner profiles."""
    user_id, organization_id = _auth_ids(context)
    return await bulk_update_owner_workspaces(
        db,
        payload=payload,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.get(
    "/owners/{owner_key}",
    response_model=CrmOwnerProfile,
)
async def crm_owner_profile(
    owner_key: str,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return one owner and every property accumulated across organization imports."""
    _, organization_id = _auth_ids(context)
    return await get_owner_profile(
        db,
        organization_id=organization_id,
        owner_key=owner_key,
    )


@router.get("/dashboard", response_model=CrmDashboardResponse)
async def crm_dashboard(
    status: str | None = Query(
        default=None, pattern="^(expired|expiring_30|expiring_60|expiring_90)$"
    ),
    priority: str | None = Query(default=None, pattern="^(urgent|high|medium)$"),
    asset_class: str | None = Query(default=None, pattern="^(residential|commercial|other)$"),
    property_type: str | None = Query(default=None, max_length=80),
    area: str | None = Query(default=None, max_length=160),
    project: str | None = Query(default=None, max_length=160),
    building: str | None = Query(default=None, max_length=200),
    search: str | None = Query(default=None, max_length=160),
    breakdown: str = Query(default="area", pattern="^(area|project|building)$"),
    expired_days: int = Query(default=365, ge=30, le=1825),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
):
    """Return actionable lease-expiry leads and location summaries."""
    return await get_dashboard(
        status=status,
        priority=priority,
        asset_class=asset_class,
        property_type=property_type,
        area=area,
        project=project,
        building=building,
        search=search,
        breakdown=breakdown,
        expired_days=expired_days,
        limit=limit,
        offset=offset,
        as_of=as_of,
    )


@router.get("/area-preferences", response_model=CrmAreaPreferencesResponse)
async def crm_area_preferences(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return the current agent's renewal-lead areas and lease-expiry windows."""
    user_id, organization_id = _auth_ids(context)
    return await get_area_preferences(db, user_id=user_id, organization_id=organization_id)


@router.put("/area-preferences", response_model=CrmAreaPreferencesResponse)
async def crm_update_area_preferences(
    payload: CrmAreaPreferencesUpdate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Replace the current agent's renewal-lead areas and lease-expiry windows."""
    user_id, organization_id = _auth_ids(context)
    return await update_area_preferences(
        db,
        payload=payload,
        user_id=user_id,
        organization_id=organization_id,
    )


async def _until_disconnect(request: Request, work):
    """Answer `work`, or stop its lease queries if the agent abandons the request."""
    result = await until_disconnected(request.is_disconnected, work)
    # 499: the client closed the request; nobody reads this response.
    return Response(status_code=499) if result is None else result


@router.get("/renewals", response_model=CrmRenewalsResponse)
async def crm_renewals(
    request: Request,
    window: str | None = Query(
        default=None, pattern="^(vacant|expired|expiring_30|expiring_60|expiring_90)$"
    ),
    area: str | None = Query(default=None, max_length=160),
    property_type: str | None = Query(default=None, max_length=80),
    project: str | None = Query(default=None, max_length=160),
    building: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
):
    """Assets › Renewals: every lease in the dataset, narrowed by the tab's filters."""
    return await _until_disconnect(
        request,
        list_renewals(
            window=window,
            area=area,
            property_type=property_type,
            project=project,
            building=building,
            limit=limit,
            offset=offset,
            as_of=as_of,
        ),
    )


@router.get("/renewal-areas", response_model=list[CrmRenewalArea])
async def crm_renewal_areas(
    request: Request,
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
):
    """List areas in the lease-expiry dataset with their leases ending in the next 90 days."""
    return await _until_disconnect(request, list_renewal_areas(as_of=as_of))


@router.get("/renewal-leads", response_model=CrmRenewalLeadsResponse)
async def crm_renewal_leads(
    request: Request,
    kind: str | None = Query(default=None, pattern="^(vacant|upcoming)$"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return lease-expiry leads in the current agent's areas and windows.

    kind=vacant: units whose lease ended with no later lease registered (Leads › Vacant units).
    kind=upcoming: leases still running (the Inbox).
    """
    user_id, organization_id = _auth_ids(context)
    return await _until_disconnect(
        request,
        list_renewal_leads(
            db,
            kind=kind,
            user_id=user_id,
            organization_id=organization_id,
            limit=limit,
            offset=offset,
            as_of=as_of,
        ),
    )


@router.post("/renewal-leads/{lease_id}/lead", response_model=InboundEnquiryResponse)
async def crm_open_renewal_lead(
    lease_id: str,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Open a Vitevue lease as the agency's shared lead for its unit (created on first open)."""
    user_id, organization_id = _auth_ids(context)
    return await open_renewal_lead(
        db, lease_id=lease_id, user_id=user_id, organization_id=organization_id
    )


@router.get("/leads/{lead_id}", response_model=CrmLeadDetailResponse)
async def crm_lead_detail(
    lead_id: str,
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
):
    """Return the evidence timeline and recommended action for one lead."""
    return await get_lead_detail(lead_id, as_of=as_of)


@router.get(
    "/leads/{lead_id}/contacts",
    response_model=list[CrmLeadImportedContact],
)
async def crm_lead_contacts(
    lead_id: str,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return imported contacts linked to one exact lease-opportunity property."""
    _, organization_id = _auth_ids(context)
    return await list_lead_imported_contacts(
        db,
        lead_id=lead_id,
        organization_id=organization_id,
    )


@router.get("/leads/{lead_id}/report", response_model=CrmPropertyReportResponse)
async def crm_property_report(
    lead_id: str,
    as_of: date | None = Query(default=None),
    context: AuthContext = Depends(require_active_subscription),
):
    """Build a one-year property report with sales, rentals, and indicative analysis."""
    return await get_property_report(lead_id, as_of=as_of)


@router.get("/workspaces", response_model=list[CrmWorkspaceSummary])
async def crm_workspaces(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List the current agent's persisted CRM state for queue decoration."""
    user_id, organization_id = _auth_ids(context)
    return await list_lead_workspaces(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.get("/workspaces/{lead_id}", response_model=CrmWorkspaceResponse)
async def crm_lead_workspace(
    lead_id: str,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Return status, contact enrichment, follow-up, tags, and notes for one lead."""
    user_id, organization_id = _auth_ids(context)
    return await get_lead_workspace(
        db,
        lead_id=lead_id,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.patch("/workspaces/{lead_id}", response_model=CrmWorkspaceResponse)
async def crm_update_workspace(
    lead_id: str,
    payload: CrmWorkspaceUpdate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Persist the current agent's working state for one lead."""
    user_id, organization_id = _auth_ids(context)
    return await update_lead_workspace(
        db,
        lead_id=lead_id,
        payload=payload,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.post("/workspaces/{lead_id}/notes", response_model=CrmWorkspaceResponse)
async def crm_add_note(
    lead_id: str,
    payload: CrmNoteCreate,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Add a timestamped agent note and return the refreshed workspace."""
    user_id, organization_id = _auth_ids(context)
    return await add_lead_note(
        db,
        lead_id=lead_id,
        payload=payload,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.delete("/notes/{note_id}", status_code=204)
async def crm_delete_note(
    note_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete one note owned by the current user and organization."""
    user_id, organization_id = _auth_ids(context)
    await delete_lead_note(
        db,
        note_id=note_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    return Response(status_code=204)


@router.get("/saved-leads", response_model=list[CrmSavedLeadResponse])
async def crm_saved_leads(
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """List saved CRM prospects for the current user and organization."""
    user_id, organization_id = _auth_ids(context)
    return await list_saved_leads(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.post(
    "/saved-leads/{lead_id}",
    response_model=CrmSavedLeadResponse,
    status_code=201,
)
async def crm_save_lead(
    lead_id: str,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Save or refresh a CRM prospect for the current user."""
    user_id, organization_id = _auth_ids(context)
    return await save_lead(
        db,
        lead_id=lead_id,
        user_id=user_id,
        organization_id=organization_id,
    )


@router.delete("/saved-leads/{saved_id}", status_code=204)
async def crm_delete_saved_lead(
    saved_id: int,
    context: AuthContext = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a prospect from the current user's saved list."""
    user_id, organization_id = _auth_ids(context)
    await delete_saved_lead(
        db,
        saved_id=saved_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    return Response(status_code=204)

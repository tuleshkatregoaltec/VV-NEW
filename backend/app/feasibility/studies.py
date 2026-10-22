import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.feasibility import repository
from app.feasibility.generation import generate_seeded_study
from app.feasibility.models import (
    FeasibilityChatState,
    FeasibilitySavedStudy,
    FeasibilitySavedStudyListItem,
    FeasibilitySavedStudyResponse,
    FeasibilityStudyCreateRequest,
    FeasibilityStudyUpdateRequest,
    GenerateStudyRequest,
    PlotResearch,
    StudyContext,
)
from app.plots.models import PlotDetails

VALID_DEVELOPMENT_MODELS: set[str] = {
    "build_to_sell_residential",
    "build_to_rent_residential",
    "build_to_lease_commercial",
}


def _dump_model(model) -> dict:
    return model.model_dump(mode="json")


def _dump_json_object(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _development_model_from_assumptions(assumptions: dict[str, Any]) -> str | None:
    development_model = assumptions.get("developmentModel")
    if isinstance(development_model, str) and development_model in VALID_DEVELOPMENT_MODELS:
        return development_model
    return None


def _empty_chat_state() -> dict:
    return FeasibilityChatState().model_dump(mode="json")


def _study_response(study: FeasibilitySavedStudy) -> FeasibilitySavedStudyResponse:
    if study.id is None:
        raise HTTPException(status_code=500, detail="Saved study is missing an id")
    return FeasibilitySavedStudyResponse(
        id=study.id,
        name=study.name,
        plot_number=study.plot_number,
        development_model=study.development_model,
        assumptions=study.assumptions,
        plot_data=PlotDetails.model_validate(study.plot_data),
        research=PlotResearch.model_validate(study.research),
        study_context=StudyContext.model_validate(study.study_context),
        chat_state=FeasibilityChatState.model_validate(study.chat_state or _empty_chat_state()),
        created_at=study.created_at,
        updated_at=study.updated_at,
    )


def _study_list_item(study: FeasibilitySavedStudy) -> FeasibilitySavedStudyListItem:
    if study.id is None:
        raise HTTPException(status_code=500, detail="Saved study is missing an id")
    return FeasibilitySavedStudyListItem(
        id=study.id,
        name=study.name,
        plot_number=study.plot_number,
        development_model=study.development_model,
        plot_data=PlotDetails.model_validate(study.plot_data),
        created_at=study.created_at,
        updated_at=study.updated_at,
    )


async def _get_saved_study_or_404(
    db: AsyncSession,
    *,
    study_id: int,
    user_id: str,
    organization_id: str,
) -> FeasibilitySavedStudy:
    study = await repository.find_study(db, organization_id, study_id, user_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Feasibility study not found")
    return study


async def list_saved_studies(
    db: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
) -> list[FeasibilitySavedStudyListItem]:
    result = await repository.list_studies(db, organization_id, user_id)
    return [_study_list_item(study) for study in result]


async def create_saved_study(
    db: AsyncSession,
    *,
    request: FeasibilityStudyCreateRequest,
    user_id: str,
    organization_id: str,
) -> FeasibilitySavedStudyResponse:
    generated = await generate_seeded_study(
        GenerateStudyRequest(
            plot_number=request.plot_number,
            usage_override=request.usage_override,
            development_model=request.development_model,
        )
    )
    assumptions = generated.assumptions.model_copy(
        update={"developmentModel": request.development_model}
    )
    now = datetime.now(timezone.utc)
    study = FeasibilitySavedStudy(
        user_id=user_id,
        organization_id=organization_id,
        name=request.name.strip(),
        plot_number=generated.plot_data.plot_number,
        development_model=request.development_model,
        assumptions=_dump_model(assumptions),
        plot_data=_dump_model(generated.plot_data),
        research=_dump_model(generated.research),
        study_context=_dump_model(generated.study_context),
        chat_state=_empty_chat_state(),
        created_at=now,
        updated_at=now,
    )
    repository.add_study(db, study)
    await db.commit()
    await repository.refresh_study(db, study)
    return _study_response(study)


async def get_saved_study(
    db: AsyncSession,
    *,
    study_id: int,
    user_id: str,
    organization_id: str,
) -> FeasibilitySavedStudyResponse:
    return _study_response(
        await _get_saved_study_or_404(
            db,
            study_id=study_id,
            user_id=user_id,
            organization_id=organization_id,
        )
    )


async def update_saved_study(
    db: AsyncSession,
    *,
    study_id: int,
    request: FeasibilityStudyUpdateRequest,
    user_id: str,
    organization_id: str,
) -> FeasibilitySavedStudyResponse:
    study = await _get_saved_study_or_404(
        db,
        study_id=study_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    if request.name is not None:
        study.name = request.name.strip()
    if request.development_model is not None:
        study.development_model = request.development_model
    if request.assumptions is not None:
        study.assumptions = _dump_json_object(request.assumptions)
        assumed_development_model = _development_model_from_assumptions(request.assumptions)
        if request.development_model is None and assumed_development_model is not None:
            study.development_model = assumed_development_model
    if request.plot_data is not None:
        study.plot_data = _dump_model(request.plot_data)
        study.plot_number = request.plot_data.plot_number
    if request.research is not None:
        study.research = _dump_model(request.research)
    if request.study_context is not None:
        study.study_context = _dump_model(request.study_context)
    if request.chat_state is not None:
        study.chat_state = _dump_model(request.chat_state)
    study.updated_at = datetime.now(timezone.utc)
    repository.add_study(db, study)
    await db.commit()
    await repository.refresh_study(db, study)
    return _study_response(study)


async def delete_saved_study(
    db: AsyncSession,
    *,
    study_id: int,
    user_id: str,
    organization_id: str,
) -> None:
    study = await _get_saved_study_or_404(
        db,
        study_id=study_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    await repository.delete_study(db, study)
    await db.commit()

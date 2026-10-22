from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.feasibility import studies
from app.feasibility.assumptions import build_assumptions_scaffold
from app.feasibility.models import (
    FeasibilityStudyCreateRequest,
    FeasibilityStudyUpdateRequest,
    GenerateStudyResponse,
    PlotResearch,
    StudyContext,
)
from app.plots.models import PlotDetails


def generated_study():
    plot = PlotDetails(
        plot_number="3460688",
        community_name="Test Area",
        project_name="Test",
        plot_area_sqm=1000,
        max_gfa_sqm=4000,
        max_gfa_sqft=43055,
        max_height="G+20",
        max_coverage="60%",
        gfa_type="Residential",
        inferred_usage="residential",
        is_verified=True,
    )
    assumptions = build_assumptions_scaffold(plot, "residential")
    return GenerateStudyResponse(
        plot_data=plot,
        assumptions=assumptions,
        research=PlotResearch(
            plot_number=plot.plot_number,
            usage="residential",
            market_area="Test Area",
            headline="Test",
            assumptions=assumptions,
        ),
        study_context=StudyContext(
            headline="Test", project_tier="premium", summary_points=[], market_summary=""
        ),
    )


async def test_study_commits_preserve_arbitrary_json_and_both_ownership_scopes(
    pg_session, monkeypatch
):
    user, org = str(uuid4()), str(uuid4())
    monkeypatch.setattr(studies, "generate_seeded_study", AsyncMock(return_value=generated_study()))
    created = await studies.create_saved_study(
        pg_session,
        request=FeasibilityStudyCreateRequest(
            name="  Persistent study  ",
            plot_number="3460688",
            development_model="build_to_sell_residential",
        ),
        user_id=user,
        organization_id=org,
    )
    assert created.name == "Persistent study"
    arbitrary = {
        "developmentModel": "build_to_rent_residential",
        "future": {"zero": 0, "nested": [None, "x"]},
    }
    await studies.update_saved_study(
        pg_session,
        study_id=created.id,
        request=FeasibilityStudyUpdateRequest(assumptions=arbitrary),
        user_id=user,
        organization_id=org,
    )
    pg_session.expire_all()
    loaded = await studies.get_saved_study(
        pg_session, study_id=created.id, user_id=user, organization_id=org
    )
    assert loaded.assumptions == arbitrary
    assert loaded.development_model == "build_to_rent_residential"
    for denied_user, denied_org in [(str(uuid4()), org), (user, str(uuid4()))]:
        assert (
            await studies.list_saved_studies(
                pg_session, user_id=denied_user, organization_id=denied_org
            )
            == []
        )
        with pytest.raises(HTTPException) as error:
            await studies.delete_saved_study(
                pg_session, study_id=created.id, user_id=denied_user, organization_id=denied_org
            )
        assert error.value.status_code == 404
    await studies.delete_saved_study(
        pg_session, study_id=created.id, user_id=user, organization_id=org
    )
    assert await studies.list_saved_studies(pg_session, user_id=user, organization_id=org) == []

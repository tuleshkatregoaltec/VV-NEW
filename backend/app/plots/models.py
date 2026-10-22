from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DDAPlotFeature(BaseModel):
    """DDA GIS plot polygon for map overlay."""

    plot_number: str
    project_name: str
    community_name: str
    plot_area_sqm: float
    coordinates: List[List[float]]  # closed ring [[lng, lat], ...]
    land_use_summary: List[str] = []
    max_gfa_sqm: Optional[float] = None
    max_height: Optional[str] = None
    max_coverage: Optional[str] = None
    land_use: Optional[str] = None
    gfa_type: Optional[str] = None
    is_verified: bool = False
    valuation_rate_min_aed_sqft: Optional[float] = None
    valuation_rate_max_aed_sqft: Optional[float] = None
    valuation_min_aed: Optional[float] = None
    valuation_max_aed: Optional[float] = None
    valuation_confidence: Optional[str] = None
    valuation_note: Optional[str] = None
    development_status: str = "no_observed_asset"
    development_confidence: str = "low"
    development_method: str = "no_observed_asset"
    development_note: Optional[str] = None
    linked_asset_count: int = 0
    linked_physical_asset_count: int = 0
    subdivision_plot_count: int = 0
    subdivision_confirmed_count: int = 0


class DDAOutlineFeature(BaseModel):
    """Coarse DDA planning outline for low-zoom map overlay."""

    name: str
    kind: str
    plot_count: int
    total_plot_area_sqm: float
    total_gfa_sqm: float
    coordinates: List[List[float]]  # closed ring [[lng, lat], ...]
    land_use_summary: List[str] = []
    developed_confirmed_count: int = 0
    developed_probable_count: int = 0
    planned_unbuilt_count: int = 0
    no_observed_asset_count: int = 0
    non_developable_count: int = 0
    developed_share: float = 0.0


Usage = Literal["residential", "commercial", "mixed_use", "unknown"]


class GISLandUse(BaseModel):
    type: str
    use: str


class Plot(BaseModel):
    plot_number: str
    community_name: str
    project_name: str
    plot_area_sqm: float
    max_gfa_sqm: float
    max_height: str
    max_coverage: str
    gfa_type: str
    is_verified: bool
    land_use_summary: list[str] = Field(default_factory=list)


class LandPlot(Plot):
    """Backward-compatible alias for older feasibility imports."""


class PlotDetails(BaseModel):
    plot_number: str
    community_name: str
    project_name: str
    master_developer: str | None = None
    plot_area_sqm: float
    max_gfa_sqm: float
    max_gfa_sqft: float
    max_height: str
    max_coverage: str
    gfa_type: str
    far: float | None = None
    inferred_usage: Usage
    site_plan_issue_date: str | None = None
    site_plan_expiry_date: str | None = None
    is_verified: bool
    verify_comments: str | None = None
    land_use: list[GISLandUse] = Field(default_factory=list)
    general_notes: list[str] = Field(default_factory=list)
    setbacks: dict[str, str] = Field(default_factory=dict)
    coordinates: list[list[float]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LandSaleEvidence(BaseModel):
    transaction_id: str
    instance_date: str
    area_name: str
    project_name: str
    procedure_name: str
    property_usage: str
    plot_area_sqm: float
    price_aed: float
    price_sqm: float
    price_per_gfa_sqm: float | None = None
    nearest_landmark: str | None = None

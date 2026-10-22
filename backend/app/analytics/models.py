"""Analytics schemas for API responses"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Valid timeframe options
TimeframeDays = Literal[7, 30, 90, 180, 365]


class AnalyticsSummary(BaseModel):
    """Summary KPI metrics for analytics dashboard"""

    transaction_count: int
    total_volume: float
    avg_price: float
    avg_price_sqft: float
    min_price: float
    max_price: float
    volume_change_pct: Optional[float] = None  # % change vs previous period
    count_change_pct: Optional[float] = None


class RentalSummary(BaseModel):
    """Summary KPI metrics for rental analytics"""

    contract_count: int
    total_annual_value: float
    avg_annual_rent: float
    avg_rent_sqft: float
    min_annual_rent: float
    max_annual_rent: float
    value_change_pct: Optional[float] = None
    count_change_pct: Optional[float] = None


class DimensionBreakdown(BaseModel):
    """Breakdown by a specific dimension (property_type, rooms, etc.)"""

    dimension_value: str
    transaction_count: int
    total_volume: float
    avg_price_sqft: float
    pct_of_total: Optional[float] = None


class RentalDimensionBreakdown(BaseModel):
    """Breakdown by a specific dimension for rentals"""

    dimension_value: str
    contract_count: int
    total_annual_value: float
    avg_rent_sqft: float
    pct_of_total: Optional[float] = None


class TrendPoint(BaseModel):
    """Single data point for trend charts"""

    date: date
    count: int
    total_value: float
    avg_value: float
    avg_sqft: float


class AnalyticsResponse(BaseModel):
    """Complete analytics response for dashboard"""

    timeframe_days: int
    property_usage: str
    summary: AnalyticsSummary
    trends: list[TrendPoint]
    by_property_type: list[DimensionBreakdown]
    by_rooms: list[DimensionBreakdown]
    by_reg_type: list[DimensionBreakdown]
    by_master_project: list[DimensionBreakdown]


class RentalAnalyticsResponse(BaseModel):
    """Complete rental analytics response for dashboard"""

    timeframe_days: int
    property_usage: str
    summary: RentalSummary
    trends: list[TrendPoint]
    by_property_type: list[RentalDimensionBreakdown]
    by_master_project: list[RentalDimensionBreakdown]


class MarketTrendPoint(BaseModel):
    """Single data point for the market trend chart"""

    date: date
    transaction_count: int
    avg_price: float
    avg_price_sqm: float


class MarketTrendResponse(BaseModel):
    """Response for the market trend chart endpoint"""

    data: list[MarketTrendPoint]
    total_transactions: int
    avg_price_change_pct: Optional[float] = None
    avg_price_sqm_change_pct: Optional[float] = None


# =============================================================================
# PROJECT ANALYTICS MODELS
# =============================================================================


class ProjectMeta(BaseModel):
    """Metadata from ch_projects + ch_developers"""

    project_name: str
    area_name: Optional[str] = None
    developer_name: Optional[str] = None
    developer_names: list[str] = Field(default_factory=list)
    project_status: Optional[str] = None
    completion_date: Optional[date] = None
    percent_completed: Optional[float] = None
    no_of_units: Optional[int] = None
    no_of_buildings: Optional[int] = None


class ProjectSalesSummary(BaseModel):
    transaction_count: int
    total_volume: float
    avg_price: float
    avg_price_sqm: float
    median_price: float


class ProjectRentalSummary(BaseModel):
    contract_count: int
    total_annual_value: float
    median_annual_rent: float
    median_rent_sqm: float


class RoomAnalytics(BaseModel):
    """Combined sales + rental + yield metrics per bedroom count"""

    rooms: str
    sale_count: int
    avg_sale_price: float
    avg_sale_price_sqm: float
    rental_count: int
    median_annual_rent: float
    median_rent_sqm: float
    gross_yield_pct: Optional[float] = None


class ProjectConfigurationRow(BaseModel):
    market_segment: Literal["all", "off_plan", "secondary"]
    rooms: str
    stock_units: int
    sale_count: int
    sales_last_12m: int
    avg_sale_price: float
    median_sale_price: float
    avg_sale_price_sqm: float
    avg_unit_size_sqm: float
    rental_count: int
    median_annual_rent: float
    median_rent_sqm: float
    gross_yield_pct: Optional[float] = None
    price_change_yoy_pct: Optional[float] = None
    sales_momentum_pct: Optional[float] = None
    absorption_rate_pct: Optional[float] = None
    price_p25: float
    price_p50: float
    price_p75: float


class PropertyTypeBreakdown(BaseModel):
    property_type: str
    count: int
    avg_price: float
    pct_of_total: float


class RegTypeBreakdown(BaseModel):
    reg_type: str
    count: int
    avg_price: float
    pct_of_total: float


class AnalyticsUnitComposition(BaseModel):
    rooms: str
    count: int
    pct_of_total: float
    avg_unit_size_sqm: float = 0.0


class ProjectAnalyticsResponse(BaseModel):
    """Full analytics response for a single project"""

    project: Optional[ProjectMeta] = None
    sales: ProjectSalesSummary
    rentals: ProjectRentalSummary
    overall_gross_yield_pct: Optional[float] = None
    by_rooms: list[RoomAnalytics]
    by_property_type: list[PropertyTypeBreakdown]
    by_reg_type: list[RegTypeBreakdown]
    unit_composition: list[AnalyticsUnitComposition]
    configuration_analysis: list[ProjectConfigurationRow]


class ProjectTrendPoint(BaseModel):
    date: date
    transaction_count: int
    avg_price: float
    avg_price_sqm: float


class ProjectTrendsResponse(BaseModel):
    data: list[ProjectTrendPoint]


class ProjectForecastPoint(BaseModel):
    date: date
    avg_price: float
    avg_price_sqm: float
    series: Literal["historical", "forecast"]


class ProjectForecastResponse(BaseModel):
    data: list[ProjectForecastPoint]
    delivery_date: Optional[date] = None
    source_reg_type: str = "Off-Plan Properties"
    methodology: str


class ProjectPipelinePoint(BaseModel):
    period: date
    existing_stock_units: int
    carried_stock_units: int
    new_pipeline_units: int


class ProjectPipelineResponse(BaseModel):
    area_name: Optional[str] = None
    developer_name: Optional[str] = None
    data: list[ProjectPipelinePoint]


class PriceDistributionBucket(BaseModel):
    """Mean + population std dev for a single bedroom type."""

    rooms: str
    sale_count: int
    mean_price: float
    std_price: float
    log_mean_price: float = 0.0
    log_std_price: float = 0.0
    p05_price: float = 0.0
    p25_price: float = 0.0
    p50_price: float = 0.0
    p75_price: float = 0.0
    p95_price: float = 0.0
    mean_price_sqm: float
    std_price_sqm: float


class ProjectPriceDistributionResponse(BaseModel):
    """Price distribution by bedroom type, including an all-rooms aggregate."""

    all: Optional[PriceDistributionBucket] = None
    by_rooms: list[PriceDistributionBucket]


class ProjectSearchItem(BaseModel):
    name: str
    filter_type: str  # "master" | "virtual_master" | "project"


class ProjectSearchResponse(BaseModel):
    projects: list[ProjectSearchItem]


class SubProjectItem(BaseModel):
    name: str
    transaction_count: int


class SubProjectsResponse(BaseModel):
    sub_projects: list[SubProjectItem]


TrendInterval = Literal["day", "week", "month"]


# --- Database-grounded market metrics -------------------------------------------------------

MarketMetricName = Literal[
    "count",
    "total_aed",
    "mean_aed",
    "median_aed",
    "p25_aed",
    "p75_aed",
    "mean_aed_per_sqm",
    "median_aed_per_sqm",
    "weighted_aed_per_sqm",
    "mean_aed_per_sqft",
    "median_aed_per_sqft",
    "weighted_aed_per_sqft",
    "total_contract_aed",
    "median_contract_aed",
]
# area = official DLD district; community = evidence-placed community (canonical geography).
MarketMetricGroup = Literal[
    "area",
    "community",
    "master_project",
    "project",
    "building",
    "month",
    "bedrooms",
    "property_type",
    "price_band",
    "registration",
    "contract_version",
]
MarketPropertyType = Literal["apartment", "hotel_apartment", "villa", "office", "shop", "land"]
MarketBedrooms = Literal["studio", "1", "2", "3", "4", "5", "6", "7"]


class MarketPeriodRequest(BaseModel):
    """A calendar period, resolved in the Asia/Dubai business timezone.

    previous_month: the calendar month before the reference date's month ("last month").
    previous_quarter: the calendar quarter before ("last quarter").
    previous_months: `months` complete calendar months ending with the previous month.
    month / quarter: explicit "YYYY-MM" / "YYYY-Qn".
    rolling_days: `days` calendar days ending on the reference date ("last 90 days").
    range: explicit inclusive start/end.
    """

    type: Literal[
        "previous_month",
        "previous_quarter",
        "previous_months",
        "month",
        "quarter",
        "rolling_days",
        "range",
    ]
    months: Optional[int] = Field(default=None, ge=1, le=120)
    days: Optional[int] = Field(default=None, ge=1, le=3660)
    month: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    quarter: Optional[str] = Field(default=None, pattern=r"^\d{4}-[Qq][1-4]$")
    start: Optional[date] = None
    end: Optional[date] = None


class MarketMetricsRequest(BaseModel):
    """Sales or rental metrics over served canonical records (DLD + reconciled DXBI)."""

    dataset: Literal["sales", "rentals"]
    metrics: list[MarketMetricName] = Field(
        default_factory=lambda: ["count", "mean_aed", "median_aed"], min_length=1, max_length=14
    )
    period: MarketPeriodRequest
    location: Optional[str] = Field(
        default=None, max_length=120, description="Community or DLD area, e.g. JVC"
    )
    building: Optional[str] = Field(default=None, max_length=160)
    project: Optional[str] = Field(default=None, max_length=160)
    property_type: Optional[MarketPropertyType] = None
    usage: Optional[Literal["residential", "commercial"]] = None
    bedrooms: Optional[list[MarketBedrooms]] = Field(default=None, max_length=8)
    registration: Optional[Literal["off_plan", "ready"]] = Field(
        default=None, description="Sales only"
    )
    contract_version: Optional[Literal["new", "renewed"]] = Field(
        default=None, description="Rentals only"
    )
    min_term_days: Optional[int] = Field(default=None, ge=1, description="Rentals only")
    max_term_days: Optional[int] = Field(default=None, ge=1, description="Rentals only")
    min_value_aed: Optional[float] = Field(
        default=None, ge=0, description="Sale price or annual rent, inclusive"
    )
    max_value_aed: Optional[float] = Field(
        default=None, gt=0, description="Sale price or annual rent, exclusive"
    )
    group_by: Optional[MarketMetricGroup] = None
    price_bands: Optional[list[float]] = Field(
        default=None,
        min_length=1,
        max_length=12,
        description="Ascending AED boundaries for group_by=price_band: [b0,b1) ... [bn,inf)",
    )
    sort_by: MarketMetricName = "count"
    limit: int = Field(default=20, ge=1, le=200)
    min_group_count: int = Field(default=1, ge=1, le=10000)
    compare_with_previous_period: bool = False
    reference_date: Optional[date] = Field(
        default=None, description="Defaults to today in Asia/Dubai"
    )

    @model_validator(mode="after")
    def _consistent(self) -> "MarketMetricsRequest":
        rentals_only = {"total_contract_aed", "median_contract_aed"}
        if self.dataset == "sales":
            if self.contract_version or self.min_term_days or self.max_term_days:
                raise ValueError("contract_version and term filters apply to rentals only")
            if rentals_only & set(self.metrics) or self.group_by == "contract_version":
                raise ValueError("contract metrics and contract_version grouping are rentals only")
        else:
            if self.registration or self.group_by == "registration":
                raise ValueError("registration applies to sales only")
            if self.property_type == "land":
                raise ValueError("land is not a rental property type")
        if self.group_by == "price_band":
            bands = self.price_bands or []
            if not bands or bands != sorted(set(bands)):
                raise ValueError("price_band grouping needs strictly ascending price_bands")
        elif self.price_bands:
            raise ValueError("price_bands require group_by=price_band")
        if self.sort_by not in {"count", *self.metrics}:
            raise ValueError("sort_by must be count or one of the requested metrics")
        return self


class MarketMetricDefinition(BaseModel):
    name: str
    unit: str
    definition: str


class MarketMetricRow(BaseModel):
    group: Optional[str] = None
    count: Optional[int] = None
    share_of_count: Optional[float] = None
    values: dict[str, Optional[float]] = Field(default_factory=dict)
    # Month rows: evidence for the month's days (see MarketCoverage).
    coverage: Optional[Literal["verified", "partial", "unverified"]] = None


class MarketResolvedPeriod(BaseModel):
    start: date
    end: date
    label: str
    type: str
    reference_date: date
    timezone: str


class MarketCoverageRange(BaseModel):
    start: date
    end: date
    status: Literal["verified", "partial", "unverified"]


class MarketCoverage(BaseModel):
    """Load evidence for the period's days, from the served release's coverage record.

    verified: the source window holding the day was read to the end of its result set (or
    the archive it came from is attested), so a day without records is a true zero.
    partial: the load covering the day stopped inside its window. unverified: no load
    evidence; records may exist but absence proves nothing. complete = every day verified;
    unavailable = no evidence and no records.
    """

    status: Literal["complete", "partial", "unverified", "unavailable"]
    data_from: Optional[date] = None
    data_to: Optional[date] = None
    covered_from: Optional[date] = None
    covered_to: Optional[date] = None
    verified_days: int = 0
    partial_days: int = 0
    unverified_days: int = 0
    ranges: list[MarketCoverageRange] = Field(default_factory=list)
    evidence_loaded_at: Optional[str] = None
    note: str


class MarketLocationMatch(BaseModel):
    """Raw source names of matched rows and the canonical place the crosswalk gave them."""

    area: str
    master_project: str
    district: str = ""
    community: str = ""
    method: str = ""
    records: int


class MarketMetricsResponse(BaseModel):
    dataset: Literal["sales", "rentals"]
    period: MarketResolvedPeriod
    coverage: MarketCoverage
    filters: dict[str, object]
    eligibility: str
    definitions: list[MarketMetricDefinition]
    total: MarketMetricRow
    rows: list[MarketMetricRow] = Field(default_factory=list)
    comparison_period: Optional[MarketResolvedPeriod] = None
    comparison_coverage: Optional[MarketCoverage] = None
    comparison: Optional[MarketMetricRow] = None
    change_pct: dict[str, Optional[float]] = Field(default_factory=dict)
    matched_locations: list[MarketLocationMatch] = Field(default_factory=list)
    sources: dict[str, int] = Field(default_factory=dict)
    release_id: Optional[str] = None
    release_built_at: Optional[str] = None
    limitations: list[str] = Field(default_factory=list)

"""Response schemas for /api/dev/* endpoints.

Per CR-041 conventions: every dev endpoint returns a typed Pydantic model.
The frontend consumes the OpenAPI schema generated from these so types
never drift between backend and UI.

Naming pattern: <Resource><Verb>Response
    e.g. DbOverviewResponse, DbTablesListResponse, SqlQueryResponse

For lists we use a common Page<T> envelope; for failures we use
ErrorResponse and rely on FastAPI's HTTPException.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, ConfigDict


T = TypeVar("T")


# ---------------------------------------------------------------------------
# Common envelopes
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """Standard error envelope returned for 4xx/5xx errors."""
    detail: str = Field(..., description="Human-readable error message")
    error_type: str | None = Field(None, description="Stable error code for clients to switch on")


class Page(BaseModel, Generic[T]):
    """Paginated list envelope used by all list endpoints."""
    items: list[T]
    total: int = Field(..., description="Total matching rows in the database (not just this page)")
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# /db/overview
# ---------------------------------------------------------------------------

class DbOverviewResponse(BaseModel):
    """Counts-at-a-glance for the Database Overview tile."""
    alembic_head: str | None
    base_tables: int = Field(..., description="Regular tables (relkind='r') in public schema")
    partitioned_parents: int = Field(..., description="Partitioned parent tables (relkind='p')")
    partition_children: int = Field(..., description="Partition child tables")
    materialized_views: int
    enum_types: int
    pl_pgsql_functions: int = Field(..., description="User-defined functions in public schema")
    foreign_keys: int
    indexes: int = Field(..., description="Total indexes incl. per-partition copies")
    unique_constraints: int
    database_size_bytes: int
    database_size_pretty: str


# ---------------------------------------------------------------------------
# /db/tables
# ---------------------------------------------------------------------------

class TableInfo(BaseModel):
    """One row in the tables list."""
    schema_name: str = Field(..., alias="schema")
    name: str
    kind: str = Field(..., description="regular | partitioned_parent | partition_child")
    parent: str | None = Field(None, description="For partition children, the parent table name")
    row_count_estimate: int = Field(..., description="From pg_stat_user_tables.n_live_tup; estimate, not exact")
    total_size_bytes: int = Field(..., description="pg_total_relation_size incl. indexes + TOAST")
    total_size_pretty: str
    index_count: int

    model_config = ConfigDict(populate_by_name=True)


class DbTablesListResponse(Page[TableInfo]):
    pass


class IndexInfo(BaseModel):
    name: str
    definition: str = Field(..., description="Full CREATE INDEX statement")
    is_unique: bool
    is_partial: bool
    size_bytes: int
    size_pretty: str


class ForeignKeyInfo(BaseModel):
    name: str
    column: str
    referenced_table: str
    referenced_column: str
    on_delete: str = Field(..., description="NO ACTION | CASCADE | SET NULL | RESTRICT | SET DEFAULT")
    on_update: str


class ColumnInfo(BaseModel):
    name: str
    data_type: str = Field(..., description="format_type output, e.g. 'character varying(50)'")
    is_nullable: bool
    has_default: bool
    column_default: str | None


class TableDetailResponse(BaseModel):
    """GET /api/dev/db/tables/{name} — single-table drill-in."""
    schema_name: str = Field(..., alias="schema")
    name: str
    kind: str
    row_count_estimate: int
    total_size_bytes: int
    total_size_pretty: str
    columns: list[ColumnInfo]
    indexes: list[IndexInfo]
    foreign_keys_out: list[ForeignKeyInfo] = Field(..., description="FKs this table has TO other tables")
    foreign_keys_in: list[ForeignKeyInfo] = Field(..., description="FKs from other tables pointing AT this one")
    partition_children: list[str] = Field(..., description="When kind=partitioned_parent")
    partition_bounds: str | None = Field(None, description="When kind=partition_child")

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# /db/materialized-views
# ---------------------------------------------------------------------------

class MaterializedViewInfo(BaseModel):
    name: str
    is_populated: bool = Field(..., description="True if data was loaded (vs. WITH NO DATA)")
    row_count_estimate: int
    total_size_bytes: int
    total_size_pretty: str
    has_unique_index: bool = Field(..., description="Required for REFRESH CONCURRENTLY")


class DbMaterializedViewsResponse(BaseModel):
    items: list[MaterializedViewInfo]
    total: int


class MvRefreshResponse(BaseModel):
    """POST /api/dev/db/refresh-mv/{name}?confirm=true"""
    name: str
    concurrent: bool
    duration_ms: int
    row_count_estimate_after: int


# ---------------------------------------------------------------------------
# /db/functions
# ---------------------------------------------------------------------------

class FunctionInfo(BaseModel):
    name: str
    language: str = Field(..., description="plpgsql | sql | c | internal")
    return_type: str
    argument_signature: str
    volatility: str = Field(..., description="immutable | stable | volatile")


class DbFunctionsResponse(BaseModel):
    items: list[FunctionInfo]
    total: int


# ---------------------------------------------------------------------------
# /db/indexes
# ---------------------------------------------------------------------------

class GlobalIndexInfo(BaseModel):
    name: str
    table_name: str
    definition: str
    is_unique: bool
    is_partial: bool
    size_bytes: int


class DbIndexesResponse(Page[GlobalIndexInfo]):
    pass


# ---------------------------------------------------------------------------
# /db/migrations
# ---------------------------------------------------------------------------

class MigrationRevision(BaseModel):
    revision: str
    down_revision: str | None
    is_current: bool
    title: str = Field(..., description="First line of the migration's docstring")


class DbMigrationsResponse(BaseModel):
    current_head: str | None
    total_revisions: int
    items: list[MigrationRevision]


# ---------------------------------------------------------------------------
# /db/query (SQL console)
# ---------------------------------------------------------------------------

class SqlQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=10_000,
                      description="A single SELECT statement. No DML/DDL/multiple statements.")


class SqlQueryResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]] = Field(..., description="Each row is a positional list aligned to `columns`")
    row_count: int
    truncated: bool = Field(..., description="True if results were capped at the row limit")
    row_limit: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Extra: aggregate health for the dashboard
# ---------------------------------------------------------------------------

class DbHealthResponse(BaseModel):
    """Wraps a database connectivity probe with a freshness timestamp."""
    status: str = Field(..., description="ok | degraded")
    database: str = Field(..., description="up | down")


# ---------------------------------------------------------------------------
# Parsing telemetry (Page 4) — all metrics computed in the backend
# ---------------------------------------------------------------------------

class DropPoint(BaseModel):
    bucket: str = Field(..., description="ISO date for daily buckets, or ISO datetime for finer grain")
    service_variant: str | None
    dropped_count: int


class DropRateResponse(BaseModel):
    """Time-series of claim-drop counts grouped by day × variant."""
    days: int = Field(..., description="Window size used to build this series")
    group_by: str = Field(..., description="day | hour")
    items: list[DropPoint]


class DropReasonPoint(BaseModel):
    """One (segment, field) row counting ERROR-severity validation entries."""
    segment: str
    field: str
    count: int


class DropReasonsResponse(BaseModel):
    days: int
    total_errors: int
    items: list[DropReasonPoint]


class UnhandledSegmentPoint(BaseModel):
    segment_name: str
    count: int
    last_seen_at: str | None = Field(None, description="ISO timestamp of most recent occurrence")
    last_seen_in_file_id: int | None
    last_seen_text: str | None = Field(None, description="Sample raw segment text from one occurrence")


class UnhandledSegmentsResponse(BaseModel):
    days: int
    items: list[UnhandledSegmentPoint]


class CasStridePoint(BaseModel):
    stride: int = Field(..., description="2 = compact non-spec form; 3 = spec form")
    count: int


class CasStrideResponse(BaseModel):
    days: int
    total_warnings: int = Field(..., description="Total CAS validator_warning events in window")
    items: list[CasStridePoint]


class EncodingDistributionPoint(BaseModel):
    encoding: str
    count: int


class EncodingDistributionResponse(BaseModel):
    days: int
    items: list[EncodingDistributionPoint]
    note: str | None = Field(
        None,
        description="Diagnostic note when underlying telemetry is not yet collected",
    )


class ValidatorTierPoint(BaseModel):
    service_variant: str | None
    validator: str = Field(..., description="tier1_structural | tier2_ig | tier3_payer | tier4_business | parser")
    severity: str = Field(..., description="ERROR | WARNING | INFO")
    count: int


class ValidatorTiersResponse(BaseModel):
    days: int
    items: list[ValidatorTierPoint]


class ReparsePoint(BaseModel):
    edi_file_id: int
    file_name: str
    parser_version: str
    parse_status: str
    parse_completed_at: str | None
    claims_saved: int | None
    claims_dropped: int | None


class ReparseLogResponse(BaseModel):
    days: int
    items: list[ReparsePoint]


# ---------------------------------------------------------------------------
# Home dashboard tiles (Page 1) — 3 new endpoints, others reuse telemetry
# ---------------------------------------------------------------------------

class RecentUploadPoint(BaseModel):
    id: int
    file_name: str
    file_type: str = Field(..., description="edi_837 | edi_835 | edi_277 | edi_999")
    service_variant_detected: str | None
    claim_subtype_detected: str | None
    parse_status: str = Field(..., description="pending | parsing | parsed | failed | partial")
    claims_saved: int | None = Field(None, description="From parse_summary.claims_saved")
    claims_dropped: int | None = Field(None, description="From parse_summary.claims_dropped")
    uploaded_at: str = Field(..., description="ISO timestamp from edi_files.created_at")


class RecentUploadsResponse(BaseModel):
    items: list[RecentUploadPoint]
    total: int = Field(..., description="Count returned, NOT total uploads in DB")


class ModelRegistryEntry(BaseModel):
    """One row per (variant, subtype) in the FE registry. `has_trained_model`
    is False until model_training_metrics has a successful row for the pair."""
    service_variant: str
    claim_subtype: str
    feature_count: int = Field(..., description="From FE registry (always present)")
    is_registered: bool = Field(..., description="True iff (variant, subtype) in FE _VARIANT_COLUMNS")
    has_trained_model: bool
    model_version: str | None
    trained_at: str | None
    training_size: int | None
    metrics_pr_auc: float | None
    metrics_f1: float | None
    decision_threshold: float | None


class ModelRegistryResponse(BaseModel):
    items: list[ModelRegistryEntry]
    total: int
    trained_count: int = Field(..., description="How many (variant, subtype) have at least one trained model")


class JobStatusCount(BaseModel):
    status: str = Field(..., description="queued | running | succeeded | failed | cancelled")
    count: int


class JobsSummaryResponse(BaseModel):
    by_status: list[JobStatusCount] = Field(..., description="All-time counts grouped by status")
    queued: int = Field(..., description="Currently queued jobs")
    running: int = Field(..., description="Currently running jobs")
    succeeded_last_hour: int
    failed_last_hour: int


# ---------------------------------------------------------------------------
# EDI Inspector (Page 2) — upload + files list + drill-in
# ---------------------------------------------------------------------------

class EdiUploadResponse(BaseModel):
    """POST /api/dev/edi/upload?confirm=true — result of the parse+save cycle.

    Either `edi_file_id` is set (success or partial) OR `error` is set
    (envelope/decode failure). Duplicate uploads (same content_hash) return
    409 with the existing file_id in `duplicate_of_file_id`.
    """
    edi_file_id: int | None
    file_name: str
    file_type: str | None
    service_variant_detected: str | None
    claim_subtype_detected: str | None
    parse_status: str | None
    claims_saved: int | None
    claims_dropped: int | None
    parser_version: str | None
    parse_duration_ms: int | None
    duplicate_of_file_id: int | None = Field(None, description="Set when 409 due to content_hash collision")
    error: str | None = Field(None, description="Envelope/decode failure message")


class EdiFileListItem(BaseModel):
    id: int
    file_name: str
    file_type: str
    service_variant_detected: str | None
    claim_subtype_detected: str | None
    parse_status: str
    parser_version: str
    claims_saved: int | None
    claims_dropped: int | None
    raw_size_bytes: int = Field(..., description="length(raw_text), bytes")
    uploaded_at: str
    parse_completed_at: str | None


class EdiFilesListResponse(Page[EdiFileListItem]):
    pass


class EdiFileDetailResponse(BaseModel):
    """GET /api/dev/edi/files/{id} — full single-file metadata + counters."""
    id: int
    file_name: str
    file_type: str
    implementation_guide: str | None
    sender_id: str | None
    receiver_id: str | None
    interchange_control_no: str | None
    functional_group_control_no: str | None
    service_variant_detected: str | None
    claim_subtype_detected: str | None
    parse_status: str
    parser_version: str
    content_hash: str
    raw_size_bytes: int
    parse_started_at: str | None
    parse_completed_at: str | None
    parse_summary: dict[str, Any] | None
    uploaded_at: str
    # Derived live counts (not from parse_summary which may be stale):
    raw_segments_count: int
    parse_events_count: int
    claims_persisted_count: int
    remittance_claims_persisted_count: int


class RawSegmentItem(BaseModel):
    id: int
    segment_position: int
    segment_name: str
    handler_status: str
    raw_segment_text: str
    parse_error: str | None
    claim_id: int | None
    remittance_claim_id: int | None
    created_at: str


class RawSegmentsListResponse(Page[RawSegmentItem]):
    pass


class ParseEventItem(BaseModel):
    id: int
    event_type: str
    segment_name: str | None
    segment_position: int | None
    claim_number: str | None
    details: dict[str, Any] | None
    created_at: str


class ParseEventsListResponse(Page[ParseEventItem]):
    pass


# ---------------------------------------------------------------------------
# Claims Browser (Page 3) — list + 9-tab detail
# ---------------------------------------------------------------------------

class ClaimsListItem(BaseModel):
    id: int
    claim_number: str
    service_variant: str
    claim_subtype: str
    claim_status: str
    total_charge_amount: float
    service_from_date: str | None
    submission_date: str
    payer_id: int | None
    payer_name: str | None
    patient_id: int | None
    edi_file_id: int
    line_count: int
    diagnosis_count: int
    has_remittance: bool
    created_at: str


class ClaimsListResponse(Page[ClaimsListItem]):
    pass


class ClaimLineItem(BaseModel):
    id: int
    line_number: int
    procedure_code: str | None
    modifier1: str | None
    modifier2: str | None
    modifier3: str | None
    modifier4: str | None
    billed_amount: float | None
    units: float | None
    units_basis: str | None
    place_of_service: str | None
    service_date: str | None
    diagnosis_pointers: list[int] | None
    revenue_code: str | None
    hipps_code: str | None
    tooth_number: str | None
    tooth_surfaces: str | None
    ndc_drug_code: str | None
    raw_sv_segment: str | None


class ClaimLinesListResponse(BaseModel):
    items: list[ClaimLineItem]
    total: int


class DiagnosisItem(BaseModel):
    id: int
    sequence_number: int
    diagnosis_code: str
    diagnosis_type: str
    diagnosis_qualifier: str | None
    present_on_admission: str | None


class DiagnosesListResponse(BaseModel):
    items: list[DiagnosisItem]
    total: int


class RemittanceClaimItem(BaseModel):
    id: int
    claim_status_code: str
    billed_amount: float
    paid_amount: float
    patient_responsibility_amount: float | None
    payer_claim_control_number: str | None
    remittance_date: str | None
    payer_paid_date: str | None
    edi_file_id: int | None
    adjustment_count: int
    raw_clp_segment: str | None


class RemittanceClaimsListResponse(BaseModel):
    items: list[RemittanceClaimItem]
    total: int


class ClaimDetailResponse(BaseModel):
    """GET /api/dev/claims/{id} — the 'overview' tab payload.

    Each individual relation (lines / diagnoses / remits / parse events /
    raw segments) is fetched lazily by its own endpoint to avoid building
    a megapayload when a tab isn't opened.
    """
    id: int
    edi_file_id: int
    claim_number: str
    service_variant: str
    claim_subtype: str
    claim_status: str
    total_charge_amount: float
    facility_type_code: str | None
    frequency_code: str | None
    service_from_date: str | None
    service_to_date: str | None
    submission_date: str

    # Foreign-key snapshots (id + denormalized name where it helps)
    payer_id: int | None
    payer_name: str | None
    patient_id: int | None
    patient_member_id: str | None
    subscriber_id: int | None
    billing_provider_id: int | None
    billing_provider_npi: str | None
    rendering_provider_id: int | None
    rendering_provider_npi: str | None
    referring_provider_id: int | None

    authorization_number: str | None
    referral_number: str | None
    previous_payer_claim_control_no: str | None

    variant_data: dict[str, Any] | None
    raw_claim_segment: str | None

    # Counts so the UI knows which tabs have data without separate calls
    line_count: int
    diagnosis_count: int
    remittance_count: int
    raw_segment_count: int
    parse_event_count: int

    created_at: str

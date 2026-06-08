"""ParseContext — mutable state threaded through every handler invocation.

Handlers are pure functions that mutate ctx. Persistence is decoupled
(persistence.save_parse_context consumes the accumulators).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from rcm.parsing.envelope import Delimiters


# ---------------------------------------------------------------------------
# Validation + event shapes (kept simple — no PHI, all log-safe)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ValidationError:
    """Issued by validators OR by handlers when a structural problem is
    detected. ERROR-severity entries drop the affected claim at save time."""

    segment: str                       # e.g. "CLM"
    field: str                         # e.g. "service_from_date"
    message: str                       # human-readable, safe to surface
    severity: str = "ERROR"            # ERROR / WARNING / INFO
    position: int = 0                  # segment_position
    object_index: int = 0              # which claim/remit (0-based within file)
    claim_identifier: str | None = None
    validator: str = ""                # tier1_structural / tier2_ig / tier3_payer / tier4_business / parser


@dataclass(slots=True)
class ParseEvent:
    """Persisted to parse_events. One row per event. Telemetry surface."""

    event_type: str
    segment_name: str | None = None
    segment_position: int | None = None
    claim_number: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Accumulator records — light DTOs so handlers don't touch SQLAlchemy mapped
# classes during parse. persistence.py converts these to model instances.
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PatientRec:
    member_id: str
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None


@dataclass(slots=True)
class ProviderRec:
    npi: str
    provider_type: str | None = None   # billing / rendering / referring / ...
    organization_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    taxonomy_code: str | None = None
    state: str | None = None


@dataclass(slots=True)
class SubscriberRec:
    member_id: str
    patient_member_id: str | None = None  # resolved at save time
    payer_canonical_name: str | None = None
    relationship_code: str | None = None
    group_number: str | None = None
    policy_number: str | None = None
    coordination_of_benefits: str | None = None  # P/S/T


@dataclass(slots=True)
class PayerRec:
    canonical_name: str
    payer_taxonomy: str | None = None
    sender_id: str | None = None
    receiver_id: str | None = None


@dataclass(slots=True)
class ClaimLineRec:
    line_number: int
    procedure_code: str | None = None
    procedure_code_qualifier: str | None = None
    modifier1: str | None = None
    modifier2: str | None = None
    modifier3: str | None = None
    modifier4: str | None = None
    billed_amount: Any = None
    units: Any = None
    units_basis: str | None = None
    place_of_service: str | None = None
    service_date: date | None = None
    diagnosis_pointers: list[int] = field(default_factory=list)
    revenue_code: str | None = None
    hipps_code: str | None = None
    tooth_number: str | None = None
    tooth_surfaces: str | None = None
    ndc_drug_code: str | None = None
    line_data: dict[str, Any] = field(default_factory=dict)
    raw_sv_segment: str | None = None


@dataclass(slots=True)
class DiagnosisRec:
    sequence_number: int
    diagnosis_code: str
    diagnosis_type: str           # ABK / ABF / BJ / BK / BF / APR
    diagnosis_qualifier: str | None = None
    present_on_admission: str | None = None


@dataclass(slots=True)
class ClaimCertRec:
    certification_type: str       # ambulance / homebound / dme / ...
    raw_segment: str | None = None
    structured_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ClaimAmountRec:
    amount_qualifier: str         # F5 / A8 / AAE / ...
    amount: Any


@dataclass(slots=True)
class ClaimAttachmentRec:
    report_type_code: str
    transmission_code: str
    attachment_control_no: str | None = None


@dataclass(slots=True)
class HomeCareEpisodeRec:
    episode_start_date: date
    episode_end_date: date | None = None
    hipps_code: str | None = None
    oasis_assessment_date: date | None = None
    visit_count: int | None = None
    discipline_mix: dict[str, int] = field(default_factory=dict)
    is_lupa: bool | None = None
    homebound_certified: bool | None = None
    plan_of_care_signed_date: date | None = None


@dataclass(slots=True)
class TransportCertRec:
    transport_miles: Any = None
    patient_weight_lbs: int | None = None
    transport_reason_code: str | None = None
    round_trip: bool | None = None
    emergent: bool | None = None
    origin_address: dict[str, Any] | None = None
    destination_address: dict[str, Any] | None = None
    level_of_service: str | None = None


@dataclass(slots=True)
class ClaimRec:
    """In-flight claim record. `payer_canonical_name`, patient/provider
    references etc. are resolved against the master tables at save time.
    """

    claim_number: str
    service_variant: str
    claim_subtype: str
    total_charge_amount: Any
    claim_status: str = "submitted"
    facility_type_code: str | None = None
    frequency_code: str | None = None
    service_from_date: date | None = None
    service_to_date: date | None = None
    submission_date: date | None = None
    authorization_number: str | None = None
    referral_number: str | None = None
    previous_payer_claim_control_no: str | None = None
    payer_canonical_name: str | None = None
    patient_member_id: str | None = None
    subscriber_member_id: str | None = None
    billing_provider_npi: str | None = None
    rendering_provider_npi: str | None = None
    referring_provider_npi: str | None = None
    variant_data: dict[str, Any] = field(default_factory=dict)
    raw_claim_segment: str | None = None

    lines: list[ClaimLineRec] = field(default_factory=list)
    diagnoses: list[DiagnosisRec] = field(default_factory=list)
    certifications: list[ClaimCertRec] = field(default_factory=list)
    amounts: list[ClaimAmountRec] = field(default_factory=list)
    attachments: list[ClaimAttachmentRec] = field(default_factory=list)
    home_care_episode: HomeCareEpisodeRec | None = None
    transport_cert: TransportCertRec | None = None

    # Stable index of the claim within the file (0-based, set when CLM seen)
    object_index: int = 0
    # Set by validators when ERROR-severity issue found; persistence skips these
    dropped: bool = False
    drop_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AdjustmentRec:
    adjustment_group_code: str
    adjustment_reason_code: str
    adjustment_amount: Any
    quantity: Any = None
    service_line_number: int | None = None    # set when CAS follows an SVC
    raw_cas_segment: str | None = None


@dataclass(slots=True)
class RemarkCodeRec:
    remark_code: str
    service_line_number: int | None = None
    raw_lq_segment: str | None = None


@dataclass(slots=True)
class RemittanceClaimRec:
    claim_number: str                          # links back to claims.claim_number
    claim_status_code: str                     # CLP02
    billed_amount: Any
    paid_amount: Any
    patient_responsibility_amount: Any = None
    payer_claim_control_number: str | None = None
    remittance_date: date | None = None        # NULLABLE — Lesson P1
    payer_paid_date: date | None = None
    raw_clp_segment: str | None = None

    adjustments: list[AdjustmentRec] = field(default_factory=list)
    remark_codes: list[RemarkCodeRec] = field(default_factory=list)

    object_index: int = 0
    dropped: bool = False
    drop_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RawSegmentRec:
    """Audit row — persisted for EVERY segment we see, even dropped ones."""

    segment_name: str
    segment_position: int
    raw_segment_text: str
    handler_status: str           # handled / skipped_unhandled / parse_error / validator_dropped
    parse_error: str | None = None
    claim_object_index: int | None = None         # binds row to a claim at save time
    remittance_object_index: int | None = None    # binds to a remit


# ---------------------------------------------------------------------------
# ParseContext
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ParseContext:
    """Threaded through every handler call. Handlers mutate, don't return.

    `_current_*` slots hold transient state between segments (e.g. NM1*PR
    populates `current_payer_name`; the next CLM consumes it).
    """

    # ---- inputs ----------------------------------------------------------
    file_name: str
    raw_text: str
    delimiters: Delimiters
    parser_version: str
    file_type: str = "edi_837"        # edi_837 / edi_835 / edi_277 / edi_999

    # ---- envelope --------------------------------------------------------
    implementation_guide: str | None = None     # GS08
    isa_sender_id: str | None = None
    isa_receiver_id: str | None = None
    isa_control_no: str | None = None           # ISA13
    gs_control_no: str | None = None            # GS06
    service_variant: str | None = None          # 837P / 837I / 837D (None for 835)
    claim_subtype: str | None = None            # set by routing AFTER segments parsed

    # ---- transient state set by NM1 / N1 / REF / DTP / SBR / HL ----------
    current_payer_name: str | None = None
    current_payer_taxonomy: str | None = None
    current_billing_provider_npi: str | None = None
    current_rendering_provider_npi: str | None = None
    current_referring_provider_npi: str | None = None
    current_billing_provider_taxonomy: str | None = None
    current_patient_member_id: str | None = None
    current_patient_first_name: str | None = None
    current_patient_last_name: str | None = None
    current_patient_dob: date | None = None
    current_patient_gender: str | None = None
    current_subscriber_member_id: str | None = None
    current_subscriber_relationship: str | None = None
    current_subscriber_group_number: str | None = None
    current_subscriber_cob_position: str | None = None  # P / S / T
    current_service_from_date: date | None = None
    current_service_to_date: date | None = None
    current_authorization_no: str | None = None
    current_referral_no: str | None = None
    current_previous_payer_claim_control_no: str | None = None
    # HL stack (837I) — track who we're inside of
    current_hl_level: str | None = None         # 20 / 22 / 23

    # Most recent NM1*?? entity code seen (e.g. "85" billing, "82" rendering,
    # "QC" patient, "IL" subscriber, "PR" payer). Consumed by N3 / N4 / PRV
    # so they bind their data to the right entity — see contact.handle_n3 etc.
    # Resets to None on CLM (claim boundary).
    current_nm1_entity: str | None = None
    # N3 buffer — handle_n3 writes here; handle_n4 consumes + clears.
    _addr_street1: str | None = None
    _addr_street2: str | None = None
    # Addresses captured at the file/HL level BEFORE the first CLM
    # (billing-provider loop 2000A). CLM transfers them into the new claim.
    _pending_addresses: dict[str, Any] = field(default_factory=dict)
    # PRV taxonomy codes seen BEFORE the matching NM1 created a ProviderRec.
    # Keyed by provider_type (billing/rendering/referring). NM1 applies + pops.
    _pending_prv: dict[str, str] = field(default_factory=dict)

    # ---- current-object pointers ----------------------------------------
    current_claim: ClaimRec | None = None
    current_remittance: RemittanceClaimRec | None = None
    current_line_number: int = 0
    current_remit_line_number: int | None = None  # set after SVC in 835

    # ---- accumulators (consumed by persistence) -------------------------
    payers: list[PayerRec] = field(default_factory=list)
    patients: list[PatientRec] = field(default_factory=list)
    providers: list[ProviderRec] = field(default_factory=list)
    subscribers: list[SubscriberRec] = field(default_factory=list)
    claims: list[ClaimRec] = field(default_factory=list)
    remittances: list[RemittanceClaimRec] = field(default_factory=list)
    raw_segments: list[RawSegmentRec] = field(default_factory=list)

    # ---- diagnostics ----------------------------------------------------
    segment_position: int = 0
    parse_errors: list[ValidationError] = field(default_factory=list)
    parse_events: list[ParseEvent] = field(default_factory=list)
    unhandled_segment_counts: dict[str, int] = field(default_factory=dict)

    # ---- 835 transient state (DTM*405 production date fallback) ---------
    # Declared as a real field because ParseContext is slot=True;
    # setattr(ctx, "_dtm_production_date", ...) would otherwise AttributeError.
    _dtm_production_date: date | None = None

    # ---- counters -------------------------------------------------------
    isa_count: int = 0
    gs_count: int = 0
    st_count: int = 0
    se_count: int = 0
    ge_count: int = 0
    iea_count: int = 0
    # SE01 / IEA01 / GE01 — values read from trailers for reconciliation
    se01_values: list[int] = field(default_factory=list)
    ge01_values: list[int] = field(default_factory=list)
    iea01_values: list[int] = field(default_factory=list)

    # ---- metadata -------------------------------------------------------
    parse_started_at: datetime | None = None
    parse_completed_at: datetime | None = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------
    def add_error(
        self,
        *,
        segment: str,
        field: str,
        message: str,
        severity: str = "ERROR",
        claim_identifier: str | None = None,
        validator: str = "parser",
    ) -> None:
        self.parse_errors.append(ValidationError(
            segment=segment,
            field=field,
            message=message,
            severity=severity,
            position=self.segment_position,
            object_index=(self.current_claim.object_index if self.current_claim else 0),
            claim_identifier=claim_identifier
                or (self.current_claim.claim_number if self.current_claim else None),
            validator=validator,
        ))

    def add_event(
        self,
        event_type: str,
        *,
        segment_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.parse_events.append(ParseEvent(
            event_type=event_type,
            segment_name=segment_name,
            segment_position=self.segment_position,
            claim_number=(self.current_claim.claim_number if self.current_claim else None),
            details=details or {},
        ))

    def record_unhandled(self, segment_name: str) -> None:
        self.unhandled_segment_counts[segment_name] = (
            self.unhandled_segment_counts.get(segment_name, 0) + 1
        )

    # Summary helpers used by the response builder
    def dropped_claim_count(self) -> int:
        return sum(1 for c in self.claims if c.dropped)

    def saved_claim_count(self) -> int:
        return sum(1 for c in self.claims if not c.dropped)

    def dropped_reasons_by_field(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.parse_errors:
            if e.severity != "ERROR":
                continue
            key = f"{e.segment}.{e.field}"
            out[key] = out.get(key, 0) + 1
        return out

"""Magic numbers / thresholds / domain constants for FE.

Centralized so a hyperparameter sweep can change them in one place.
"""

from __future__ import annotations

# Version stamp embedded in every model artifact + every prediction_log row.
# Bump when the feature SCHEMA changes (added/removed/renamed feature columns).
# Internal formula tweaks that don't change the column set do not need a bump.
FEATURE_ENGINEERING_VERSION = "v1.0.0"

# ---- Rarity thresholds (Category L) -------------------------------------
RARE_PAYER_THRESHOLD = 50
RARE_CPT_THRESHOLD = 10
RARE_DX_THRESHOLD = 10
RARE_PROVIDER_THRESHOLD = 25

# ---- Risk-level thresholds (used by predictor) --------------------------
LOW_PROB_CUTOFF = 0.05        # absolute, applies across all variants
PRECISION_FLOOR = 0.85        # target precision when selecting per-variant threshold

# ---- Patient/provider history window sizes (Category G) -----------------
PATIENT_HISTORY_DAYS_SHORT = 30
PATIENT_HISTORY_DAYS_MID = 90
PATIENT_HISTORY_DAYS_LONG = 365

# ---- Timely-filing defaults (Category E) --------------------------------
DEFAULT_TIMELY_FILING_DAYS = 365   # used when payer-specific not loaded
TIMELY_NEAR_THRESHOLD_RATIO = 0.85 # is_near_timely_filing fires at >= this

# ---- Therapy (Category M / therapy variant) -----------------------------
MEDICARE_THERAPY_CAP_2024 = 2330.0

# ---- E&M code ranges (Category M / healthcare variant) ------------------
EM_CODE_RANGES = {
    "office_new": (99201, 99205),
    "office_established": (99211, 99215),
    "consultation": (99241, 99245),
    "consultation_inpatient": (99251, 99255),
    "preventive_new": (99381, 99387),
    "preventive_established": (99391, 99397),
}
EM_HIGH_COMPLEXITY = frozenset({99204, 99205, 99214, 99215, 99244, 99245})

# ---- Telehealth identifiers ---------------------------------------------
TELEHEALTH_POS_CODES = frozenset({"02", "10"})
TELEHEALTH_MODIFIERS = frozenset({"95", "GT", "G0"})

# ---- Home care revenue code ranges (Category M / home_care variant) -----
HOME_CARE_SKILLED_REVENUE = frozenset({"0551", "0552", "0571", "0572"})
HOME_CARE_REVENUE_RANGE = (551, 589)
INPATIENT_REVENUE_RANGE = (100, 219)
HOSPICE_REVENUE_RANGE = (820, 859)

# ---- Therapy modifiers --------------------------------------------------
THERAPY_DISCIPLINE_MODIFIERS = frozenset({"GP", "GO", "GN"})  # PT / OT / ST
THERAPY_MAINTENANCE_MODIFIER = "KH"
THERAPY_THRESHOLD_MODIFIER = "KX"

# ---- Ambulance HCPCS prefixes (Category M / transport) ------------------
AMBULANCE_HCPCS_PREFIXES = ("A0",)

# ---- Sentinels ----------------------------------------------------------
MISSING_CATEGORICAL_SENTINEL = "__missing__"
UNSEEN_CATEGORICAL_SENTINEL = "__unseen__"

# ---- Encoder hyperparams ------------------------------------------------
TARGET_ENCODER_CV_FOLDS = 5
TARGET_ENCODER_RANDOM_STATE = 42
TARGET_ENCODER_SMOOTHING = "auto"

# ---- Model training -----------------------------------------------------
MIN_TRAINING_SIZE = 1000       # fall back to global model below this
XGBOOST_RANDOM_STATE = 42

# ---- NCCI unbundling override modifiers ---------------------------------
NCCI_OVERRIDE_MODIFIERS = frozenset({"59", "XE", "XS", "XP", "XU"})

# ---- Bundle of CPT code categories used in features ---------------------
CPT_CATEGORY_DEFAULT = "uncategorized"
DX_CHAPTER_DEFAULT = "unspecified"

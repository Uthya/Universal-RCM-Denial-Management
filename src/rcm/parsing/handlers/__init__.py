"""Per-segment handlers.

Importing this package registers every handler with HANDLER_REGISTRY via the
side-effect of calling `register_handler`. The top-level parser calls
`from rcm.parsing.handlers import *` (or just `import rcm.parsing.handlers`)
to ensure all handlers are wired up before dispatch begins.

Organized by responsibility, not by one-file-per-segment:
    contact        — NM1, N1, PER
    hierarchy      — HL, SBR
    claim          — CLM
    service_line   — SV1 (837P), SV2 (837I), SV3 (837D)
    diagnosis      — HI
    date           — DTP, DTM
    reference      — REF
    certification  — CR1, CR3, CRC
    dental         — TOO, DN1, DN2
    attachment     — PWK
    amount         — AMT
    note           — NTE
    remittance     — CLP, CAS, LQ, MIA, MOA, SVC
"""

from rcm.parsing.handlers import (
    amount,
    attachment,
    certification,
    claim,
    contact,
    date,
    dental,
    diagnosis,
    hierarchy,
    note,
    reference,
    remittance,
    service_line,
)

__all__ = [
    "amount",
    "attachment",
    "certification",
    "claim",
    "contact",
    "date",
    "dental",
    "diagnosis",
    "hierarchy",
    "note",
    "reference",
    "remittance",
    "service_line",
]

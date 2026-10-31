"""Decide document outcome from validation warnings.

Review is reserved for contradictions and missing critical facts.
Informational warnings leave the document completed.
"""
from __future__ import annotations

from typing import Iterable, Tuple

CONFIRMED = 'confirmed'
COMPLETED_WITH_WARNINGS = 'completed_with_warnings'
REVIEW_REQUIRED = 'review_required'
FAILED = 'failed'

_FAILED_PREFIXES = (
    'STRUCTURED_OUTPUT_FAILED',
    'GEMINI_API_KEY_NOT_CONFIGURED',
    'GOOGLE_GENAI_NOT_INSTALLED',
)

_CRITICAL_PREFIXES = _FAILED_PREFIXES + (
    'TOTAL_MISMATCH',
    'SUBTOTAL_EXCEEDS_TOTAL_WITHOUT_DISCOUNT',
    'IVA_RATE_AMOUNT_MISMATCH',
    'FINANCIAL_CONTRADICTION',
    'AMBIGUOUS_INVOICE_NUMBER',
    'GEMINI_SCHEMA_VALIDATION_FAILED',
)

_INFO_PREFIXES = (
    'GEMINI_MODEL_USED',
)


def warning_code(warning: str) -> str:
    return warning.split(':', 1)[0].strip()


def is_critical(warning: str) -> bool:
    code = warning_code(warning)
    return code.startswith(_CRITICAL_PREFIXES)


def is_failed(warning: str) -> bool:
    return warning_code(warning).startswith(_FAILED_PREFIXES)


def decide_validation(
    warnings: Iterable[str],
    *,
    missing_total: bool = False,
    missing_supplier: bool = False,
) -> Tuple[str, bool]:
    """Return (validation_level, requires_review)."""
    stored = list(warnings)
    if any(is_failed(item) for item in stored):
        return FAILED, True
    if missing_total or missing_supplier or any(is_critical(item) for item in stored):
        return REVIEW_REQUIRED, True
    meaningful = [item for item in stored if not warning_code(item).startswith(_INFO_PREFIXES)]
    if meaningful:
        return COMPLETED_WITH_WARNINGS, False
    return CONFIRMED, False

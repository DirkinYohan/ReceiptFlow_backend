"""Reconcile printed amounts. Does not replace a printed total to force a match."""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from services.extraction.schemas import ExtractedDocument, LineItem, TaxItem

Warning = Tuple[str, Optional[str], bool]  # code, message, critical


def _rate_percent(rate: Decimal) -> Decimal:
    if rate <= 1:
        return rate * Decimal('100')
    return rate


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.01'))


def neutralize_implausible_line_totals(
    items: Sequence[LineItem],
    subtotal: Optional[Decimal],
    tolerance: Decimal,
) -> Tuple[List[LineItem], List[Warning]]:
    """Drop a line total that is only qty × price when that product exceeds the subtotal.

    The printed price stays. The fabricated extension does not.
    """
    warnings: List[Warning] = []
    updated: List[LineItem] = []
    for index, item in enumerate(items, start=1):
        if (
            subtotal is None
            or item.quantity is None
            or item.unit_price is None
            or item.quantity <= 1
        ):
            updated.append(item)
            continue
        implied = (item.quantity * item.unit_price) - (item.discount or Decimal('0'))
        if implied <= subtotal + tolerance:
            updated.append(item)
            continue
        total = item.total
        if total is not None and abs(total - implied) <= tolerance:
            item = item.model_copy(update={'total': None})
            warnings.append((
                'LINE_TOTAL_AMBIGUOUS',
                (
                    f"item #{index} qty*price={implied} exceeds subtotal={subtotal}; "
                    "the price column was not treated as a unit price and the line total was left null"
                ),
                False,
            ))
        else:
            warnings.append((
                'LINE_PRICE_COLUMN_AMBIGUOUS',
                (
                    f"item #{index} qty*price={implied} exceeds subtotal={subtotal}; "
                    "printed values were kept"
                ),
                False,
            ))
        updated.append(item)
    return updated, warnings


def _line_bases_for_rate(items: Sequence[LineItem], rate: Decimal, tolerance: Decimal) -> List[Decimal]:
    bases: List[Decimal] = []
    for item in items:
        if item.tax_rate is None or item.total is None:
            continue
        if abs(_rate_percent(item.tax_rate) - rate) <= Decimal('0.1'):
            bases.append(item.total)
    return bases


def identify_taxable_base(
    tax: TaxItem,
    items: Sequence[LineItem],
    subtotal: Optional[Decimal],
    discounts: Decimal,
    tolerance: Decimal,
) -> Optional[Decimal]:
    """Find the printed base for one tax. Never defaults to the full subtotal."""
    if tax.taxable_base is not None:
        return tax.taxable_base
    if tax.rate is None or tax.amount is None:
        return None
    rate = _rate_percent(tax.rate)
    if rate == 0:
        return tax.taxable_base
    implied = _quantize(tax.amount * Decimal('100') / rate)
    rated = _line_bases_for_rate(items, rate, tolerance)
    if rated:
        return sum(rated, Decimal('0'))
    line_hits = [
        item.total for item in items
        if item.total is not None and abs(item.total - implied) <= tolerance
    ]
    if len(line_hits) == 1:
        return line_hits[0]
    if subtotal is not None and abs(subtotal - implied) <= tolerance:
        return subtotal
    net = subtotal - discounts if subtotal is not None else None
    if net is not None and abs(net - implied) <= tolerance:
        return net
    return None


def reconcile_taxes(
    taxes: Sequence[TaxItem],
    items: Sequence[LineItem],
    subtotal: Optional[Decimal],
    discounts: Decimal,
    tolerance: Decimal,
    currency: Optional[str],
) -> Tuple[List[TaxItem], List[Warning]]:
    tax_tolerance = Decimal('5.00') if (currency or 'COP') == 'COP' else Decimal('1.00')
    warnings: List[Warning] = []
    updated: List[TaxItem] = []
    for tax in taxes:
        if tax.type and str(tax.type).upper() not in {'IVA', 'I.V.A.', 'IVA 19%', 'IVA 5%', 'IVA 0%'}:
            updated.append(tax)
            continue
        base = identify_taxable_base(tax, items, subtotal, discounts, tolerance)
        if (
            tax.taxable_base is not None
            and tax.rate is not None
            and tax.amount is not None
            and base is not None
        ):
            rate = _rate_percent(tax.rate)
            stated_expected = _quantize(base * rate / Decimal('100'))
            if abs(stated_expected - tax.amount) > tax_tolerance and tax.amount != 0:
                probe = tax.model_copy(update={'taxable_base': None})
                alternative = identify_taxable_base(probe, items, subtotal, discounts, tolerance)
                if alternative is not None and abs(alternative - base) > tolerance:
                    alternative_expected = _quantize(alternative * rate / Decimal('100'))
                    if abs(alternative_expected - tax.amount) <= tax_tolerance:
                        warnings.append((
                            'TAXABLE_BASE_RECONCILED',
                            (
                                f"stated taxable_base {base}*{rate}% does not match "
                                f"tax.amount={tax.amount}; using identified base {alternative}"
                            ),
                            False,
                        ))
                        base = alternative
        if base is not None and tax.taxable_base != base:
            tax = tax.model_copy(update={'taxable_base': base})
        if tax.rate is not None and tax.amount is not None and tax.taxable_base is not None:
            rate = _rate_percent(tax.rate)
            expected = _quantize(tax.taxable_base * rate / Decimal('100'))
            if abs(expected - tax.amount) > tax_tolerance and tax.amount != 0:
                warnings.append((
                    'IVA_RATE_AMOUNT_MISMATCH',
                    (
                        f"expected ~{expected} (taxable_base {tax.taxable_base}*{rate}%) "
                        f"vs tax.amount={tax.amount}"
                    ),
                    True,
                ))
        updated.append(tax)
    return updated, warnings


def document_total_warnings(doc: ExtractedDocument, items: Sequence[LineItem], tolerance: Decimal) -> List[Warning]:
    warnings: List[Warning] = []
    discounts = Decimal('0')
    for item in items:
        if item.discount is not None:
            discounts += item.discount
    taxes_sum = Decimal('0')
    for tax in doc.taxes:
        if tax.amount is not None:
            taxes_sum += tax.amount
    if doc.subtotal is None or doc.total_amount is None:
        return warnings
    other = doc.other_charges or Decimal('0')
    withholdings = doc.withholdings_amount or Decimal('0')
    expected = doc.subtotal + taxes_sum - discounts - withholdings + other
    if (doc.document_class or '').upper() in {
        'UTILITY_BILL', 'ENERGY_BILL', 'WATER_BILL', 'GAS_BILL',
    } and doc.total_amount + tolerance >= doc.subtotal:
        if abs(expected - doc.total_amount) > tolerance:
            warnings.append((
                'UTILITY_PARTIAL_TOTAL',
                (
                    f"subtotal de componentes {doc.subtotal} no es el total a pagar {doc.total_amount}; "
                    "una factura de servicio puede incluir aseo, terceros u otros cargos fuera de ese subtotal"
                ),
                False,
            ))
        return warnings
    bases = [tax.taxable_base for tax in doc.taxes if tax.taxable_base is not None]
    if len(bases) == 1 and discounts == 0:
        net = bases[0]
        discounted = net + taxes_sum - withholdings + other
        if net < doc.subtotal and abs(discounted - doc.total_amount) <= tolerance:
            return warnings
    if abs(expected - doc.total_amount) > tolerance:
        warnings.append((
            'TOTAL_MISMATCH',
            (
                f"subtotal({doc.subtotal})+taxes({taxes_sum})-discounts({discounts})"
                f"-withholdings({withholdings})+other({other})={expected} "
                f"vs total_amount={doc.total_amount}"
            ),
            True,
        ))
    elif discounts == 0 and other == 0 and withholdings == 0 and doc.subtotal > doc.total_amount:
        warnings.append((
            'SUBTOTAL_EXCEEDS_TOTAL_WITHOUT_DISCOUNT',
            f"subtotal={doc.subtotal} > total_amount={doc.total_amount} without discounts",
            True,
        ))
    return warnings

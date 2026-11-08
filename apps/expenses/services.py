
from django.db import transaction

from apps.documents.models import Document
from apps.documents.models import ExpenseItem as DocumentExpenseItem
from apps.expenses.document_values import _expense_values, resolve_category
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier


class ExpenseService:
    @staticmethod
    def create_from_document(user, document_id: int, **overrides) -> Expense | None:
        try:
            document = Document.objects.get(pk=document_id, user=user)
        except Document.DoesNotExist:
            return None
        if document.total_amount is None:
            raise ValueError('El documento aún no tiene un monto total extraído.')

        with transaction.atomic():
            if hasattr(document, 'expenses') and document.expenses.exists():
                existing = document.expenses.first()
                return existing


            values = _expense_values(document)
            if overrides.get('supplier_id') and not overrides.get('supplier') and values['supplier'] is None:
                values['supplier'] = Supplier.objects.filter(pk=overrides['supplier_id'], user=user).first()
            for field in (
                'category', 'supplier', 'description', 'amount', 'tax_amount', 'discount_amount',
                'tip_amount', 'currency', 'payment_method', 'incurred_at',
            ):
                if overrides.get(field) not in (None, ''):
                    values[field] = overrides[field]
            if values['category'] is None:
                values['category'] = resolve_category(user, values['supplier'])
            if values['category'] is None:
                raise ValueError('No se pudo determinar la categoría del gasto.')

            expense = Expense.objects.create(
                user=user,
                document=document,
                is_recurring=overrides.get('is_recurring', False),
                recurrence_frequency=overrides.get('recurrence_frequency', ''),
                notes=overrides.get('notes', '') or document.notes or '',
                **values,
            )
            line_items = list(document.line_items.all())
            if line_items:
                items_to_create = []
                for idx, doc_item in enumerate(line_items, start=1):
                    items_to_create.append(DocumentExpenseItem(
                        expense=expense,
                        document=None,
                        sequence=idx,
                        sku=doc_item.sku,
                        description=doc_item.description,
                        quantity=doc_item.quantity,
                        unit_price=doc_item.unit_price,
                        unit=doc_item.unit,
                        tax_rate=doc_item.tax_rate,
                        tax_amount=doc_item.tax_amount,
                        discount_amount=doc_item.discount_amount,
                        total_amount=doc_item.total_amount,
                        notes=doc_item.notes,
                        extra=doc_item.extra,
                    ))
                if items_to_create:
                    DocumentExpenseItem.objects.bulk_create(items_to_create)
                    expense.has_line_items = True
                    expense.save(update_fields=['has_line_items', 'updated_at'])
            return expense


expense_service = ExpenseService

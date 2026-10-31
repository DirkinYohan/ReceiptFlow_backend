from django.core.management.base import BaseCommand

from apps.documents.models import Document
from apps.documents.payables import apply_payment_inference
from apps.expenses.automation import sync_expense_safely
from apps.expenses.recurring import refresh_supplier_recurrence
from apps.suppliers.models import Supplier


class Command(BaseCommand):
    help = (
        'Pone al día los documentos ya procesados: estado de pago y vencimiento, '
        'gasto asociado y detección de gastos recurrentes.'
    )

    def handle(self, *args, **options):
        updated = 0
        for doc in Document.objects.exclude(status=Document.STATUS_PROCESSING).iterator():
            before = (doc.is_paid, doc.due_date)
            raw = doc.raw_ocr_data or {}
            apply_payment_inference(
                doc,
                (doc.structured_data or {}).get('document_class'),
                raw.get('extracted_extra_fields') or {},
            )
            if (doc.is_paid, doc.due_date) != before:
                doc.save(update_fields=['is_paid', 'due_date', 'updated_at'])
                updated += 1

        pending = Document.objects.filter(
            status=Document.STATUS_COMPLETED, expenses__isnull=True,
        ).select_related('user', 'supplier', 'category')
        created = sum(1 for doc in pending.iterator() if sync_expense_safely(doc) is not None)

        suppliers = Supplier.objects.filter(expenses__isnull=False).distinct().select_related('user')
        recurring = sum(1 for supplier in suppliers if refresh_supplier_recurrence(supplier.user, supplier))

        self.stdout.write(self.style.SUCCESS(
            f'Pagos/vencimientos actualizados: {updated}. Gastos creados: {created}. '
            f'Proveedores recurrentes: {recurring}.'
        ))

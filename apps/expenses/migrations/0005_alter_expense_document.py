import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('documents', '0006_document_last_due_reminder_at'),
        ('expenses', '0004_alter_expense_currency'),
    ]

    operations = [
        migrations.AlterField(
            model_name='expense',
            name='document',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='expenses', to='documents.document', verbose_name='Documento asociado'),
        ),
    ]

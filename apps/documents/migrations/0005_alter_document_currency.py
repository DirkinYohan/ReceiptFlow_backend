from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('documents', '0004_document_is_paid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='document',
            name='currency',
            field=models.CharField(blank=True, db_index=True, default='COP', max_length=3, verbose_name='Moneda'),
        ),
    ]

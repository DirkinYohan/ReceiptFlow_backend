import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('expenses', '0001_initial'),
        ('suppliers', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='supplier',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses', to='suppliers.supplier', verbose_name='Proveedor'),
        ),
    ]

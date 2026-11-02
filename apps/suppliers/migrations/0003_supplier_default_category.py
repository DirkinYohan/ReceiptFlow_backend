import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('categories', '0002_initial'),
        ('suppliers', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='default_category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='default_for_suppliers', to='categories.category', verbose_name='Categoría aprendida'),
        ),
    ]

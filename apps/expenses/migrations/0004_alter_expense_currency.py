from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0003_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='expense',
            name='currency',
            field=models.CharField(db_index=True, default='COP', max_length=3, verbose_name='Moneda'),
        ),
    ]

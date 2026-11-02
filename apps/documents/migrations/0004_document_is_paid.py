from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('documents', '0003_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='is_paid',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Pagado'),
        ),
    ]

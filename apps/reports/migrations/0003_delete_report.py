from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('reports', '0002_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Report',
        ),
    ]

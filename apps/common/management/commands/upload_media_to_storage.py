"""Copy receipts that only exist in the local media/ folder to the configured storage (Supabase).

Run it once from your computer with the production storage variables, e.g.:
  STORAGE_BACKEND=s3 S3_BUCKET_NAME=... S3_ENDPOINT_URL=... S3_ACCESS_KEY_ID=... \
  S3_SECRET_ACCESS_KEY=... python manage.py upload_media_to_storage
Files already in the bucket are skipped, so it is safe to run again.
"""
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document


class Command(BaseCommand):
    help = 'Sube a Supabase Storage los recibos que solo están en la carpeta media/ local.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo muestra lo que subiría.')

    def handle(self, *args, dry_run=False, **options):
        target = storages['default']
        if 'S3Storage' not in type(target).__name__:
            raise CommandError('El almacenamiento configurado no es S3: define STORAGE_BACKEND=s3 y sus variables.')
        media_root = Path(settings.MEDIA_ROOT)
        uploaded = skipped = missing = 0
        for name in Document.objects.exclude(file='').values_list('file', flat=True).iterator():
            local = media_root / name
            if target.exists(name):
                skipped += 1
                continue
            if not local.is_file():
                missing += 1
                self.stdout.write(self.style.WARNING(f'No está en media/: {name}'))
                continue
            if not dry_run:
                with local.open('rb') as fh:
                    saved = target.save(name, File(fh))
                if saved != name:  # storage renamed it: it would no longer match the database
                    raise CommandError(f'El almacenamiento renombró {name} a {saved}.')
            uploaded += 1
        verb = 'Por subir' if dry_run else 'Subidos'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {uploaded} | ya estaban: {skipped} | sin archivo local: {missing}'))

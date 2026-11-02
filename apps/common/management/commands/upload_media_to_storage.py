from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Document


class Command(BaseCommand):
    help = 'Uploads to Supabase Storage the receipts that only exist in the local media/ folder.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only show what would be uploaded.')

    def handle(self, *args, dry_run=False, **options):
        target = storages['default']
        if 'S3Storage' not in type(target).__name__:
            raise CommandError('The configured storage is not S3: set STORAGE_BACKEND=s3 and its variables.')
        media_root = Path(settings.MEDIA_ROOT)
        uploaded = skipped = missing = 0
        for name in Document.objects.exclude(file='').values_list('file', flat=True).iterator():
            local = media_root / name
            if target.exists(name):
                skipped += 1
                continue
            if not local.is_file():
                missing += 1
                self.stdout.write(self.style.WARNING(f'Not in media/: {name}'))
                continue
            if not dry_run:
                with local.open('rb') as fh:
                    saved = target.save(name, File(fh))
                if saved != name:
                    raise CommandError(f'Storage renamed {name} to {saved}.')
            uploaded += 1
        verb = 'To upload' if dry_run else 'Uploaded'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {uploaded} | already there: {skipped} | missing locally: {missing}'))

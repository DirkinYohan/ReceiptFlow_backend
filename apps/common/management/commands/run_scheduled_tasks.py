import json

from django.core.management.base import BaseCommand

from apps.common.scheduled import run_scheduled_tasks


class Command(BaseCommand):
    help = 'Ejecuta las tareas diarias: documentos atascados, avisos de vencimiento y limpieza de tokens.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(run_scheduled_tasks(), default=str, ensure_ascii=False))

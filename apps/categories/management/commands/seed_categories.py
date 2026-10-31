from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.utils.text import slugify
from django.core.management.base import BaseCommand, CommandError


DEFAULT_SYSTEM_CATEGORIES = [
    {'name': 'Food', 'icon': 'utensils', 'color': '#F59E0B', 'sort_order': 10,
     'description': 'Gastos en restaurantes, comida, supermercado y alimentos.'},
    {'name': 'Transportation', 'icon': 'car', 'color': '#3B82F6', 'sort_order': 20,
     'description': 'Gastos de transporte: gasolina, taxi, autobús, vuelos, peajes.'},
    {'name': 'Technology', 'icon': 'laptop', 'color': '#8B5CF6', 'sort_order': 30,
     'description': 'Hardware, software, suscripciones tecnológicas, accesorios.'},
    {'name': 'Health', 'icon': 'heart-pulse', 'color': '#EF4444', 'sort_order': 40,
     'description': 'Médicos, medicinas, seguros de salud, consultas, dentistas.'},
    {'name': 'Home', 'icon': 'home', 'color': '#10B981', 'sort_order': 50,
     'description': 'Renta, servicios, muebles, reparaciones y mantenimiento del hogar.'},
    {'name': 'Entertainment', 'icon': 'film', 'color': '#EC4899', 'sort_order': 60,
     'description': 'Cine, streaming, eventos, deportes, suscripciones de ocio.'},
    {'name': 'Education', 'icon': 'graduation-cap', 'color': '#06B6D4', 'sort_order': 70,
     'description': 'Cursos, libros, colegiaturas, capacitaciones y material educativo.'},
    {'name': 'Shopping', 'icon': 'shopping-bag', 'color': '#F97316', 'sort_order': 80,
     'description': 'Ropa, accesorios, compras generales y bienes de consumo.'},
    {'name': 'Other', 'icon': 'folder', 'color': '#6B7280', 'sort_order': 99,
     'description': 'Gastos misceláneos que no encajan en otras categorías.'},
]


class Command(BaseCommand):
    help = 'Creates default system categories if they do not exist yet.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Reset all system categories to their default values.',
        )

    def handle(self, *args, **options):
        Category = apps.get_model('categories', 'Category')
        reset = options.get('reset', False)

        created = 0
        updated = 0

        with transaction.atomic():
            for entry in DEFAULT_SYSTEM_CATEGORIES:
                name = entry['name']
                defaults = {
                    'icon': entry['icon'],
                    'color': entry['color'],
                    'sort_order': entry['sort_order'],
                    'description': entry.get('description', ''),
                    'slug': slugify(name),
                    'scope': Category.SCOPE_SYSTEM,
                    'is_active': True,
                }
                qs = Category.objects.filter(name=name, scope=Category.SCOPE_SYSTEM, user__isnull=True)
                if qs.exists():
                    obj = qs.first()
                    if reset:
                        for key, value in defaults.items():
                            setattr(obj, key, value)
                        obj.save()
                        updated += 1
                    continue
                Category.objects.create(user=None, name=name, **defaults)
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'System categories processed: created={created}, updated={updated}.'
            )
        )

import atexit

from django.apps import AppConfig


def _close_connection_pools():
    from django.db import connections

    for alias in connections:
        close_pool = getattr(connections[alias], 'close_pool', None)
        if close_pool:
            close_pool()


class CommonConfig(AppConfig):
    name = 'apps.common'
    label = 'common'

    def ready(self):
        atexit.register(_close_connection_pools)

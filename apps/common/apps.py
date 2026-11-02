import atexit

from django.apps import AppConfig


def _close_connection_pools():
    """Stop the database pool's worker threads before the interpreter shuts down.

    Otherwise a command that ends while the pool is still busy prints
    "PythonFinalizationError: cannot join thread at interpreter shutdown".
    """
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

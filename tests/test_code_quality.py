import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ('apps', 'services', 'config')


def _python_files():
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob('*.py'):
            if 'migrations' not in path.parts:
                yield path


def test_broad_excepts_log_or_reraise():
    silent = []
    for path in _python_files():
        source = path.read_text(encoding='utf-8')
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ('Exception', 'BaseException'))
            if not broad:
                continue
            body = '\n'.join(lines[node.lineno:node.end_lineno])
            if 'logger.' not in body and 'raise' not in body:
                silent.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    assert silent == [], f'broad except blocks that hide errors: {silent}'

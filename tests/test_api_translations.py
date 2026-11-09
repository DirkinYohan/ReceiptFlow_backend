import ast
from pathlib import Path

from apps.common.i18n import translate_message

ROOT = Path(__file__).resolve().parent.parent
MESSAGE_FILES = ('views.py', 'serializers.py', 'validators.py', 'exceptions.py', 'budget_views.py', 'responses.py')


def _is_validation_error(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
    return name in ('ValidationError', 'AuthenticationFailed', 'PermissionDenied', 'NotFound')


def _constant_strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            yield from _constant_strings(item)
    elif isinstance(node, ast.Dict):
        for value in node.values:
            yield from _constant_strings(value)


def api_messages():
    found = {}
    for path in (ROOT / 'apps').rglob('*.py'):
        if path.name not in MESSAGE_FILES or 'migrations' in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value in ('message', 'detail'):
                        for text in _constant_strings(value):
                            found.setdefault(text, f'{path.relative_to(ROOT)}:{node.lineno}')
            if isinstance(node, ast.Call) and _is_validation_error(node) and node.args:
                for text in _constant_strings(node.args[0]):
                    found.setdefault(text, f'{path.relative_to(ROOT)}:{node.lineno}')
    return found


def test_every_api_message_has_an_english_translation():
    missing = [
        f'{where}  {text!r}'
        for text, where in sorted(api_messages().items(), key=lambda item: item[1])
        if text.strip() and translate_message(text) == text
    ]
    assert missing == [], 'messages without English translation:\n' + '\n'.join(missing)

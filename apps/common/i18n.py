from __future__ import annotations

import json
import re
from typing import Any

MESSAGES = {
    'Has excedido el número máximo de solicitudes permitidas. Por favor, inténtalo de nuevo más tarde.':
        'You have exceeded the maximum number of requests. Please try again later.',
    'Error de integridad en la base de datos.': 'Database integrity error.',
    'Error de validación.': 'Validation error.',
    'Error de validación en los datos enviados.': 'The data sent is not valid.',
    'Ocurrió un error interno en el servidor.': 'An internal server error occurred.',
    'No tienes permiso para realizar esta acción.': 'You do not have permission to do this.',
    'Error desconocido.': 'Unknown error.',
    'No se ha proporcionado ningún archivo.': 'No file was provided.',
    'No se pudo determinar la extensión del archivo.': "The file's extension could not be determined.",
    'Se requiere un archivo para subir.': 'A file is required.',
    'El archivo no cumple los requisitos.': 'The file does not meet the requirements.',
    'Documento subido correctamente.': 'Document uploaded.',
    'Detalle de documento.': 'Document details.',
    'Documentos.': 'Documents.',
    'No puedes editar un documento en procesamiento.': 'You cannot edit a document while it is being read.',
    'Documento actualizado correctamente.': 'Document updated.',
    'Documento eliminado correctamente.': 'Document deleted.',
    'El documento ya está siendo procesado.': 'The document is already being read.',
    'Se alcanzó el máximo de intentos de procesamiento. Contacta soporte si el problema persiste.':
        'The maximum number of reading attempts was reached. Contact support if the problem continues.',
    'Pipeline de procesamiento no disponible.': 'The reading pipeline is not available.',
    'Servicio de OCR no disponible.': 'The reading service is not available.',
    'Error durante el procesamiento.': 'An error occurred while reading the document.',
    'Documento procesado.': 'Document read.',
    'No se pudo programar el procesamiento.': 'The reading could not be scheduled.',
    'No se pudo subir el documento. Inténtalo de nuevo en unos minutos.': 'The document could not be uploaded. Try again in a few minutes.',
    'No se pudo eliminar el documento. Inténtalo de nuevo.': 'The document could not be deleted. Try again.',
    'No se pudo leer el archivo.': 'The file could not be read.',
    'Documento enviado a procesamiento de forma asíncrona. El estado se actualizará automáticamente cuando finalice.':
        'The document is being read. Its status will update automatically when it finishes.',
    'Ya se está leyendo este documento. Espera unos segundos.': 'This document is already being read. Wait a few seconds.',
    'Proveedor no encontrado.': 'Supplier not found.',
    'Categoría no encontrada.': 'Category not found.',
    'Datos del documento actualizados.': 'Document details updated.',
    'Documento en procesamiento.': 'Document being read.',
    'No puedes establecer el estado solicitado manualmente.': 'You cannot set that status by hand.',
    'El documento aún no tiene un monto total extraído.': 'The document does not have a total amount yet.',
    'No se pudo determinar la categoría del gasto.': "The expense's category could not be determined.",
    'Detalle de gasto.': 'Expense details.',
    'Documento no encontrado.': 'Document not found.',
    'Gasto creado a partir del documento.': 'Expense created from the document.',
    'Gasto registrado correctamente.': 'Expense saved.',
    'Gasto actualizado correctamente.': 'Expense updated.',
    'Gasto eliminado correctamente.': 'Expense deleted.',
    'Formato no soportado. Usa xlsx o csv.': 'Unsupported format. Use xlsx or csv.',
    'Fechas inválidas. Usa AAAA-MM-DD.': 'Invalid dates. Use YYYY-MM-DD.',
    'El presupuesto debe ser mayor que cero.': 'The budget must be greater than zero.',
    'No puedes modificar categorías del sistema.': 'You cannot change system categories.',
    'No puedes eliminar categorías del sistema.': 'You cannot delete system categories.',
    'Favorito actualizado.': 'Favorite updated.',
    'Registro exitoso.': 'Registration complete.',
    'Te enviamos un correo de verificación. Revisa tu bandeja de entrada y haz clic en el enlace para activar tu cuenta antes de iniciar sesión.':
        'We sent you a verification email. Open it and click the link to activate your account before signing in.',
    'Inicio de sesión exitoso.': 'Signed in.',
    'Sesión cerrada correctamente.': 'Signed out.',
    'Tokens actualizados.': 'Tokens refreshed.',
    'Perfil de usuario.': 'User profile.',
    'Perfil actualizado correctamente.': 'Profile updated.',
    'Contraseña actualizada correctamente.': 'Password updated.',
    'Si existe una cuenta activa con ese correo electrónico, hemos enviado las instrucciones para restablecer la contraseña.':
        'If an active account exists for that email, we sent instructions to reset the password.',
    'Contraseña restablecida correctamente.': 'Password reset.',
    'Correo electrónico verificado correctamente.': 'Email verified.',
    'Si existe una cuenta pendiente de verificación, hemos reenviado el correo de confirmación.':
        'If an account is waiting for verification, we sent the confirmation email again.',
    'No puedes desactivar tu propia cuenta.': 'You cannot disable your own account.',
    'Cuenta activada correctamente.': 'Account enabled.',
    'Cuenta desactivada correctamente.': 'Account disabled.',
    'El correo y la contraseña son requeridos.': 'Email and password are required.',
    'No existe una cuenta con este correo electrónico.': 'There is no account with this email.',
    'Tu cuenta se encuentra desactivada.': 'Your account is disabled.',
    'Contraseña incorrecta.': 'Incorrect password.',
    'Debes verificar tu correo electrónico antes de iniciar sesión. Revisa tu bandeja de entrada y haz clic en el enlace de verificación.':
        'You must verify your email before signing in. Open your inbox and click the verification link.',
    'Ya existe un usuario registrado con este correo electrónico.': 'A user with this email already exists.',
    'Debes aceptar los términos y condiciones.': 'You must accept the terms and conditions.',
    'Las contraseñas no coinciden.': 'The passwords do not match.',
    'La contraseña actual es incorrecta.': 'The current password is incorrect.',
    'Las contraseñas nuevas no coinciden.': 'The new passwords do not match.',
    'La nueva contraseña debe ser diferente a la actual.': 'The new password must be different from the current one.',
    'Token de recuperación inválido.': 'Invalid recovery token.',
    'El token de recuperación ha expirado.': 'The recovery token has expired.',
    'Token de verificación inválido.': 'Invalid verification token.',
    'El token de verificación ha expirado.': 'The verification token has expired.',
    'Refresh token es requerido.': 'A refresh token is required.',
}

STATUS_NAMES = {
    'Pendiente': 'Pending',
    'Procesando': 'Processing',
    'Procesado': 'Processed',
    'Revisión requerida': 'Review required',
    'Error': 'Error',
}

PATTERNS = [
    (r'Tipo de archivo no permitido \(\.(.+?)\)\. Extensiones permitidas: (.+)\.',
     'File type not allowed (.{0}). Allowed extensions: {1}.'),
    (r'El archivo excede el tamaño máximo permitido \((.+?)MB\)\.',
     'The file is larger than the maximum size allowed ({0} MB).'),
    (r'El contenido del archivo no corresponde a un formato \.(.+?) válido o está corrupto\.',
     'The file content is not a valid .{0} file or it is corrupted.'),
    (r'Error al subir el documento: (.*)', 'Error uploading the document: {0}'),
    (r'Error al eliminar el documento: (.*)', 'Error deleting the document: {0}'),
    (r'No se pudo encolar la tarea: (.*)', 'The reading could not be queued: {0}'),
    (r'No se puede reintentar un documento en estado "(.+?)"\.', 'A document with status "{0}" cannot be read again.'),
]
_COMPILED = [(re.compile(rf'^{pattern}$', re.S), template) for pattern, template in PATTERNS]


def translate_message(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text
    if text in MESSAGES:
        return MESSAGES[text]
    for pattern, template in _COMPILED:
        match = pattern.match(text)
        if match:
            groups = [STATUS_NAMES.get(group, group) for group in match.groups()]
            return template.format(*groups)
    return text


def translate_payload(data: Any, key: str = '') -> Any:
    if isinstance(data, dict):
        return {k: translate_payload(v, k) for k, v in data.items()}
    if isinstance(data, list):
        return [translate_payload(item, key) for item in data]
    if isinstance(data, str) and key in ('message', 'detail', 'errors', 'non_field_errors') or (
        isinstance(data, str) and key.startswith('non_field_errors')
    ):
        return translate_message(data)
    return data


def wants_english(request) -> bool:
    lang = getattr(request, 'LANGUAGE_CODE', '') or request.headers.get('Accept-Language', '')
    return lang.lower().startswith('en')


class ApiLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith('/api/') or not wants_english(request):
            return response
        if 'application/json' not in response.get('Content-Type', '') or getattr(response, 'streaming', False):
            return response
        try:
            payload = json.loads(response.content.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return response
        translated = translate_payload(payload)
        if translated != payload:
            response.content = json.dumps(translated, ensure_ascii=False).encode('utf-8')
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
        return response

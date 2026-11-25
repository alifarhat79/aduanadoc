"""
Mensajes claros para fallas de conexión (internet caído, Google Drive o Turso que no responden).

Muchas de estas excepciones no traen texto (p. ej. TimeoutError), y en la consola quedaban mensajes
vacíos como "[CheckNew] No se pudo consultar Google Drive:".
"""
import asyncio
import socket
import ssl


def es_error_de_conexion(e: BaseException) -> bool:
    """¿Es una falla de red pasajera (vale la pena reintentar más tarde)?"""
    try:
        import httpx
        if isinstance(e, httpx.TransportError):  # sin conexión, tiempo agotado, conexión cortada
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        import httplib2  # cliente HTTP de la API de Google Drive
        if isinstance(e, httplib2.HttpLib2Error):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError, socket.timeout,
                          socket.gaierror, ssl.SSLError))


def describir_error(e: BaseException) -> str:
    """Tipo y mensaje del error; nunca un texto vacío."""
    texto = str(e).strip()
    if not texto and isinstance(e, (TimeoutError, asyncio.TimeoutError, socket.timeout)):
        return "tiempo de espera agotado"
    nombre = type(e).__name__
    return f"{nombre}: {texto}" if texto else nombre

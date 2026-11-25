"""
Abre el navegador recién cuando el servidor de AduanaDoc ya responde.

Antes iniciar_app.bat abría el navegador a los 2 segundos fijos, pero el servidor recién acepta
conexiones al terminar su arranque (base de datos y claves desde Google Drive, que puede tardar hasta
20 s): el navegador llegaba antes y mostraba "127.0.0.1 rechazó la conexión" (ERR_CONNECTION_REFUSED).

Uso: python scripts/abrir_navegador.py [http://127.0.0.1:8000]
"""
import sys
import time
import urllib.request
import webbrowser

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
ESPERA_MAXIMA = 180  # segundos
SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 nunca va por un proxy


def servidor_listo() -> bool:
    try:
        # Archivo estático: responde apenas el servidor terminó de arrancar, sin consultar la base
        with SIN_PROXY.open(f"{URL}/static/css/custom.css", timeout=3) as resp:
            return resp.status < 500
    except Exception:
        return False


def main() -> None:
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_MAXIMA:
        if servidor_listo():
            break
        time.sleep(1)
    # Si el servidor no arrancó, igual se abre: la consola del servidor muestra el error
    webbrowser.open(URL)


if __name__ == "__main__":
    main()

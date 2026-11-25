"""
El navegador se abre recién cuando el servidor responde (antes: a los 2 s fijos -> ERR_CONNECTION_REFUSED).
Usa un servidor HTTP de prueba en un puerto libre; nunca abre un navegador real.
"""
import importlib.util
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "abrir_navegador.py"


def _cargar(url, monkeypatch, abiertos):
    monkeypatch.setattr("sys.argv", ["abrir_navegador.py", url])
    spec = importlib.util.spec_from_file_location("abrir_navegador_prueba", RUTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.webbrowser, "open", lambda u: abiertos.append((u, time.monotonic())))
    return mod


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Estatico(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/static/css/custom.css" else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def test_espera_a_que_el_servidor_responda(monkeypatch):
    puerto = _puerto_libre()
    abiertos = []
    mod = _cargar(f"http://127.0.0.1:{puerto}", monkeypatch, abiertos)
    servidor = {}

    def arrancar_tarde():
        time.sleep(1.5)  # como el servidor real, que tarda en aceptar conexiones
        servidor["s"] = ThreadingHTTPServer(("127.0.0.1", puerto), _Estatico)
        servidor["s"].serve_forever()

    hilo = threading.Thread(target=arrancar_tarde, daemon=True)
    inicio = time.monotonic()
    hilo.start()
    try:
        mod.main()
    finally:
        if "s" in servidor:
            servidor["s"].shutdown()
    assert abiertos and abiertos[0][0] == f"http://127.0.0.1:{puerto}"
    assert abiertos[0][1] - inicio >= 1.5  # no se abrió antes de que el servidor estuviera listo


def test_si_el_servidor_no_arranca_igual_abre(monkeypatch):
    abiertos = []
    mod = _cargar(f"http://127.0.0.1:{_puerto_libre()}", monkeypatch, abiertos)
    monkeypatch.setattr(mod, "ESPERA_MAXIMA", 2)
    mod.main()
    assert len(abiertos) == 1

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_configuracion_unauthenticated_shows_lock_screen():
    # Sin cookie de sesión, debe mostrar la pantalla de bloqueo
    response = client.get("/configuracion")
    assert response.status_code == 200
    assert "Acceso Restringido" in response.text
    assert "Panel Exclusivo del Programador" in response.text
    assert "Desbloquear Configuración" in response.text


def test_configuracion_login_wrong_password():
    response = client.post("/configuracion/login", json={"password": "wrong_password_123"})
    assert response.status_code == 401
    assert "Contraseña incorrecta" in response.text


def test_configuracion_login_and_access_full_lifecycle():
    # 1. Login exitoso con la clave maestra
    response = client.post("/configuracion/login", json={"password": "Sohalia2012*@"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "aduanadoc_admin_session" in response.cookies

    # 2. Acceso con la cookie de sesión a /configuracion
    cookie_val = response.cookies.get("aduanadoc_admin_session")
    res_auth = client.get("/configuracion", cookies={"aduanadoc_admin_session": cookie_val})
    assert res_auth.status_code == 200
    assert "Modo Programador Activo" in res_auth.text
    assert "Conexión con Turso Cloud Database" in res_auth.text
    assert "Cerrar Sesión" in res_auth.text

    # 3. Acceso a endpoint protegido con cookie
    res_api = client.get("/configuracion/api/gdrive/watcher/status")
    assert res_api.status_code == 200

    # 4. Logout
    res_logout = client.get("/configuracion/logout")
    assert res_logout.status_code == 200 or res_logout.status_code == 303

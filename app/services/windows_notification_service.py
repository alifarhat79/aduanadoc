import subprocess
import threading
import logging
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)

class WindowsNotificationService:
    @staticmethod
    def send_notification(title: str, message: str) -> None:
        """
        Envía una notificación nativa de Windows (Toast o BalloonTip) de forma asíncrona no bloqueante.
        """
        if sys.platform != "win32":
            logger.info(f"[Notification] {title}: {message}")
            return

        def _do_send():
            # Script de PowerShell que intenta Toast nativo de Windows 10/11 y hace fallback a BalloonTip
            ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{title_b64}'))
$msg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{msg_b64}'))

try {{
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textNodes = $template.GetElementsByTagName('text')
    $textNodes.Item(0).AppendChild($template.CreateTextNode($title)) | Out-Null
    $textNodes.Item(1).AppendChild($template.CreateTextNode($msg)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AduanaDoc').Show($toast)
}} catch {{
    try {{
        Add-Type -AssemblyName System.Windows.Forms
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipTitle = $title
        $notify.BalloonTipText = $msg
        $notify.Visible = $True
        $notify.ShowBalloonTip(5000)
        Start-Sleep -Seconds 2
        $notify.Dispose()
    }} catch {{}}
}}
"""
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                    capture_output=True,
                    timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            except Exception as e:
                logger.warning(f"[WindowsNotificationService] No se pudo emitir notificación de Windows: {e}")

        import base64
        title_b64 = base64.b64encode(title.encode('utf-8')).decode('ascii')
        msg_b64 = base64.b64encode(message.encode('utf-8')).decode('ascii')

        # Lanzar en hilo secundario para retorno instantáneo
        thread = threading.Thread(target=_do_send, daemon=True)
        thread.start()

    @classmethod
    def notify_new_despacho(cls, numero_despacho: str, importador: Optional[str] = None):
        """Notifica la adición de un despacho individual."""
        title = "📦 AduanaDoc: Nuevo Despacho Registrado"
        if importador:
            message = f"Despacho {numero_despacho}\nImportador: {importador}"
        else:
            message = f"Despacho {numero_despacho} procesado e indexado."
        cls.send_notification(title, message)

    @classmethod
    def notify_batch_despachos(cls, count: int, despachos_ejemplo: Optional[List[str]] = None):
        """Notifica la adición de varios despachos."""
        title = "📦 AduanaDoc: Nuevos Despachos Detectados"
        if despachos_ejemplo:
            primeros = ", ".join(despachos_ejemplo[:3])
            if len(despachos_ejemplo) > 3:
                primeros += f" y {len(despachos_ejemplo) - 3} más"
            message = f"Se agregaron {count} despachos desde Google Drive:\n{primeros}"
        else:
            message = f"Se agregaron {count} nuevos despachos automáticamente."
        cls.send_notification(title, message)

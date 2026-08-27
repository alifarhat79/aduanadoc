import logging
import httpx
import os
from typing import Dict, Any, Optional, List
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        webhook_url: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        self.telegram_token = (telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", getattr(settings, "TELEGRAM_BOT_TOKEN", "")) or "").strip()
        self.telegram_chat_id = (telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", getattr(settings, "TELEGRAM_CHAT_ID", "")) or "").strip()
        self.webhook_url = (webhook_url or os.getenv("WEBHOOK_URL", getattr(settings, "WEBHOOK_URL", "")) or "").strip()
        self.enabled = enabled if enabled is not None else getattr(settings, "NOTIFICATIONS_ENABLED", True)

    def send_telegram_message(self, text: str, parse_mode: str = "HTML") -> Dict[str, Any]:
        """
        Envía un mensaje formateado a un chat/canal/grupo de Telegram usando la Bot API oficial.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            return {"success": False, "error": "Telegram Token o Chat ID no configurados."}

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return {"success": True, "message": "Mensaje enviado a Telegram con éxito."}
                else:
                    err_msg = resp.text
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("description", err_msg)
                    except Exception:
                        pass
                    return {"success": False, "error": f"Telegram API Error ({resp.status_code}): {err_msg}"}
        except Exception as e:
            logger.error(f"[NotificationService] Error enviando a Telegram: {e}")
            return {"success": False, "error": str(e)}

    def send_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Envía una notificación HTTP POST a una URL de Webhook personalizada (Discord, Zapier, Make, n8n, etc.).
        """
        if not self.webhook_url:
            return {"success": False, "error": "Webhook URL no configurada."}

        try:
            headers = {"Content-Type": "application/json"}
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.webhook_url, json=payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    return {"success": True, "message": "Notificación Webhook enviada con éxito."}
                else:
                    return {"success": False, "error": f"Webhook Error ({resp.status_code}): {resp.text}"}
        except Exception as e:
            logger.error(f"[NotificationService] Error enviando a Webhook: {e}")
            return {"success": False, "error": str(e)}

    def notify_new_despacho(self, despacho_dict: Dict[str, Any], items_count: int = 0, source: str = "Google Drive Auto") -> Dict[str, Any]:
        """
        Envía una notificación completa tras el procesamiento exitoso de un despacho.
        """
        if not self.enabled:
            return {"success": False, "message": "Notificaciones desactivadas en la configuración."}

        nro_despacho = despacho_dict.get("numero_despacho") or "S/N"
        importador = despacho_dict.get("importador_nombre") or "No identificado"
        propietario = despacho_dict.get("propietario") or "Sin Asignar"
        canal = (despacho_dict.get("canal") or "VERDE").upper()
        fob = despacho_dict.get("valor_fob") or 0.0
        cif = despacho_dict.get("valor_cif") or 0.0
        archivo = despacho_dict.get("nombre_archivo_original") or "despacho.pdf"

        # Emoji de canal
        canal_emoji = "🟢" if "VERDE" in canal else ("🟠" if "NARANJA" in canal else "🔴")

        # 1. Mensaje para Telegram en HTML
        tg_text = (
            f"📦 <b>¡Nuevo Despacho Procesado!</b>\n\n"
            f"📄 <b>Nº Despacho:</b> <code>{nro_despacho}</code>\n"
            f"👤 <b>Importador:</b> {importador}\n"
            f"🏷️ <b>Dueño / Cliente:</b> {propietario}\n"
            f"🚦 <b>Canal:</b> {canal_emoji} <b>{canal}</b>\n"
            f"💰 <b>Total FOB:</b> ${fob:,.2f} USD\n"
            f"💵 <b>Total CIF:</b> ${cif:,.2f} USD\n"
            f"📋 <b>Mercancías / Ítems:</b> {items_count} extraídos\n"
            f"📁 <b>Archivo:</b> {archivo}\n"
            f"🤖 <b>Origen:</b> {source}\n\n"
            f"<i>AduanaDoc - Sistema de Gestión Aduanera</i>"
        )

        # 2. Payload para Webhook
        webhook_payload = {
            "event": "despacho_procesado",
            "source": source,
            "despacho": {
                "numero_despacho": nro_despacho,
                "importador": importador,
                "propietario": propietario,
                "canal": canal,
                "valor_fob": fob,
                "valor_cif": cif,
                "items_extraidos": items_count,
                "archivo": archivo
            }
        }

        results = {}
        if self.telegram_token and self.telegram_chat_id:
            results["telegram"] = self.send_telegram_message(tg_text)

        if self.webhook_url:
            results["webhook"] = self.send_webhook(webhook_payload)

        return {
            "success": True,
            "results": results
        }

    def send_test_notification(self) -> Dict[str, Any]:
        """
        Envía un mensaje de prueba a Telegram y/o Webhook para verificar la configuración.
        """
        test_text = (
            "🔔 <b>AduanaDoc - Notificación de Prueba</b>\n\n"
            "✅ La conexión con el sistema de alertas de AduanaDoc funciona correctamente.\n"
            "Recibirás un aviso cada vez que el Auto-Vigilante procese nuevos despachos aduaneros.\n\n"
            "<i>Fecha y Hora: Sistema Activo</i>"
        )
        test_payload = {
            "event": "test_notification",
            "message": "Prueba de conexión exitosa desde AduanaDoc.",
            "status": "OK"
        }

        results = {}
        has_any = False

        if self.telegram_token and self.telegram_chat_id:
            has_any = True
            results["telegram"] = self.send_telegram_message(test_text)

        if self.webhook_url:
            has_any = True
            results["webhook"] = self.send_webhook(test_payload)

        if not has_any:
            return {
                "success": False,
                "error": "No hay ningún canal configurado. Ingresa el Token y Chat ID de Telegram o una URL de Webhook."
            }

        all_ok = all(r.get("success", False) for r in results.values())
        return {
            "success": all_ok,
            "results": results,
            "message": "Notificación de prueba enviada con éxito." if all_ok else "Ocurrió un error en uno de los canales configurados."
        }

import smtplib
import ssl
from email.message import EmailMessage


def _smtp_config() -> dict | None:
    try:
        import streamlit as st

        host = str(st.secrets.get("SMTP_HOST", "")).strip()
        usuario = str(st.secrets.get("SMTP_USER", "")).strip()
        senha = str(st.secrets.get("SMTP_PASSWORD", "")).strip()
        porta = int(st.secrets.get("SMTP_PORT", 587) or 587)
        remetente = str(st.secrets.get("SMTP_FROM", "") or usuario).strip()
    except Exception:
        return None
    if not host or not usuario or not senha:
        return None
    return {"host": host, "porta": porta, "usuario": usuario, "senha": senha, "remetente": remetente}


def enviar_email(destinatario: str, assunto: str, corpo: str) -> bool:
    destinatario = (destinatario or "").strip()
    config = _smtp_config()
    if not config or not destinatario:
        print(
            f"[email] Envio pulado (SMTP não configurado ou destinatário vazio). destinatario={destinatario!r}",
            flush=True,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = config["remetente"]
    msg["To"] = destinatario
    msg.set_content(corpo)

    try:
        context = ssl.create_default_context()
        if config["porta"] == 465:
            with smtplib.SMTP_SSL(config["host"], config["porta"], timeout=15, context=context) as server:
                server.login(config["usuario"], config["senha"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(config["host"], config["porta"], timeout=15) as server:
                server.starttls(context=context)
                server.login(config["usuario"], config["senha"])
                server.send_message(msg)
        print(f"[email] Enviado para {destinatario}: {assunto}", flush=True)
        return True
    except Exception as e:
        print(f"[email] Falha ao enviar para {destinatario}: {e}", flush=True)
        return False

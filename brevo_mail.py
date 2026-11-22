"""
Send an already-built MIME message through Brevo's HTTPS API.

Why: some hosts (e.g. Render's free plan) block outbound SMTP ports, so
smtplib can never reach Gmail. Brevo's API goes over normal HTTPS (port 443).

Enabled only when the BREVO_API_KEY environment variable is set; otherwise
the callers keep using SMTP exactly as before.

Brevo setup: create a free account, verify your sender address
(Senders, Domains & Dedicated IPs -> Senders), then create an API key
(SMTP & API -> API Keys). Free plan: 300 emails/day.
"""
import base64
import os
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def brevo_api_key():
    return (os.environ.get("BREVO_API_KEY") or "").strip()


def brevo_enabled():
    return bool(brevo_api_key())


def _decode(value):
    try:
        return str(make_header(decode_header(str(value or ""))))
    except Exception:
        return str(value or "")


def send_mime_via_brevo(msg, sender_email, sender_name="", timeout=20):
    """POST `msg` (an email.message.Message) to Brevo. Raises RuntimeError on failure."""
    import requests

    api_key = brevo_api_key()
    if not api_key:
        raise RuntimeError("BREVO_API_KEY is not set")

    html_body = text_body = None
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            attachments.append({"name": _decode(filename),
                                "content": base64.b64encode(payload).decode("ascii")})
        elif part.get_content_type() == "text/html" and html_body is None:
            html_body = payload.decode(part.get_content_charset() or "utf-8", "replace")
        elif part.get_content_type() == "text/plain" and text_body is None:
            text_body = payload.decode(part.get_content_charset() or "utf-8", "replace")

    to = []
    for name, addr in getaddresses(msg.get_all("To", [])):
        if addr:
            entry = {"email": addr}
            if name:
                entry["name"] = _decode(name)
            to.append(entry)
    if not to:
        raise RuntimeError("Message has no recipient")

    body = {
        "sender": {"email": sender_email, **({"name": sender_name} if sender_name else {})},
        "to": to,
        "subject": _decode(msg.get("Subject")) or "(no subject)",
        "htmlContent": html_body or "<p>{}</p>".format(text_body or ""),
    }
    if text_body:
        body["textContent"] = text_body
    reply_to = parseaddr(str(msg.get("Reply-To") or ""))[1]
    if reply_to:
        body["replyTo"] = {"email": reply_to}
    if attachments:
        body["attachment"] = attachments

    response = requests.post(
        BREVO_API_URL, json=body, timeout=timeout,
        headers={"api-key": api_key, "accept": "application/json",
                 "content-type": "application/json"},
    )
    if response.status_code >= 300:
        raise RuntimeError("Brevo API error {}: {}".format(response.status_code, response.text[:300]))
    return response.json() if response.content else {}

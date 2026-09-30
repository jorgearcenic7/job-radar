import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from job_radar.storage import get_active_matches


RESEND_EMAILS_URL = "https://api.resend.com/emails"
DEFAULT_NOTIFICATION_FROM = "Job Radar <onboarding@resend.dev>"
LOGGER = logging.getLogger(__name__)


def safe_https_url(value):
    try:
        parsed = urlsplit(value or "")
    except (TypeError, ValueError):
        return None

    if parsed.scheme != "https" or not parsed.netloc:
        return None

    return value


def build_match_email(matches, partial=False):
    count = len(matches)
    match_label = "coincidencia" if count == 1 else "coincidencias"
    active_label = "activa" if count == 1 else "activas"
    date = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    qualifier = " · ejecución parcial" if partial else ""
    subject = f"Job Radar · {count} {match_label} · {date}{qualifier}"

    summary = (
        "La ingesta terminó con errores en una o más fuentes. "
        "El listado contiene los datos que pudieron actualizarse."
        if partial
        else "La ingesta diaria ha terminado correctamente."
    )

    cards = []
    text_items = []

    for index, job in enumerate(matches, start=1):
        company = str(job.get("company") or "Empresa no indicada")
        title = str(job.get("title") or "Puesto sin título")
        status = str(job.get("match_status") or "Coincidencia")
        location = str(job.get("location") or "Ubicación no indicada")
        salary = str(job.get("salary_text") or "No publicado")
        experience = str(job.get("experience_text") or "No publicada")
        url = safe_https_url(job.get("url"))

        link = (
            '<a href="{}" style="color:#047857;font-weight:600;">'
            "Ver oferta</a>".format(escape(url, quote=True))
            if url
            else '<span style="color:#64748b;">Enlace no disponible</span>'
        )

        cards.append(
            """
            <div style="border:1px solid #e2e8f0;border-radius:12px;
                        margin:0 0 14px;padding:18px;">
              <div style="color:#047857;font-size:12px;font-weight:700;
                          letter-spacing:.04em;text-transform:uppercase;">
                {status}
              </div>
              <h2 style="color:#0f172a;font-size:18px;line-height:1.35;
                         margin:6px 0 4px;">{title}</h2>
              <div style="color:#334155;font-size:14px;font-weight:600;">
                {company}
              </div>
              <div style="color:#64748b;font-size:13px;line-height:1.6;
                          margin:10px 0;">
                {location}<br>
                Salario: {salary}<br>
                Experiencia: {experience}
              </div>
              {link}
            </div>
            """.format(
                status=escape(status),
                title=escape(title),
                company=escape(company),
                location=escape(location),
                salary=escape(salary),
                experience=escape(experience),
                link=link,
            )
        )

        text_items.extend([
            f"{index}. [{status}] {title} — {company}",
            f"   Ubicación: {location}",
            f"   Salario: {salary}",
            f"   Experiencia: {experience}",
            f"   {url or 'Enlace no disponible'}",
            "",
        ])

    if not cards:
        cards.append(
            '<p style="color:#475569;">No hay coincidencias activas.</p>'
        )
        text_items.append("No hay coincidencias activas.")

    html = """
    <!doctype html>
    <html lang="es">
      <body style="background:#f8fafc;font-family:Arial,sans-serif;
                   margin:0;padding:24px;">
        <div style="background:#ffffff;border-radius:16px;margin:0 auto;
                    max-width:680px;padding:28px;">
          <div style="color:#047857;font-size:13px;font-weight:700;">
            JOB RADAR
          </div>
          <h1 style="color:#0f172a;font-size:26px;margin:8px 0;">
            {count} {match_label} {active_label}
          </h1>
          <p style="color:#475569;font-size:14px;line-height:1.6;
                    margin:0 0 22px;">{summary}</p>
          {cards}
          <p style="color:#94a3b8;font-size:12px;margin:24px 0 0;">
            Generado automáticamente por Job Radar.
          </p>
        </div>
      </body>
    </html>
    """.format(
        count=count,
        match_label=match_label,
        active_label=active_label,
        summary=escape(summary),
        cards="".join(cards),
    )

    text = "\n".join([
        "JOB RADAR",
        f"{count} {match_label} {active_label}",
        summary,
        "",
        *text_items,
        "Generado automáticamente por Job Radar.",
    ])

    return subject, html, text


def send_match_notification(partial=False):
    api_key = os.getenv("RESEND_API_KEY", "").strip()

    if not api_key:
        LOGGER.info("notification_skipped reason=missing_resend_api_key")
        return None

    recipient = os.getenv("NOTIFICATION_EMAIL", "").strip()

    if not recipient:
        raise ValueError("Falta NOTIFICATION_EMAIL")

    matches = get_active_matches()
    subject, html, text = build_match_email(matches, partial=partial)

    payload = json.dumps({
        "from": (
            os.getenv("NOTIFICATION_FROM", "").strip()
            or DEFAULT_NOTIFICATION_FROM
        ),
        "to": [recipient],
        "subject": subject,
        "html": html,
        "text": text,
    }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Job-Radar/1.0",
    }

    run_id = os.getenv("NOTIFICATION_RUN_ID", "").strip()

    if run_id:
        headers["Idempotency-Key"] = f"job-radar/{run_id}"[:256]

    request = Request(
        RESEND_EMAILS_URL,
        data=payload,
        headers=headers,
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        result = json.load(response)

    email_id = result.get("id")

    if not email_id:
        raise ValueError("Resend no devolvió el identificador del correo")

    LOGGER.info(
        "notification_sent matches=%s email_id=%s",
        len(matches),
        email_id,
    )

    return email_id

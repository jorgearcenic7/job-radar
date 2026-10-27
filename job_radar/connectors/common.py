import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_radar.domain import Job
from job_radar.matching import extract_experience, extract_salary, normalize


LOGGER = logging.getLogger(__name__)

MAX_HTTP_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 10.0
MAX_JITTER_SECONDS = 0.25
DEFAULT_HTTP_TIMEOUT_SECONDS = 40
JSON_TIMEOUT_SECONDS = 30
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
# El mayor payload real (Greenhouse con content=true) ronda pocos MB; el
# límite solo evita que una respuesta anómala agote la memoria del runner.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024

BROWSER_USER_AGENT = "Mozilla/5.0 Job-Radar/1.0"
ENRICHMENT_WORKERS = 8
# Fallos de red, HTTP o de payload que afectan a una sola petición.
RECOVERABLE_FETCH_ERRORS = (OSError, HTTPException, ValueError)

RELEVANT_TITLE_PATTERNS = (
    r"\bdata (?:engineer|engineering|platform|infrastructure"
    r"|warehouse|scientist|analyst)\b",
    r"\b(?:engineer|engineering|platform).*?\bdata\b",
    r"\banalytics engineer\b",
    r"\bbusiness intelligence (?:engineer|developer|analyst)\b",
    r"\b(?:etl|elt)(?: engineer| developer)?\b",
    r"\b(?:machine learning|mlops|artificial intelligence)\b",
    r"\b(?:software|platform|backend) engineer\b",
)


class ResponseTooLargeError(ValueError):
    pass


class IncompleteSnapshot(list):
    """Jobs de un listado leído entero cuyo detalle falló para algunos IDs.

    Se comporta como ``list[Job]``. ``failed_ids`` siguen publicados en la
    fuente: cuentan como vistos y el runner no los cierra.
    """

    def __init__(self, jobs, *, failed_ids):
        super().__init__(jobs)
        self.failed_ids = frozenset(failed_ids)

    def __eq__(self, other):
        if not isinstance(other, IncompleteSnapshot):
            return NotImplemented
        return list.__eq__(self, other) and self.failed_ids == other.failed_ids

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return NotImplemented
        return not result


def as_jobs(values):
    return [
        value if isinstance(value, Job) else Job.from_mapping(value)
        for value in values
    ]


def source_context(company, source):
    """Contexto ``company=... source=...`` de los logs de reintento HTTP."""
    return f"company={company!r} source={source}"


def text_fields(description, *, salary_text=None, experience_fallback=None):
    """Campos de texto de un job derivados de su descripción.

    El salario estructurado del ATS prevalece sobre el extraído del texto;
    la experiencia estructurada solo se usa si el texto no la menciona.
    """
    return {
        "description": description,
        "salary_text": salary_text or extract_salary(description),
        "experience_text": (
            extract_experience(description) or experience_fallback
        ),
    }


def join_unique(values, separator="; "):
    """Une los valores no vacíos sin repetir, o ``None`` si no queda nada."""
    unique = dict.fromkeys(value for value in values if value)
    return separator.join(unique) or None


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(html):
    parser = TextExtractor()
    parser.feed(unescape(html or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def is_relevant_title(title):
    """Filtro de títulos que justifican descargar el detalle de una oferta."""
    title = normalize(title)
    return any(
        re.search(pattern, title) for pattern in RELEVANT_TITLE_PATTERNS
    )


def _error_status(error: Exception) -> int | None:
    return error.code if isinstance(error, HTTPError) else None


def _retry_after_seconds(error: Exception) -> float | None:
    headers = getattr(error, "headers", None)
    value = headers.get("Retry-After") if headers else None

    if not value:
        return None

    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(value)

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        return max(
            0.0,
            (retry_at - datetime.now(timezone.utc)).total_seconds(),
        )
    except (IndexError, TypeError, ValueError, OverflowError):
        return None


def _is_transient_error(error: Exception, extra_transient_error=None) -> bool:
    status = _error_status(error)

    if status is not None:
        return status in TRANSIENT_HTTP_STATUS_CODES

    if isinstance(error, (TimeoutError, URLError, ConnectionError)):
        return True

    return bool(extra_transient_error and extra_transient_error(error))


def request_with_retry(
    operation,
    *,
    context: str = "http",
    extra_transient_error=None,
):
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as error:
            if (
                attempt >= MAX_HTTP_ATTEMPTS
                or not _is_transient_error(error, extra_transient_error)
            ):
                raise

            exponential_delay = min(
                MAX_RETRY_DELAY_SECONDS,
                BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)),
            )
            retry_after = _retry_after_seconds(error)
            base_delay = max(
                exponential_delay,
                retry_after if retry_after is not None else 0.0,
            )
            delay = min(
                MAX_RETRY_DELAY_SECONDS,
                base_delay + random.uniform(0.0, MAX_JITTER_SECONDS),
            )

            if isinstance(error, HTTPError):
                error.close()

            LOGGER.warning(
                "http_retry context=%r attempt=%s max_attempts=%s "
                "status=%s error_type=%s delay_seconds=%.3f",
                context,
                attempt,
                MAX_HTTP_ATTEMPTS,
                _error_status(error),
                type(error).__name__,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError("unreachable")


def _read_bounded(open_request, request, timeout):
    with open_request(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)

    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseTooLargeError(
            f"respuesta mayor de {MAX_RESPONSE_BYTES} bytes"
        )

    return body


def read_url(request, *, timeout: float, context: str = "urllib", opener=None):
    open_request = opener.open if opener is not None else urlopen

    return request_with_retry(
        lambda: _read_bounded(open_request, request, timeout),
        context=context,
    )


def _is_empty_json_body(error: Exception) -> bool:
    return isinstance(error, json.JSONDecodeError) and not error.doc.strip()


def read_json(request, *, timeout: float, context: str = "json"):
    """Lee y decodifica JSON reintentando solo cuerpos vacíos.

    Algunos ATS (Workday) devuelven ocasionalmente un 200 sin cuerpo; se
    trata como transitorio igual que un 503. Un JSON malformado es un
    cambio de contrato y falla sin reintentos.
    """
    return request_with_retry(
        lambda: json.loads(_read_bounded(urlopen, request, timeout)),
        context=context,
        extra_transient_error=_is_empty_json_body,
    )


def fetch_json(url, *, context: str = "json"):
    request = Request(
        url,
        headers={
            "User-Agent": "Job-Radar/1.0",
            "Accept": "application/json",
        },
    )

    return read_json(request, timeout=JSON_TIMEOUT_SECONDS, context=context)


def read_browser_json(url, *, context, payload=None, headers=None):
    """GET (o POST si hay ``payload``) de JSON con User-Agent de navegador.

    Lo usan las APIs de portal (Workday, Ant) que rechazan clientes sin
    User-Agent de navegador.
    """
    request_headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json",
    }
    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request_headers.update(headers or {})

    return read_json(
        Request(url, data=data, headers=request_headers),
        timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
        context=context,
    )


def fetch_text(url, *, context: str = "text"):
    request = Request(
        url,
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json",
        },
    )

    body = read_url(
        request,
        timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
        context=context,
    )
    return body.decode("utf-8", errors="ignore")


def fetch_offset_pages(fetch_page, *, items_key, total_key):
    """Recorre un listado paginado por offset y devuelve todos sus items.

    ``fetch_page(offset)`` devuelve el dict de la página. Se para con una
    página vacía o al alcanzar ``total_key``; si la página no informa del
    total, se asume que era la última.
    """
    items = []

    while True:
        data = fetch_page(len(items))
        page = data.get(items_key) or []

        if not page:
            return items

        items.extend(page)

        if len(items) >= data.get(total_key, len(items)):
            return items


def enrich_jobs(targets, fetch_detail, *, company, source, recoverable):
    """Descarga en paralelo el detalle de las ofertas candidatas.

    ``fetch_detail(target)`` devuelve ``(job, valores)`` y los valores se
    aplican sobre el dict del job. Un error de tipo ``recoverable`` deja el
    job con los datos del listado y se registra; cualquier otro se propaga.
    """
    LOGGER.info(
        "enrichment_started company=%r source=%s candidates=%s",
        company,
        source,
        len(targets),
    )
    enriched = 0
    failures = 0

    with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as executor:
        futures = [executor.submit(fetch_detail, target) for target in targets]

        for future in as_completed(futures):
            try:
                job, values = future.result()
            except recoverable as error:
                failures += 1
                LOGGER.warning(
                    "enrichment_detail_failed company=%r source=%s "
                    "error_type=%s error_message=%r",
                    company,
                    source,
                    type(error).__name__,
                    str(error),
                )
                continue

            job.update(values)
            enriched += 1

    LOGGER.info(
        "enrichment_finished company=%r source=%s enriched=%s failures=%s",
        company,
        source,
        enriched,
        failures,
    )

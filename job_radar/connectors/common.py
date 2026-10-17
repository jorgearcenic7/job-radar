import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_radar.domain import Job
from job_radar.matching import normalize


LOGGER = logging.getLogger(__name__)

MAX_HTTP_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 10.0
MAX_JITTER_SECONDS = 0.25
DEFAULT_HTTP_TIMEOUT_SECONDS = 40
JSON_TIMEOUT_SECONDS = 30
TEXT_TIMEOUT_SECONDS = DEFAULT_HTTP_TIMEOUT_SECONDS
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
# El mayor payload real (Greenhouse con content=true) ronda pocos MB; el
# límite solo evita que una respuesta anómala agote la memoria del runner.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024


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


def _error_status(error: Exception) -> int | None:
    if isinstance(error, HTTPError):
        return error.code

    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)

    if isinstance(status, int) and status >= 100:
        return status

    return None


def _retry_after_seconds(error: Exception) -> float | None:
    headers = getattr(error, "headers", None)

    if headers is None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)

    if not headers:
        return None

    value = headers.get("Retry-After")

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


def _is_transient_error(
    error: Exception,
    extra_transient_error=None,
) -> bool:
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


def read_url(
    request,
    *,
    timeout: float,
    context: str = "urllib",
    opener=None,
):
    open_request = opener.open if opener is not None else urlopen

    return request_with_retry(
        lambda: _read_bounded(open_request, request, timeout),
        context=context,
    )


def _read_bounded(open_request, request, timeout):
    with open_request(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)

    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseTooLargeError(
            f"respuesta mayor de {MAX_RESPONSE_BYTES} bytes"
        )

    return body


def _is_empty_json_body(error: Exception) -> bool:
    return isinstance(error, json.JSONDecodeError) and not error.doc.strip()


def read_json(
    request,
    *,
    timeout: float,
    context: str = "json",
):
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

    return read_json(
        request,
        timeout=JSON_TIMEOUT_SECONDS,
        context=context,
    )


def fetch_text(url, *, context: str = "text"):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
            "Accept": "text/html,application/xhtml+xml,application/json",
        },
    )

    body = read_url(
        request,
        timeout=TEXT_TIMEOUT_SECONDS,
        context=context,
    )
    return body.decode("utf-8", errors="ignore")


def workday_relevant_title(title):
    title = normalize(title)

    patterns = (
        r"\bdata (?:engineer|engineering|platform|infrastructure"
        r"|warehouse|scientist|analyst)\b",
        r"\b(?:engineer|engineering|platform).*?\bdata\b",
        r"\banalytics engineer\b",
        r"\bbusiness intelligence "
        r"(?:engineer|developer|analyst)\b",
        r"\b(?:etl|elt)(?: engineer| developer)?\b",
        r"\b(?:machine learning|mlops|artificial intelligence)\b",
        r"\b(?:software|platform|backend) engineer\b",
    )

    return any(re.search(pattern, title) for pattern in patterns)

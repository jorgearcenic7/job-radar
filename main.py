import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


COMPANIES = {
    "Typeform": "typeform",
    "N26": "n26",
    "Datadog": "datadog",
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def normalize(text):
    return re.sub(r"\s+", " ", text.casefold()).strip()


def plain_text(html):
    parser = TextExtractor()
    parser.feed(unescape(html))
    return normalize(" ".join(parser.parts))


def classify(job):
    title = normalize(job["title"])
    description = plain_text(job.get("content") or "")

    # El nivel se evalúa en el título: la descripción puede mencionar
    # a un responsable senior sin que ese sea el nivel de la vacante.
    senior = re.search(
        r"\b(senior|sr|staff|principal|lead|head|director|manager"
        r"|architect|gerente|jefe)\b",
        title,
    )
    advanced_grade = re.search(r"\b(iii|iv|[3-9])\b", title)

    if senior or advanced_grade:
        return None

    technical = re.search(
        r"\b(engineer(?:ing)?|developer|development"
        r"|ingenier[oa]|desarrollador[a]?)\b",
        title,
    )
    data_domain = re.search(
        r"\b(data|datos|analytics|etl|elt)\b", title
    )

    # Cada grupo cuenta una sola vez aunque se mencione varias veces.
    signals = [
        r"\b(?:data|etl|elt)\s+pipelines?\b|\bpipelines?\s+de\s+datos\b",
        r"\b(?:etl|elt)\b|extract.{0,30}transform.{0,30}load",
        r"\bdata ingestion\b|\bingesta de datos\b",
        r"\bdata warehouse\b|\bdata lake\b|\blakehouse\b",
        r"\bdata model(?:ing|ling)\b|\bmodelado de datos\b",
        r"\b(?:batch|stream) processing\b|\bprocesamiento por lotes\b",
    ]
    evidence_count = sum(
        bool(re.search(pattern, description))
        for pattern in signals
    )

    if technical and data_domain:
        reason = "Título relacionado con ingeniería y datos"
    elif technical and evidence_count >= 2:
        reason = (
            f"Título técnico y {evidence_count} señales "
            "de ingeniería de datos en la descripción"
        )
    else:
        return None

    explicit_level = re.search(
        r"\b(junior|jr|mid|intermediate|associate|entry"
        r"|graduate|ii|1|2)\b|mid-level",
        title,
    )
    level = (
        "Nivel inicial/intermedio indicado en el título"
        if explicit_level
        else "Nivel por revisar"
    )

    status = (
        "Candidata"
        if technical and data_domain and explicit_level
        else "Por revisar"
    )

    return status, level, reason



def save_jobs(company, jobs):
    import psycopg

    sql = """
        INSERT INTO jobs (
            source, source_job_id, company, title, location,
            url, description, selected, match_status, match_reason
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_job_id) DO UPDATE SET
            company = EXCLUDED.company,
            title = EXCLUDED.title,
            location = EXCLUDED.location,
            url = EXCLUDED.url,
            description = EXCLUDED.description,
            selected = EXCLUDED.selected,
            match_status = EXCLUDED.match_status,
            match_reason = EXCLUDED.match_reason,
            last_seen_at = CURRENT_TIMESTAMP
    """

    with psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            for job in jobs:
                result = classify(job)
                status, level, reason = result or (None, None, None)

                cursor.execute(sql, (
                    "greenhouse",
                    str(job["id"]),
                    company,
                    job["title"],
                    (job.get("location") or {}).get("name"),
                    job["absolute_url"],
                    plain_text(job.get("content") or ""),
                    result is not None,
                    status,
                    f"{level}: {reason}" if result else None,
                ))

    print(f"  Ofertas guardadas o actualizadas: {len(jobs)}")

def main():
    failed = False

    for company, board in COMPANIES.items():
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/"
            f"{board}/jobs?content=true"
        )

        try:
            with urlopen(url, timeout=30) as response:
                data = json.load(response)

            jobs = data["jobs"]
            save_jobs(company, jobs)
            print(f"\n{company}: {len(jobs)} ofertas consultadas")
            selected = 0

            for job in jobs:
                result = classify(job)
                if result is None:
                    continue

                selected += 1
                status, level, reason = result
                location = (job.get("location") or {}).get(
                    "name", "No indicada"
                )

                print(f"\n  [{status}] {job['title']}")
                print(f"  Nivel: {level}")
                print(f"  Motivo: {reason}")
                print(f"  Ubicación: {location}")
                print(f"  Enlace: {job['absolute_url']}")

            print(f"\n  Ofertas seleccionadas: {selected}")

        except (HTTPError, URLError, TimeoutError) as error:
            failed = True
            print(f"\n{company}: error de conexión: {error}")

        except (KeyError, ValueError, TypeError) as error:
            failed = True
            print(f"\n{company}: respuesta inesperada: {error}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

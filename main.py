import json
import os
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed


GREENHOUSE_COMPANIES = {
    "Typeform": "typeform",
    "N26": "n26",
    "Datadog": "datadog",
    "Clarity AI": "clarityai",
    "Fever": "feverup",
    "Cabify": "cabify",
    "Aircall": "aircallioinc",
    "Auctane": "auctane",
    "Celonis": "celonis",
    "Taxbit": "taxbit",
    "Ebury": "ebury",
    "Lynx": "lynxtech",
    "Monzo": "monzo",
}

ASHBY_COMPANIES = {
    "Pleo": "pleo",
    "Capchase": "capchase",
    "Invopop": "invopop",
    "Airwallex": "airwallex",
    "Checkout.com": "checkout.com",
    "Rain": "rain",
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def plain_text(html):
    parser = TextExtractor()
    parser.feed(unescape(html or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def fetch_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Job-Radar/1.0",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_text(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
            "Accept": "text/html,application/xhtml+xml,application/json",
        },
    )

    with urlopen(request, timeout=40) as response:
        return response.read().decode(
            "utf-8",
            errors="ignore",
        )


def extract_salary(description):
    patterns = [
        r"(?:€|\$|£)\s?\d{2,3}(?:[.,]\d{3})*(?:\s?[kK])?"
        r"\s*(?:-|–|—|to)\s*"
        r"(?:€|\$|£)?\s?\d{2,3}(?:[.,]\d{3})*(?:\s?[kK])?"
        r"(?:\s*(?:EUR|USD|GBP))?",

        r"\b\d{2,3}(?:[.,]\d{3})+\s*(?:EUR|USD|GBP)"
        r"\s*(?:-|–|—|to)\s*"
        r"\d{2,3}(?:[.,]\d{3})+\s*(?:EUR|USD|GBP)?\b",

        r"\b\d{2,3}\s?[kK]\s*(?:-|–|—|to)\s*"
        r"\d{2,3}\s?[kK]\s*(?:EUR|USD|GBP)\b",

        r"(?:€|\$|£)\s?\d{2,3}(?:[.,]\d{3})+"
        r"(?:\s*(?:EUR|USD|GBP))?"
        r"(?:\s*(?:per year|annually|a year|/year))?",
    ]

    for pattern in patterns:
        match = re.search(pattern, description or "", re.IGNORECASE)

        if match:
            return match.group(0).strip()

    return None


def extract_experience(description):
    patterns = [
        r"\b(?:at least|minimum(?: of)?|min\.?)?\s*"
        r"\d{1,2}\+?\s+years?\s+(?:of\s+)?"
        r"(?:relevant\s+|professional\s+|hands-on\s+|industry\s+)?"
        r"experience\b",

        r"\b\d{1,2}\s*(?:-|–|—|to)\s*\d{1,2}\s+years?"
        r"\s+(?:of\s+)?(?:relevant\s+|professional\s+)?experience\b",

        r"\b\d{1,2}\+?\s+years['’]?\s+experience\b",

        r"\b(?:mínimo|minimo|al menos)?\s*"
        r"\d{1,2}\+?\s+años?\s+de\s+experiencia\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, description or "", re.IGNORECASE)

        if match:
            return match.group(0).strip()

    return None


def required_experience_years(experience_text):
    """
    Extrae el mínimo de años exigidos.

    Ejemplos:
      1+ years of experience -> 1
      At least 3 years of experience -> 3
      3-5 years of experience -> 3
      Minimum of 4 years -> 4
      7+ years of experience -> 7
    """

    if not experience_text:
        return None

    value = normalize(experience_text)

    patterns = [
        # at least 3 years / minimum of 3 years
        r"\b(?:at least|minimum(?: of)?|min\.?)\s*"
        r"(\d{1,2})\+?\s*(?:years?|yrs?)\b",

        # 3-5 years / 3 to 5 years
        r"\b(\d{1,2})\s*(?:-|–|—|to)\s*"
        r"\d{1,2}\s*(?:years?|yrs?)\b",

        # 7+ years
        r"\b(\d{1,2})\+\s*(?:years?|yrs?)\b",

        # 2 years of experience
        r"\b(\d{1,2})\s*(?:years?|yrs?)\s+"
        r"(?:of\s+)?"
        r"(?:relevant\s+|professional\s+|hands-on\s+|industry\s+)?"
        r"experience\b",

        # español
        r"\b(?:mínimo|minimo|al menos)?\s*"
        r"(\d{1,2})\+?\s*años?\s+de\s+experiencia\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            value,
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return None


def classify(job):
    """
    JOB RADAR - REGLAS GLOBALES DE COINCIDENCIA

    PERFIL OBJETIVO
    ----------------
    Experiencia actual aproximada: 1 año.

    Roles prioritarios:
    - Data Engineer
    - Analytics Engineer
    - Data Platform Engineer
    - Data Infrastructure Engineer
    - Data Warehouse Engineer
    - ETL / ELT Engineer
    - DataOps Engineer
    - BI Engineer

    También mostramos como STRETCH:
    - Mid / III
    - hasta 3 años requeridos
    - Data Analyst técnico
    - Product Data Analyst técnico
    - Data Scientist técnico
    - ML / MLOps / Applied AI
    - Platform / Software Engineer con fuerte componente Data

    NO queremos:
    - Internships
    - Graduate / New Grad
    - Trainee / Apprentice
    - Working Student
    - Senior
    - Staff
    - Principal
    - Lead
    - Manager
    - Director
    - Architect
    - 4+ años mínimos de experiencia
    - nivel IV / 4 o superior

    RESULTADOS
    ----------
    Buena coincidencia:
        claramente alineado y nivel compatible.

    Stretch:
        algo por encima o rol adyacente pero razonable.

    None:
        fuera de objetivo.
    """

    title = normalize(
        job.get("title") or ""
    )

    raw_description = (
        job.get("description") or ""
    )

    description = normalize(
        raw_description
    )

    # =========================================================
    # 1. DESCARTES ABSOLUTOS POR TÍTULO
    # =========================================================

    early_career = re.search(
        r"\b("
        r"intern|internship|"
        r"working student|student|"
        r"graduate|new grad|new graduate|"
        r"trainee|apprentice|apprenticeship"
        r")\b",
        title,
    )

    if early_career:
        return None

    senior = re.search(
        r"\b("
        r"senior|sr|"
        r"staff|principal|"
        r"lead|head|"
        r"manager|director|"
        r"architect|"
        r"distinguished|expert|"
        r"vice president|vp"
        r")\b",
        title,
    )

    if senior:
        return None

    # III / 3 SE PERMITEN.
    # IV / 4+ NO.
    advanced_grade = re.search(
        r"\b("
        r"iv|v|vi|vii|viii|ix|x|"
        r"[4-9]"
        r")\b",
        title,
    )

    if advanced_grade:
        return None

    # =========================================================
    # 2. EXPERIENCIA
    # =========================================================

    experience_text = (
        job.get("experience_text")
        or extract_experience(
            raw_description
        )
    )

    years = required_experience_years(
        experience_text
    )

    # Regla global:
    # 0-2 años -> objetivo
    # 3 años   -> stretch
    # 4+       -> fuera
    if years is not None and years >= 4:
        return None

    # =========================================================
    # 3. FAMILIAS DE ROLES
    # =========================================================

    core_data_role = re.search(
        r"\b("
        r"data engineer(?:ing)?|"
        r"analytics engineer(?:ing)?|"
        r"data platform engineer|"
        r"data infrastructure engineer|"
        r"data warehouse engineer|"
        r"etl engineer|"
        r"elt engineer|"
        r"dataops engineer|"
        r"bi engineer|"
        r"business intelligence engineer"
        r")\b",
        title,
    )

    data_analyst_role = re.search(
        r"\b("
        r"data analyst|"
        r"product data analyst|"
        r"analytics analyst|"
        r"business intelligence analyst"
        r")\b",
        title,
    )

    data_science_role = re.search(
        r"\b("
        r"data scientist|"
        r"machine learning engineer|"
        r"ml engineer|"
        r"mlops engineer|"
        r"applied ai engineer|"
        r"ai engineer"
        r")\b",
        title,
    )

    adjacent_engineering_role = re.search(
        r"\b("
        r"platform engineer|"
        r"software engineer|"
        r"backend engineer|"
        r"cloud engineer|"
        r"devops engineer"
        r")\b",
        title,
    )

    # =========================================================
    # 4. SEÑALES FUERTES DE DATA ENGINEERING
    # =========================================================

    strong_data_signals = [
        r"\bdata pipelines?\b"
        r"|\bpipelines?\s+de\s+datos\b",

        r"\b(?:etl|elt)\b"
        r"|extract.{0,30}transform.{0,30}load",

        r"\bdata ingestion\b"
        r"|\bingesta de datos\b",

        r"\bdata warehouse\b"
        r"|\bdata lake\b"
        r"|\blakehouse\b",

        r"\bdata model(?:ing|ling)\b"
        r"|\bmodelado de datos\b",

        r"\b(?:batch|stream) processing\b",

        r"\b(?:spark|pyspark|databricks)\b",

        r"\b(?:airflow|dbt)\b",

        r"\b(?:kafka|event streaming)\b",

        r"\b(?:snowflake|bigquery|redshift|"
        r"microsoft fabric)\b",
    ]

    strong_data_count = sum(
        bool(
            re.search(
                signal,
                description,
            )
        )
        for signal in strong_data_signals
    )

    # =========================================================
    # 5. SEÑALES TÉCNICAS GENERALES
    # =========================================================

    technical_signals = [
        r"\bsql\b",
        r"\bpython\b",
        r"\bjava\b",
        r"\bscala\b",
        r"\bazure\b",
        r"\baws\b",
        r"\bgcp\b",
        r"\bterraform\b",
        r"\bkubernetes\b",
        r"\bdocker\b",
    ]

    technical_count = sum(
        bool(
            re.search(
                signal,
                description,
            )
        )
        for signal in technical_signals
    )

    # =========================================================
    # 6. SEÑALES ML / AI
    # =========================================================

    ml_signals = [
        r"\bmachine learning\b",
        r"\bdeep learning\b",
        r"\bnlp\b",
        r"\bmlops\b",
        r"\bmodel deployment\b",
        r"\bmodel training\b",
        r"\bfeature engineering\b",
        r"\bpython\b",
        r"\bspark\b",
        r"\bdatabricks\b",
    ]

    ml_count = sum(
        bool(
            re.search(
                signal,
                description,
            )
        )
        for signal in ml_signals
    )

    # =========================================================
    # 7. ¿EL ROL ENCAJA?
    # =========================================================

    role_type = None
    reason = None

    if core_data_role:
        role_type = "core"

        reason = (
            "Rol directamente alineado "
            "con Data Engineering"
        )

    elif data_analyst_role:
        # Queremos analistas técnicos,
        # no puestos puramente business.
        if (
            strong_data_count >= 1
            or technical_count >= 2
        ):
            role_type = "analyst"

            reason = (
                "Data Analyst con contenido "
                "técnico relevante"
            )

        else:
            return None

    elif data_science_role:
        if (
            ml_count >= 2
            or strong_data_count >= 1
        ):
            role_type = "ml"

            reason = (
                "Data Science / ML con "
                "contenido técnico relevante"
            )

        else:
            return None

    elif adjacent_engineering_role:
        # Software / Platform solo interesa si
        # realmente hay mucho Data Engineering.
        if strong_data_count >= 3:
            role_type = "adjacent"

            reason = (
                f"Rol de ingeniería con "
                f"{strong_data_count} señales "
                "fuertes de Data Engineering"
            )

        else:
            return None

    else:
        return None

    # =========================================================
    # 8. NIVEL DEL PUESTO
    # =========================================================

    junior_level = re.search(
        r"\b("
        r"junior|jr|associate|entry"
        r")\b",
        title,
    )

    level_i_ii = re.search(
        r"\b("
        r"engineer\s+i|"
        r"engineer\s+ii|"
        r"level\s*1|"
        r"level\s*2"
        r")\b",
        title,
    )

    mid_level = re.search(
        r"\b("
        r"mid|mid-level|"
        r"intermediate|"
        r"engineer\s+iii|"
        r"level\s*3"
        r")\b",
        title,
    )

    grade_iii = re.search(
        r"\biii\b",
        title,
    )

    # =========================================================
    # 9. CLASIFICACIÓN FINAL
    # =========================================================

    # Roles adyacentes siempre son Stretch.
    if role_type in {
        "analyst",
        "ml",
        "adjacent",
    }:
        status = "Stretch"

        level = (
            "Rol técnico adyacente "
            "razonable para el perfil"
        )

    # 3 años / Mid / III -> Stretch.
    elif (
        years == 3
        or mid_level
        or grade_iii
    ):
        status = "Stretch"

        level = (
            "Mid / III / hasta 3 años: "
            "stretch razonable"
        )

    # Core Data + experiencia <= 2.
    elif (
        years is not None
        and years <= 2
    ):
        status = "Buena coincidencia"

        level = (
            f"Experiencia compatible: "
            f"{years} año(s)"
        )

    # Core Data explícitamente Junior / I / II.
    elif junior_level or level_i_ii:
        status = "Buena coincidencia"

        level = (
            "Nivel inicial/intermedio "
            "compatible"
        )

    # Data Engineer sin nivel ni años:
    # lo mostramos, pero no asumimos que sea junior.
    else:
        status = "Stretch"

        level = (
            "Rol muy alineado, "
            "pero nivel no especificado"
        )

    # =========================================================
    # 10. MOTIVO
    # =========================================================

    details = [reason]

    if strong_data_count:
        details.append(
            f"{strong_data_count} señales "
            "fuertes de Data Engineering"
        )

    if experience_text:
        details.append(
            "Experiencia publicada: "
            f"{experience_text}"
        )

    return (
        status,
        level,
        " | ".join(details),
    )



def fetch_greenhouse(company, board):
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{board}/jobs?content=true"
    )

    data = fetch_json(url)
    result = []

    for job in data["jobs"]:
        description = plain_text(job.get("content") or "")

        result.append({
            "source": "greenhouse",
            "source_job_id": str(job["id"]),
            "company": company,
            "title": job["title"],
            "location": (job.get("location") or {}).get("name"),
            "url": job["absolute_url"],
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return result


def ashby_location(job):
    locations = []

    primary = (job.get("location") or "").strip()

    if primary:
        locations.append(primary)

    for item in job.get("secondaryLocations") or []:
        value = (item.get("location") or "").strip()

        if value and value not in locations:
            locations.append(value)

    return "; ".join(locations) or None


def fetch_ashby(company, board):
    url = (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{board}?includeCompensation=true"
    )

    data = fetch_json(url)
    result = []

    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = (
            job.get("descriptionPlain")
            or plain_text(job.get("descriptionHtml") or "")
        )

        compensation = job.get("compensation") or {}

        salary = (
            compensation.get("scrapeableCompensationSalarySummary")
            or extract_salary(description)
        )

        job_url = job.get("jobUrl") or job.get("applyUrl")

        if not job_url:
            continue

        source_job_id = job_url.rstrip("/").split("/")[-1]

        result.append({
            "source": "ashby",
            "source_job_id": source_job_id,
            "company": company,
            "title": job["title"],
            "location": ashby_location(job),
            "url": job_url,
            "description": description,
            "salary_text": salary,
            "experience_text": extract_experience(description),
        })

    return result


def schema_location(jobposting):
    locations = []

    raw_locations = jobposting.get("jobLocation") or []

    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]

    for item in raw_locations:
        if not isinstance(item, dict):
            continue

        address = item.get("address") or {}

        if not isinstance(address, dict):
            continue

        parts = []

        for key in (
            "addressLocality",
            "addressRegion",
            "addressCountry",
        ):
            value = address.get(key)

            if isinstance(value, dict):
                value = value.get("name")

            if value and str(value) not in parts:
                parts.append(str(value))

        if parts:
            value = ", ".join(parts)

            if value not in locations:
                locations.append(value)

    if jobposting.get("jobLocationType") == "TELECOMMUTE":
        remote = "Remote"

        requirements = (
            jobposting.get("applicantLocationRequirements") or []
        )

        if isinstance(requirements, dict):
            requirements = [requirements]

        required_places = []

        for item in requirements:
            if not isinstance(item, dict):
                continue

            name = item.get("name")

            if name:
                required_places.append(str(name))

        if required_places:
            remote += " - " + ", ".join(required_places)

        if remote not in locations:
            locations.append(remote)

    return "; ".join(locations) or None


def schema_salary(jobposting):
    base = jobposting.get("baseSalary")

    if not isinstance(base, dict):
        return None

    currency = base.get("currency") or ""
    value = base.get("value") or {}

    if not isinstance(value, dict):
        return None

    minimum = value.get("minValue")
    maximum = value.get("maxValue")
    exact = value.get("value")
    unit = value.get("unitText")

    if minimum is not None and maximum is not None:
        result = f"{minimum} – {maximum}"

    elif exact is not None:
        result = str(exact)

    else:
        return None

    if currency:
        result += f" {currency}"

    if unit:
        result += f" / {str(unit).lower()}"

    return result


def extract_jobposting_jsonld(page):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)</script>',
        page,
        re.IGNORECASE | re.DOTALL,
    )

    for script in scripts:
        try:
            data = json.loads(unescape(script).strip())
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]

        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and candidate.get("@type") == "JobPosting"
            ):
                return candidate

    return None


def fetch_spendesk():
    data = fetch_json("https://career.spendesk.com/jobs.json")

    result = []

    for item in data.get("items", []):
        schema = item.get("_jobposting") or {}

        description = plain_text(
            item.get("content_html")
            or schema.get("description")
            or ""
        )

        result.append({
            "source": "teamtailor:spendesk",
            "source_job_id": str(item["id"]),
            "company": "Spendesk",
            "title": item["title"],
            "location": schema_location(schema),
            "url": item["url"],
            "description": description,
            "salary_text": (
                schema_salary(schema)
                or extract_salary(description)
            ),
            "experience_text": extract_experience(description),
        })

    return result


def fetch_thetaray():
    url = (
        "https://www.comeet.co/careers-api/2.0/company/"
        "72.00F/positions"
        "?token=27F9FC16779FC13F8EFA13F84FE27F77D"
        "&details=true"
    )

    data = fetch_json(url)

    result = []

    for item in data:
        if item.get("is_internal"):
            continue

        html_parts = []

        for detail in item.get("details") or []:
            value = detail.get("value")

            if value:
                html_parts.append(value)

        description = plain_text(" ".join(html_parts))

        location_data = item.get("location") or {}

        location = (
            location_data.get("name")
            if isinstance(location_data, dict)
            else None
        )

        url_value = (
            item.get("url_active_page")
            or item.get("url_comeet_hosted_page")
            or item.get("url_recruit_hosted_page")
        )

        if not url_value:
            continue

        result.append({
            "source": "comeet:thetaray",
            "source_job_id": str(item["uid"]),
            "company": "ThetaRay",
            "title": item["name"],
            "location": location,
            "url": url_value,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return result


def fetch_deel_job(job_id):
    url = (
        "https://jobs.deel.com/deel/job-details/"
        f"{job_id}/overview"
    )

    page = fetch_text(url)
    schema = extract_jobposting_jsonld(page)

    if not schema:
        return None

    description = plain_text(schema.get("description") or "")

    return {
        "source": "deel:careers",
        "source_job_id": job_id,
        "company": "Deel",
        "title": schema.get("title") or "Untitled",
        "location": schema_location(schema),
        "url": url,
        "description": description,
        "salary_text": (
            schema_salary(schema)
            or extract_salary(description)
        ),
        "experience_text": extract_experience(description),
    }


def fetch_deel():
    page = fetch_text("https://www.deel.com/careers/")

    ids = sorted(set(
        re.findall(
            r"job-details/([0-9a-fA-F-]{36})",
            page,
        )
    ))

    if not ids:
        raise ValueError("Deel: no job IDs found")

    result = []
    failures = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_deel_job, job_id): job_id
            for job_id in ids
        }

        for future in as_completed(futures):
            try:
                job = future.result()

                if job:
                    result.append(job)

            except Exception:
                failures += 1

    if not result:
        raise ValueError(
            f"Deel: no jobs parsed ({failures} failures)"
        )

    if failures:
        print(
            f"Deel: aviso, {failures} páginas "
            "individuales no pudieron leerse"
        )

    return result


def fetch_mambu_job(job_id):
    url = (
        "https://careers-mambu.icims.com/jobs/"
        f"{job_id}/job?in_iframe=1"
    )

    page = fetch_text(url)

    title_match = re.search(
        r"<h1[^>]*>(.*?)</h1>",
        page,
        re.IGNORECASE | re.DOTALL,
    )

    if not title_match:
        return None

    title = plain_text(title_match.group(1))
    description = plain_text(page)

    location_match = re.search(
        r"Job Locations?\s+(.+?)\s+"
        r"(?:Posted Date|Job ID)",
        description,
        re.IGNORECASE,
    )

    location = (
        location_match.group(1).strip()
        if location_match
        else None
    )

    return {
        "source": "icims:mambu",
        "source_job_id": str(job_id),
        "company": "Mambu",
        "title": title,
        "location": location,
        "url": url,
        "description": description,
        "salary_text": extract_salary(description),
        "experience_text": extract_experience(description),
    }


def fetch_mambu():
    listing_url = (
        "https://careers-mambu.icims.com/jobs/search"
        "?ss=1&searchRelation=keyword_all&in_iframe=1"
    )

    page = fetch_text(listing_url)

    ids = sorted(set(
        re.findall(
            r"/jobs/(\d+)/[^\"'<>\s]+",
            page,
        )
    ))

    if not ids:
        raise ValueError("Mambu: no job IDs found")

    result = []

    for job_id in ids:
        job = fetch_mambu_job(job_id)

        if job:
            result.append(job)

    if not result:
        raise ValueError("Mambu: jobs found but none parsed")

    return result


def fetch_teamtailor_company(
    company,
    base_url,
    source,
):
    data = fetch_json(
        base_url.rstrip("/") + "/jobs.json"
    )

    result = []

    for item in data.get("items", []):
        schema = item.get("_jobposting") or {}

        description = plain_text(
            item.get("content_html")
            or schema.get("description")
            or ""
        )

        result.append({
            "source": source,
            "source_job_id": str(item["id"]),
            "company": company,
            "title": item["title"],
            "location": schema_location(schema),
            "url": item["url"],
            "description": description,
            "salary_text": (
                schema_salary(schema)
                or extract_salary(description)
            ),
            "experience_text": (
                extract_experience(description)
            ),
        })

    return result


def fetch_seedtag():
    return fetch_teamtailor_company(
        company="Seedtag",
        base_url="https://jobs.seedtag.com",
        source="teamtailor:seedtag",
    )


def fetch_lingokids():
    return fetch_teamtailor_company(
        company="Lingokids",
        base_url="https://jobs.lingokids.com",
        source="teamtailor:lingokids",
    )



def revolut_slug(title):
    value = unicodedata.normalize(
        "NFKD",
        title,
    ).encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    value = value.lower()

    return re.sub(
        r"[^a-z0-9]+",
        "-",
        value,
    ).strip("-")


def revolut_locations(position):
    locations = []

    for item in position.get("locations") or []:
        name = (item.get("name") or "").strip()

        if name and name not in locations:
            locations.append(name)

    return "; ".join(locations) or None


def fetch_revolut():
    from curl_cffi import requests as curl_requests

    careers_url = "https://www.revolut.com/en-US/careers/"

    response = curl_requests.get(
        careers_url,
        impersonate="chrome",
        headers={
            "Accept-Language": "en-GB,en;q=0.9",
        },
        timeout=40,
    )

    response.raise_for_status()

    next_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>'
        r'(.*?)</script>',
        response.text,
        re.IGNORECASE | re.DOTALL,
    )

    if not next_match:
        raise ValueError(
            "Revolut: __NEXT_DATA__ no encontrado"
        )

    next_data = json.loads(
        next_match.group(1)
    )

    build_id = next_data.get("buildId")
    locale = next_data.get("locale") or "en-GB"

    positions = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("positions", [])
    )

    if not build_id:
        raise ValueError(
            "Revolut: buildId no encontrado"
        )

    if not positions:
        raise ValueError(
            "Revolut: listado de ofertas vacío"
        )

    print(
        f"Revolut: {len(positions)} ofertas "
        "encontradas"
    )

    # Solo descargamos descripción completa para títulos
    # que podrían interesarnos.
    interesting_title = re.compile(
        r"\b("
        r"data|analytics|"
        r"engineer|engineering|"
        r"developer|development|"
        r"platform|infrastructure|"
        r"etl|elt|warehouse|"
        r"bi"
        r")\b",
        re.IGNORECASE,
    )

    candidates = [
        position
        for position in positions
        if interesting_title.search(
            position.get("text") or ""
        )
    ]

    print(
        f"Revolut: {len(candidates)} ofertas "
        "potencialmente técnicas para enriquecer"
    )

    result = []
    enriched = 0
    failures = 0

    candidate_ids = {
        str(position.get("id"))
        for position in candidates
    }

    for position in positions:
        job_id = str(
            position.get("id") or ""
        ).strip()

        title = (
            position.get("text") or ""
        ).strip()

        if not job_id or not title:
            continue

        slug = revolut_slug(title)

        identifier = (
            f"{slug}-{job_id}"
        )

        public_url = (
            "https://www.revolut.com/"
            "careers/position/"
            f"{identifier}/"
        )

        description = ""

        # ====================================================
        # DESCARGAR DETALLE SOLO SI EL TÍTULO PUEDE INTERESAR
        # ====================================================

        if job_id in candidate_ids:

            detail_url = (
                "https://www.revolut.com/"
                f"_next/data/{build_id}/"
                f"{locale}/careers/position/"
                f"{identifier}.json"
                f"?id={identifier}"
            )

            try:
                detail_response = curl_requests.get(
                    detail_url,
                    impersonate="chrome",
                    headers={
                        "Accept-Language":
                            "en-GB,en;q=0.9",
                        "Referer":
                            careers_url,
                        "X-Nextjs-Data":
                            "1",
                    },
                    timeout=30,
                )

                detail_response.raise_for_status()

                detail_json = (
                    detail_response.json()
                )

                detail_position = (
                    detail_json
                    .get("pageProps", {})
                    .get("position", {})
                )

                description = plain_text(
                    detail_position.get(
                        "description"
                    )
                    or ""
                )

                # Si el detalle tiene localizaciones,
                # usamos esas.
                if detail_position.get(
                    "locations"
                ):
                    position = {
                        **position,
                        "locations":
                            detail_position[
                                "locations"
                            ],
                    }

                enriched += 1

            except Exception as error:
                failures += 1

                print(
                    "Revolut detalle ERROR:",
                    title,
                    "-",
                    type(error).__name__,
                )

        result.append({
            "source":
                "revolut:careers",

            "source_job_id":
                job_id,

            "company":
                "Revolut",

            "title":
                title,

            "location":
                revolut_locations(position),

            "url":
                public_url,

            "description":
                description,

            "salary_text":
                extract_salary(description),

            "experience_text":
                extract_experience(
                    description
                ),
        })

    print(
        f"Revolut: {enriched} ofertas "
        "enriquecidas correctamente"
    )

    if failures:
        print(
            f"Revolut: {failures} detalles "
            "no pudieron descargarse"
        )

    return result


# ============================================================
# COUNTRY NORMALIZATION
# ============================================================

_COUNTRY_ALIASES = None
_CITY_COUNTRIES = None


def _geo_normalize(value):
    import unicodedata

    value = unicodedata.normalize(
        "NFKD",
        value or "",
    ).encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    value = value.casefold()

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def _prepare_geography():
    global _COUNTRY_ALIASES
    global _CITY_COUNTRIES

    if _COUNTRY_ALIASES is not None:
        return

    import geonamescache

    gc = geonamescache.GeonamesCache()

    countries = gc.get_countries()
    cities = gc.get_cities()

    aliases = {}
    code_to_name = {}

    for code, data in countries.items():
        name = data.get("name")

        if not name:
            continue

        code_to_name[code.upper()] = name

        aliases[_geo_normalize(name)] = name
        aliases[code.casefold()] = name

        iso3 = data.get("iso3")

        if iso3:
            aliases[iso3.casefold()] = name

    aliases.update({
        "uk": "United Kingdom",
        "usa": "United States",
        "uae": "United Arab Emirates",
        "czech republic": "Czechia",
    })

    city_candidates = {}

    for city in cities.values():
        country_code = (
            city.get("countrycode") or ""
        ).upper()

        country = code_to_name.get(
            country_code
        )

        if not country:
            continue

        population = city.get("population") or 0

        for city_name in {
            city.get("name"),
            city.get("ascii"),
        }:
            if not city_name:
                continue

            key = _geo_normalize(city_name)

            previous = city_candidates.get(key)

            if (
                previous is None
                or population > previous[0]
            ):
                city_candidates[key] = (
                    population,
                    country,
                )

    _COUNTRY_ALIASES = aliases

    _CITY_COUNTRIES = {
        key: value[1]
        for key, value
        in city_candidates.items()
    }


def infer_countries(location):
    """
    Convierte la ubicación original en uno o varios países.

    Prioridad:
    1. País escrito explícitamente.
    2. Código ISO explícito.
    3. Ciudad, solo si no conocemos ya el país.

    La ubicación original no se modifica.
    """

    if not location:
        return []

    _prepare_geography()

    result = []

    def add(country):
        if country and country not in result:
            result.append(country)

    segments = re.split(
        r"[;|]",
        str(location),
    )

    for raw_segment in segments:
        segment = raw_segment.strip()

        if not segment:
            continue

        normalized = _geo_normalize(segment)

        explicit_countries = []

        def add_explicit(country):
            if country and country not in explicit_countries:
                explicit_countries.append(country)

        # 1. País completo escrito en el texto
        for alias, country in _COUNTRY_ALIASES.items():

            if len(alias) <= 3:
                continue

            if re.search(
                r"(?<![a-z])"
                + re.escape(alias)
                + r"(?![a-z])",
                normalized,
            ):
                add_explicit(country)

        # 2. Código ISO independiente:
        # Madrid, ES
        # Berlin, DE
        # Porto, PT
        pieces = [
            _geo_normalize(piece)
            for piece in re.split(
                r"[,/()]",
                segment,
            )
            if piece.strip()
        ]

        for piece in pieces:
            if (
                len(piece) <= 3
                and piece in _COUNTRY_ALIASES
            ):
                add_explicit(
                    _COUNTRY_ALIASES[piece]
                )

        # Si ya sabemos el país por texto o código,
        # NO intentamos reinterpretar la ciudad.
        if explicit_countries:
            for country in explicit_countries:
                add(country)

            continue

        # 3. Inferencia por ciudad
        cleaned = re.sub(
            r"\b("
            r"remote|office|hybrid|"
            r"onsite|on-site|based"
            r")\b",
            " ",
            normalized,
        )

        cleaned = re.sub(
            r"\s+",
            " ",
            cleaned,
        ).strip(" ,-")

        candidates = [cleaned]

        candidates.extend(
            part.strip()
            for part in re.split(
                r"[,/()]",
                cleaned,
            )
            if part.strip()
        )

        for candidate in candidates:
            country = _CITY_COUNTRIES.get(candidate)

            if country:
                add(country)
                break

    return result


def save_jobs(jobs):
    import psycopg

    sql = """
        INSERT INTO jobs (
            source,
            source_job_id,
            company,
            title,
            location,
            countries,
            url,
            description,
            salary_text,
            experience_text,
            selected,
            match_status,
            match_reason
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (source, source_job_id) DO UPDATE SET
            company = EXCLUDED.company,
            title = EXCLUDED.title,
            location = EXCLUDED.location,
            countries = EXCLUDED.countries,
            url = EXCLUDED.url,
            description = EXCLUDED.description,
            salary_text = EXCLUDED.salary_text,
            experience_text = EXCLUDED.experience_text,
            selected = EXCLUDED.selected,
            match_status = EXCLUDED.match_status,
            match_reason = EXCLUDED.match_reason,
            last_seen_at = CURRENT_TIMESTAMP
    """

    with psycopg.connect(
        os.getenv("DATABASE_URL", ""),
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cursor:
            for job in jobs:
                result = classify(job)
                status, level, reason = result or (None, None, None)

                cursor.execute(
                    sql,
                    (
                        job["source"],
                        job["source_job_id"],
                        job["company"],
                        job["title"],
                        job["location"],
                        infer_countries(job.get("location")),
                        job["url"],
                        job["description"],
                        job["salary_text"],
                        job["experience_text"],
                        result is not None,
                        status,
                        f"{level}: {reason}" if result else None,
                    ),
                )


def process_company(company, provider, board):
    if provider == "greenhouse":
        jobs = fetch_greenhouse(company, board)

    elif provider == "ashby":
        jobs = fetch_ashby(company, board)

    else:
        raise ValueError(f"Proveedor no soportado: {provider}")

    save_jobs(jobs)

    selected = sum(classify(job) is not None for job in jobs)

    print(
        f"{company}: {len(jobs)} ofertas consultadas "
        f"({selected} coincidencias)"
    )


def main():
    failed = False

    sources = []

    sources.extend(
        (company, "greenhouse", board)
        for company, board in GREENHOUSE_COMPANIES.items()
    )

    sources.extend(
        (company, "ashby", board)
        for company, board in ASHBY_COMPANIES.items()
    )

    for company, provider, board in sources:
        try:
            process_company(company, provider, board)

        except (
            HTTPError,
            URLError,
            TimeoutError,
            KeyError,
            ValueError,
            TypeError,
        ) as error:
            failed = True
            print(
                f"{company}: ERROR ({provider}): "
                f"{type(error).__name__}: {error}"
            )

    extra_sources = [
        ("Spendesk", fetch_spendesk),
        ("ThetaRay", fetch_thetaray),
        ("Deel", fetch_deel),
        ("Mambu", fetch_mambu),
        ("Seedtag", fetch_seedtag),
        ("Lingokids", fetch_lingokids),
        ("Revolut", fetch_revolut),
    ]

    for company, fetcher in extra_sources:
        try:
            jobs = fetcher()
            save_jobs(jobs)

            selected = sum(
                classify(job) is not None
                for job in jobs
            )

            print(
                f"{company}: {len(jobs)} ofertas consultadas "
                f"({selected} coincidencias)"
            )

        except Exception as error:
            if company != "Revolut":
                failed = True
            print(
                f"{company}: ERROR: "
                f"{type(error).__name__}: {error}"
            )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

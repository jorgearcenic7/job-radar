import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from .common import (
    RECOVERABLE_FETCH_ERRORS,
    as_jobs,
    enrich_jobs,
    fetch_json,
    fetch_offset_pages,
    fetch_text,
    is_relevant_title,
    join_unique,
    plain_text,
    read_browser_json,
    source_context,
    text_fields,
)


def format_salary(minimum, maximum, currency=None, interval=None):
    """Rango publicado como ``min – max MONEDA / intervalo``."""
    if minimum is None and maximum is None:
        return None

    if minimum is not None and maximum is not None:
        result = f"{minimum} – {maximum}"
    else:
        result = str(minimum if minimum is not None else maximum)

    if currency:
        result += f" {currency}"

    if interval:
        result += f" / {str(interval).replace('-', ' ')}"

    return result


def _city_country(item):
    return ", ".join(
        value for value in (item.get("city"), item.get("country")) if value
    )


def _upgrade_to_https(url):
    # Algunas boards (p. ej. Block) publican absolute_url con http:// aunque
    # el mismo host sirve HTTPS; la web y el correo solo enlazan HTTPS.
    if url and url[:7].lower() == "http://":
        return "https://" + url[7:]

    return url


def fetch_greenhouse(company, board):
    data = fetch_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
        "?content=true",
        context=source_context(company, "greenhouse"),
    )

    return as_jobs(
        {
            "source": "greenhouse",
            "source_job_id": str(job["id"]),
            "company": company,
            "title": job["title"],
            "location": (job.get("location") or {}).get("name"),
            "url": _upgrade_to_https(job["absolute_url"]),
            **text_fields(plain_text(job.get("content") or "")),
        }
        for job in data["jobs"]
    )


def ashby_location(job):
    # La ubicación principal va en el propio job y el resto en
    # secondaryLocations, ambas bajo la clave "location".
    return join_unique(
        (item.get("location") or "").strip()
        for item in (job, *(job.get("secondaryLocations") or []))
    )


def fetch_ashby(company, board):
    data = fetch_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        "?includeCompensation=true",
        context=source_context(company, "ashby"),
    )
    jobs = []

    for job in data.get("jobs", []):
        job_url = job.get("jobUrl") or job.get("applyUrl")

        if job.get("isListed") is False or not job_url:
            continue

        description = (
            job.get("descriptionPlain")
            or plain_text(job.get("descriptionHtml") or "")
        )
        compensation = job.get("compensation") or {}

        jobs.append({
            "source": "ashby",
            "source_job_id": job_url.rstrip("/").split("/")[-1],
            "company": company,
            "title": job["title"],
            "location": ashby_location(job),
            "url": job_url,
            **text_fields(
                description,
                salary_text=compensation.get(
                    "scrapeableCompensationSalarySummary"
                ),
            ),
        })

    return as_jobs(jobs)


def fetch_lever(company, site):
    data = fetch_json(
        f"https://api.lever.co/v0/postings/{site}?mode=json",
        context=source_context(company, "lever"),
    )
    jobs = []

    for posting in data:
        job_id = str(posting.get("id") or "").strip()
        job_url = posting.get("hostedUrl") or posting.get("applyUrl")

        if not job_id or not job_url:
            continue

        description_parts = [
            posting.get("descriptionPlain") or "",
            *(
                plain_text(section.get("content") or "")
                for section in posting.get("lists") or []
                if isinstance(section, dict)
            ),
            posting.get("additionalPlain") or "",
        ]
        description = re.sub(
            r"\s+",
            " ",
            " ".join(part for part in description_parts if part),
        ).strip()
        categories = posting.get("categories") or {}
        locations = categories.get("allLocations") or []

        if isinstance(locations, str):
            locations = [locations]

        location = "; ".join(
            str(value).strip() for value in locations if str(value).strip()
        )
        salary_range = posting.get("salaryRange")
        salary = None

        if isinstance(salary_range, dict):
            salary = format_salary(
                salary_range.get("min"),
                salary_range.get("max"),
                salary_range.get("currency"),
                salary_range.get("interval"),
            )

        jobs.append({
            "source": f"lever:{site}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("text") or "Untitled",
            "location": location or categories.get("location") or None,
            "url": job_url,
            **text_fields(description, salary_text=salary),
        })

    return as_jobs(jobs)


def bamboohr_location(posting):
    location = posting.get("location") or posting.get("atsLocation") or {}

    if isinstance(location, str):
        return location.strip() or None

    if not isinstance(location, dict):
        return None

    return join_unique(
        (
            str(location.get(key) or "").strip()
            for key in ("city", "state", "country")
        ),
        separator=", ",
    )


def fetch_bamboohr(company, subdomain):
    base_url = f"https://{subdomain}.bamboohr.com/careers"
    data = fetch_json(
        f"{base_url}/list",
        context=source_context(company, "bamboohr"),
    )
    jobs = []

    for posting in data.get("result") or []:
        job_id = str(posting.get("id") or "").strip()
        title = str(posting.get("jobOpeningName") or "").strip()

        if not job_id or not title:
            continue

        description = " ".join(
            str(value).strip()
            for value in (
                posting.get("departmentLabel"),
                posting.get("employmentStatusLabel"),
                posting.get("employmentType"),
            )
            if value
        )

        jobs.append({
            "source": f"bamboohr:{subdomain}",
            "source_job_id": job_id,
            "company": company,
            "title": title,
            "location": bamboohr_location(posting),
            "url": f"{base_url}/{job_id}",
            **text_fields(description),
        })

    return as_jobs(jobs)


def fetch_eightfold(company, host, domain):
    base_url = f"https://{host}"
    context = source_context(company, "eightfold")

    def fetch_page(start):
        query = urlencode({
            "domain": domain,
            "query": "",
            "location": "",
            "start": start,
        })
        payload = fetch_json(
            f"{base_url}/api/pcsx/search?{query}",
            context=context,
        )
        return payload.get("data") or {}

    jobs = []
    seen_ids = set()

    for posting in fetch_offset_pages(
        fetch_page,
        items_key="positions",
        total_key="count",
    ):
        key = str(posting.get("id") or "").strip()

        if not key or key in seen_ids:
            continue

        seen_ids.add(key)
        job_id = str(posting["id"])
        position_url = posting.get("positionUrl") or f"/careers/job/{job_id}"

        jobs.append({
            "source": f"eightfold:{domain}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("name") or "Untitled",
            "location": "; ".join(posting.get("locations") or []) or None,
            "url": f"{base_url}{position_url}?domain={quote(domain)}",
            **text_fields(""),
        })

    def fetch_detail(job):
        query = urlencode({
            "position_id": job["source_job_id"],
            "domain": domain,
            "hl": "en",
        })
        payload = fetch_json(
            f"{base_url}/api/pcsx/position_details?{query}",
            context=context,
        )
        detail = payload.get("data") or {}

        return job, {
            "location": (
                "; ".join(detail.get("locations") or [])
                or job["location"]
            ),
            **text_fields(plain_text(detail.get("jobDescription") or "")),
        }

    enrich_jobs(
        [job for job in jobs if is_relevant_title(job["title"])],
        fetch_detail,
        company=company,
        source="eightfold",
        recoverable=Exception,
    )
    return as_jobs(jobs)


def fetch_workday(company, host, tenant, site):
    base_url = f"https://{host}/wday/cxs/{tenant}/{site}"
    public_url = f"https://{host}/{site}"
    context = source_context(company, "workday")
    limit = 20
    offset = 0
    # externalPath -> posting; el orden de inserción es el del listado.
    postings = {}

    # Se avanza hasta una página corta o sin rutas nuevas; esto último
    # evita un bucle infinito si la API repite páginas.
    while True:
        data = read_browser_json(
            f"{base_url}/jobs",
            context=context,
            payload={
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            },
        )
        page = data.get("jobPostings") or []
        new_postings = 0

        for posting in page:
            external_path = posting.get("externalPath")

            if external_path and external_path not in postings:
                postings[external_path] = posting
                new_postings += 1

        if not new_postings:
            break

        offset += limit

        if len(page) < limit:
            break

    jobs = []
    enrichment_targets = []

    for external_path, posting in postings.items():
        job = {
            "source": f"workday:{tenant}",
            "source_job_id": external_path.rstrip("/").split("/")[-1],
            "company": company,
            "title": posting.get("title") or "",
            "location": posting.get("locationsText"),
            "url": f"{public_url}{external_path}",
            **text_fields(""),
        }
        jobs.append(job)

        if is_relevant_title(job["title"]):
            enrichment_targets.append((job, external_path))

    def fetch_detail(target):
        job, external_path = target
        detail = read_browser_json(
            f"{base_url}{external_path}",
            context=context,
        )
        info = detail.get("jobPostingInfo") or {}
        # El listado resume las ofertas multiubicación como "9 Locations";
        # el detalle trae la principal y el resto en additionalLocations.
        location = join_unique((
            info.get("location"),
            *(info.get("additionalLocations") or []),
        ))

        return job, {
            "location": location or job["location"],
            "url": info.get("externalUrl") or job["url"],
            **text_fields(plain_text(info.get("jobDescription") or "")),
        }

    enrich_jobs(
        enrichment_targets,
        fetch_detail,
        company=company,
        source="workday",
        recoverable=Exception,
    )
    return as_jobs(jobs)


def fetch_smartrecruiters(company, identifier):
    base_url = (
        f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
    )
    context = source_context(company, "smartrecruiters")

    def fetch_page(offset):
        query = urlencode({"limit": 100, "offset": offset})
        return fetch_json(f"{base_url}?{query}", context=context)

    jobs = []

    for posting in fetch_offset_pages(
        fetch_page,
        items_key="content",
        total_key="totalFound",
    ):
        job_id = str(posting.get("id") or "")

        if not job_id:
            continue

        jobs.append({
            "source": f"smartrecruiters:{identifier.casefold()}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("name") or "",
            "location": (posting.get("location") or {}).get("fullLocation"),
            "url": f"https://jobs.smartrecruiters.com/{identifier}/{job_id}",
            **text_fields(""),
        })

    def fetch_detail(job):
        detail = fetch_json(
            f"{base_url}/{job['source_job_id']}",
            context=context,
        )
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        description = plain_text(" ".join(
            section["text"]
            for section in sections.values()
            if isinstance(section, dict) and section.get("text")
        ))

        return job, {
            "url": detail.get("postingUrl") or job["url"],
            **text_fields(description),
        }

    enrich_jobs(
        [job for job in jobs if is_relevant_title(job["title"])],
        fetch_detail,
        company=company,
        source="smartrecruiters",
        recoverable=Exception,
    )
    return as_jobs(jobs)


SUCCESSFACTORS_LOCATION_FIELDS = (
    "internal posting location",
    "job location (region)",
    "job location (country/region)",
    "country",
)


def successfactors_value(block, field):
    match = re.search(
        rf"<{re.escape(field)}>(.*?)</{re.escape(field)}>",
        block,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return ""

    value = match.group(1).strip()

    if value.startswith("<![CDATA[") and value.endswith("]]>"):
        value = value[9:-3]

    return unescape(value).strip()


def successfactors_slug(title):
    value = unicodedata.normalize("NFKD", title or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-") or "job"


def fetch_successfactors(company, host, identifier, url_template):
    query = urlencode({
        "company": identifier,
        "career_ns": "job_listing_summary",
        "resultType": "XML",
    })
    page = fetch_text(
        f"https://{host}/career?{query}",
        context=source_context(company, "successfactors"),
    )
    blocks = re.findall(r"<Job>(.*?)</Job>", page, re.IGNORECASE | re.DOTALL)

    if not blocks:
        raise ValueError(f"{company}: no jobs found in SuccessFactors feed")

    jobs = []

    for block in blocks:
        job_id = plain_text(successfactors_value(block, "ReqId"))
        title = plain_text(successfactors_value(block, "JobTitle"))

        if not job_id or not title:
            continue

        metadata = {}

        for field in re.findall(
            r"<(?:filter|mfield)\d+>(.*?)</(?:filter|mfield)\d+>",
            block,
            re.IGNORECASE | re.DOTALL,
        ):
            label = plain_text(successfactors_value(field, "label"))
            value = plain_text(successfactors_value(field, "value"))

            if label and value:
                metadata[label.casefold()] = value

        jobs.append({
            "source": f"successfactors:{identifier.casefold()}",
            "source_job_id": job_id,
            "company": company,
            "title": title,
            "location": join_unique(
                (metadata.get(key) for key in SUCCESSFACTORS_LOCATION_FIELDS),
                separator=", ",
            ),
            "url": url_template.format(
                job_id=quote(job_id),
                slug=quote(successfactors_slug(title)),
            ),
            **text_fields(
                plain_text(successfactors_value(block, "Job-Description"))
            ),
        })

    return as_jobs(jobs)


def fetch_recruitee(company, slug):
    data = fetch_json(
        f"https://{slug}.recruitee.com/api/offers/",
        context=source_context(company, "recruitee"),
    )
    jobs = []

    for offer in data["offers"]:
        if offer.get("status") != "published":
            continue

        description = plain_text(" ".join(
            offer.get(field) or "" for field in ("description", "requirements")
        ))
        salary = offer.get("salary") or {}

        jobs.append({
            "source": f"recruitee:{slug}",
            "source_job_id": str(offer["id"]),
            "company": company,
            "title": offer["title"],
            "location": join_unique((
                offer.get("location"),
                *map(_city_country, offer.get("locations") or []),
                "Remote" if offer.get("remote") else None,
            )),
            "url": offer["careers_url"],
            **text_fields(
                description,
                salary_text=format_salary(
                    salary.get("min"),
                    salary.get("max"),
                    salary.get("currency"),
                    salary.get("period"),
                ),
            ),
        })

    return as_jobs(jobs)


def _xml_text(element, path):
    value = element.findtext(path)
    return value.strip() if value else ""


def fetch_personio(company, host):
    body = fetch_text(
        f"https://{host}/xml",
        context=source_context(company, "personio"),
    )

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError(
            f"{company}: invalid Personio XML: {error}"
        ) from error

    jobs = []

    for position in root.iter("position"):
        job_id = _xml_text(position, "id")
        description = plain_text(" ".join(
            _xml_text(item, "value")
            for item in position.iter("jobDescription")
        ))
        salary = position.find("salaryInformation")
        salary_text = None

        if salary is not None:
            salary_text = format_salary(
                _xml_text(salary, "min") or None,
                _xml_text(salary, "max") or None,
                _xml_text(salary, "currencyCode"),
                _xml_text(salary, "type"),
            )

        years = _xml_text(position, "yearsOfExperience")

        jobs.append({
            "source": f"personio:{host.split('.')[0]}",
            "source_job_id": job_id,
            "company": company,
            "title": _xml_text(position, "name"),
            "location": join_unique((
                _xml_text(position, "office"),
                *(
                    (office.text or "").strip()
                    for office in position.findall("additionalOffices/office")
                ),
            )),
            "url": f"https://{host}/job/{job_id}",
            **text_fields(
                description,
                salary_text=salary_text,
                experience_fallback=(
                    f"{years} years of experience" if years else None
                ),
            ),
        })

    return as_jobs(jobs)


def fetch_pinpoint(company, slug):
    data = fetch_json(
        f"https://{slug}.pinpointhq.com/postings.json",
        context=source_context(company, "pinpoint"),
    )
    jobs = []

    for posting in data["data"]:
        description = plain_text(" ".join(
            posting.get(field) or ""
            for field in (
                "description",
                "key_responsibilities",
                "skills_knowledge_expertise",
            )
        ))
        salary = None

        if posting.get("compensation_visible"):
            salary = format_salary(
                posting.get("compensation_minimum"),
                posting.get("compensation_maximum"),
                posting.get("compensation_currency"),
                posting.get("compensation_frequency"),
            )

        jobs.append({
            "source": f"pinpoint:{slug}",
            "source_job_id": str(posting["id"]),
            "company": company,
            "title": posting["title"],
            "location": join_unique((
                (posting.get("location") or {}).get("name"),
                (
                    "Remote"
                    if posting.get("workplace_type") == "remote"
                    else None
                ),
            )),
            "url": posting["url"],
            **text_fields(description, salary_text=salary),
        })

    return as_jobs(jobs)


def fetch_workable(company, account):
    context = source_context(company, "workable")
    data = fetch_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{account}",
        context=context,
    )
    jobs = [
        {
            "source": f"workable:{account}",
            "source_job_id": posting["shortcode"],
            "company": company,
            "title": posting["title"],
            "location": join_unique((
                *(
                    _city_country(item)
                    for item in posting.get("locations") or []
                    if not item.get("hidden")
                ),
                "Remote" if posting.get("telecommuting") else None,
            )),
            "url": posting["url"],
            **text_fields(""),
        }
        for posting in data["jobs"]
    ]

    def fetch_detail(job):
        detail = fetch_json(
            "https://apply.workable.com/api/v2/accounts/"
            f"{account}/jobs/{job['source_job_id']}",
            context=context,
        )
        description = plain_text(" ".join(
            detail.get(field) or ""
            for field in ("description", "requirements")
        ))
        return job, text_fields(description)

    # El widget no trae descripción: se enriquece solo lo relevante, como
    # en SmartRecruiters, para no multiplicar peticiones en cuentas grandes.
    enrich_jobs(
        [job for job in jobs if is_relevant_title(job["title"])],
        fetch_detail,
        company=company,
        source="workable",
        recoverable=RECOVERABLE_FETCH_ERRORS,
    )
    return as_jobs(jobs)


class _FactorialJobsParser(HTMLParser):
    """Lee las tarjetas ``li.job-offer-item`` y el filtro de ubicaciones."""

    def __init__(self):
        super().__init__()
        self.locations = {}
        self.jobs = []
        self._option = None
        self._job = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "option" and attrs.get("value"):
            self._option = attrs["value"]
        elif tag == "li" and "job-offer-item" in (attrs.get("class") or ""):
            self._job = {
                "url": attrs.get("data-job-postings-url"),
                "location_id": attrs.get("data-location-id"),
                "remote": attrs.get("data-is-remote") == "true",
                "title": "",
            }
            self.jobs.append(self._job)
        elif (
            tag == "div"
            and self._job is not None
            and not self._job["title"]
            and "font-bold" in (attrs.get("class") or "")
        ):
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "option":
            self._option = None
        elif tag == "div":
            self._in_title = False
        elif tag == "li":
            self._job = None

    def handle_data(self, data):
        if self._option is not None:
            self.locations[self._option] = data.strip()
        elif self._in_title:
            self._job["title"] += data.strip()


def fetch_factorial(company, host):
    parser = _FactorialJobsParser()
    parser.feed(fetch_text(
        f"https://{host}/",
        context=source_context(company, "factorial"),
    ))
    jobs = []

    for card in parser.jobs:
        match = re.search(r"/job_posting/[\w-]*?(\d+)/?$", card["url"] or "")

        if not match or not card["title"]:
            continue

        jobs.append({
            "source": f"factorial:{host.split('.')[0]}",
            "source_job_id": match.group(1),
            "company": company,
            "title": card["title"],
            "location": join_unique((
                parser.locations.get(card["location_id"]),
                "Remote" if card["remote"] else None,
            )),
            "url": card["url"],
            **text_fields(""),
        })

    if parser.jobs and not jobs:
        raise ValueError(
            f"{company}: Factorial job cards found but none parsed"
        )

    return as_jobs(jobs)

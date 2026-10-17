import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.parse import quote, urlencode
from urllib.request import Request
from xml.etree import ElementTree

from job_radar.matching import extract_experience, extract_salary

from .common import as_jobs as _as_jobs
from .common import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    fetch_json,
    fetch_text,
    plain_text,
    read_json,
    workday_relevant_title,
)


LOGGER = logging.getLogger(__name__)


def fetch_greenhouse(company, board):
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{board}/jobs?content=true"
    )

    data = fetch_json(
        url,
        context=f"company={company!r} source=greenhouse",
    )
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

    return _as_jobs(result)


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

    data = fetch_json(
        url,
        context=f"company={company!r} source=ashby",
    )
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

    return _as_jobs(result)


def lever_salary(salary_range):
    if not isinstance(salary_range, dict):
        return None

    minimum = salary_range.get("min")
    maximum = salary_range.get("max")

    if minimum is None and maximum is None:
        return None

    if minimum is not None and maximum is not None:
        result = f"{minimum} – {maximum}"
    else:
        result = str(minimum if minimum is not None else maximum)

    currency = salary_range.get("currency")
    interval = salary_range.get("interval")

    if currency:
        result += f" {currency}"

    if interval:
        result += f" / {str(interval).replace('-', ' ')}"

    return result


def fetch_lever(company, site):
    data = fetch_json(
        "https://api.lever.co/v0/postings/"
        f"{site}?mode=json",
        context=f"company={company!r} source=lever",
    )
    result = []

    for posting in data:
        job_id = str(posting.get("id") or "").strip()
        job_url = posting.get("hostedUrl") or posting.get("applyUrl")

        if not job_id or not job_url:
            continue

        description_parts = [posting.get("descriptionPlain") or ""]

        for section in posting.get("lists") or []:
            if isinstance(section, dict):
                description_parts.append(
                    plain_text(section.get("content") or "")
                )

        description_parts.append(posting.get("additionalPlain") or "")
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
            str(value).strip()
            for value in locations
            if str(value).strip()
        )

        if not location:
            location = categories.get("location")

        result.append({
            "source": f"lever:{site}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("text") or "Untitled",
            "location": location or None,
            "url": job_url,
            "description": description,
            "salary_text": (
                lever_salary(posting.get("salaryRange"))
                or extract_salary(description)
            ),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


def bamboohr_location(posting):
    location = posting.get("location") or posting.get("atsLocation") or {}

    if isinstance(location, str):
        return location.strip() or None

    if not isinstance(location, dict):
        return None

    values = []

    for key in ("city", "state", "country"):
        value = str(location.get(key) or "").strip()

        if value and value not in values:
            values.append(value)

    return ", ".join(values) or None


def fetch_bamboohr(company, subdomain):
    base_url = f"https://{subdomain}.bamboohr.com/careers"
    data = fetch_json(
        f"{base_url}/list",
        context=f"company={company!r} source=bamboohr",
    )
    result = []

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

        result.append({
            "source": f"bamboohr:{subdomain}",
            "source_job_id": job_id,
            "company": company,
            "title": title,
            "location": bamboohr_location(posting),
            "url": f"{base_url}/{job_id}",
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


def fetch_eightfold(company, host, domain):
    base_url = f"https://{host}"
    start = 0
    postings = []
    seen_ids = set()

    while True:
        query = urlencode({
            "domain": domain,
            "query": "",
            "location": "",
            "start": start,
        })
        payload = fetch_json(
            f"{base_url}/api/pcsx/search?{query}",
            context=f"company={company!r} source=eightfold",
        )
        data = payload.get("data") or {}
        page = data.get("positions") or []

        if not page:
            break

        for posting in page:
            job_id = str(posting.get("id") or "").strip()

            if job_id and job_id not in seen_ids:
                seen_ids.add(job_id)
                postings.append(posting)

        start += len(page)

        if start >= data.get("count", start):
            break

    jobs = []
    enrichment_targets = []

    for posting in postings:
        job_id = str(posting["id"])
        position_url = posting.get("positionUrl")

        if not position_url:
            position_url = f"/careers/job/{job_id}"

        job = {
            "source": f"eightfold:{domain}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("name") or "Untitled",
            "location": "; ".join(posting.get("locations") or []) or None,
            "url": f"{base_url}{position_url}?domain={quote(domain)}",
            "description": "",
            "salary_text": None,
            "experience_text": None,
        }
        jobs.append(job)

        if workday_relevant_title(job["title"]):
            enrichment_targets.append(job)

    LOGGER.info(
        "enrichment_started company=%r source=eightfold candidates=%s",
        company,
        len(enrichment_targets),
    )

    def fetch_detail(job):
        query = urlencode({
            "position_id": job["source_job_id"],
            "domain": domain,
            "hl": "en",
        })
        payload = fetch_json(
            f"{base_url}/api/pcsx/position_details?{query}",
            context=f"company={company!r} source=eightfold",
        )
        detail = payload.get("data") or {}
        description = plain_text(detail.get("jobDescription") or "")

        return job, {
            "location": (
                "; ".join(detail.get("locations") or [])
                or job["location"]
            ),
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        }

    enriched = 0
    failures = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_detail, job)
            for job in enrichment_targets
        ]

        for future in as_completed(futures):
            try:
                job, values = future.result()
                job.update(values)
                enriched += 1
            except Exception as error:
                failures += 1
                LOGGER.warning(
                    "enrichment_detail_failed company=%r source=eightfold "
                    "error_type=%s error_message=%r",
                    company,
                    type(error).__name__,
                    str(error),
                )

    LOGGER.info(
        "enrichment_finished company=%r source=eightfold enriched=%s "
        "failures=%s",
        company,
        enriched,
        failures,
    )

    return _as_jobs(jobs)


def fetch_workday(company, host, tenant, site):
    base_url = f"https://{host}/wday/cxs/{tenant}/{site}"
    public_url = f"https://{host}/{site}"
    limit = 20
    offset = 0
    postings = []
    seen_paths = set()

    while True:
        payload = json.dumps({
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }).encode("utf-8")

        request = Request(
            f"{base_url}/jobs",
            data=payload,
            headers={
                "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

        data = read_json(
            request,
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            context=f"company={company!r} source=workday",
        )

        page = data.get("jobPostings") or []

        if not page:
            break

        new_page = []

        for posting in page:
            external_path = posting.get("externalPath")

            if not external_path or external_path in seen_paths:
                continue

            seen_paths.add(external_path)
            new_page.append(posting)

        if not new_page:
            break

        postings.extend(new_page)
        offset += limit

        if len(page) < limit:
            break

    jobs = []
    enrichment_targets = []

    for posting in postings:
        external_path = posting.get("externalPath")

        if not external_path:
            continue

        job = {
            "source": f"workday:{tenant}",
            "source_job_id": external_path.rstrip("/").split("/")[-1],
            "company": company,
            "title": posting.get("title") or "",
            "location": posting.get("locationsText"),
            "url": f"{public_url}{external_path}",
            "description": "",
            "salary_text": None,
            "experience_text": None,
        }
        jobs.append(job)

        if workday_relevant_title(job["title"]):
            enrichment_targets.append((job, external_path))

    LOGGER.info(
        "enrichment_started company=%r source=workday candidates=%s",
        company,
        len(enrichment_targets),
    )

    def fetch_detail(target):
        job, external_path = target
        request = Request(
            f"{base_url}{external_path}",
            headers={
                "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
                "Accept": "application/json",
            },
        )

        detail = read_json(
            request,
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            context=f"company={company!r} source=workday",
        )

        info = detail.get("jobPostingInfo") or {}
        description = plain_text(
            info.get("jobDescription") or ""
        )

        return job, {
            "location": info.get("location") or job["location"],
            "url": info.get("externalUrl") or job["url"],
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        }

    enriched = 0
    failures = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_detail, target)
            for target in enrichment_targets
        ]

        for future in as_completed(futures):
            try:
                job, values = future.result()
                job.update(values)
                enriched += 1

            except Exception as error:
                failures += 1
                LOGGER.warning(
                    "enrichment_detail_failed company=%r source=workday "
                    "error_type=%s error_message=%r",
                    company,
                    type(error).__name__,
                    str(error),
                )

    LOGGER.info(
        "enrichment_finished company=%r source=workday enriched=%s "
        "failures=%s",
        company,
        enriched,
        failures,
    )

    return _as_jobs(jobs)


def fetch_smartrecruiters(company, identifier):
    base_url = (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{identifier}/postings"
    )
    limit = 100
    offset = 0
    postings = []

    while True:
        data = fetch_json(
            f"{base_url}?{urlencode({'limit': limit, 'offset': offset})}",
            context=f"company={company!r} source=smartrecruiters",
        )
        page = data.get("content") or []

        if not page:
            break

        postings.extend(page)
        offset += len(page)

        if offset >= data.get("totalFound", offset):
            break

    jobs = []
    enrichment_targets = []

    for posting in postings:
        job_id = str(posting.get("id") or "")

        if not job_id:
            continue

        location_data = posting.get("location") or {}
        job = {
            "source": f"smartrecruiters:{identifier.casefold()}",
            "source_job_id": job_id,
            "company": company,
            "title": posting.get("name") or "",
            "location": location_data.get("fullLocation"),
            "url": f"https://jobs.smartrecruiters.com/{identifier}/{job_id}",
            "description": "",
            "salary_text": None,
            "experience_text": None,
        }
        jobs.append(job)

        if workday_relevant_title(job["title"]):
            enrichment_targets.append(job)

    LOGGER.info(
        "enrichment_started company=%r source=smartrecruiters candidates=%s",
        company,
        len(enrichment_targets),
    )

    def fetch_detail(job):
        detail = fetch_json(
            f"{base_url}/{job['source_job_id']}",
            context=f"company={company!r} source=smartrecruiters",
        )
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        html_parts = []

        for section in sections.values():
            if isinstance(section, dict) and section.get("text"):
                html_parts.append(section["text"])

        description = plain_text(" ".join(html_parts))

        return job, {
            "url": detail.get("postingUrl") or job["url"],
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        }

    enriched = 0
    failures = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_detail, job)
            for job in enrichment_targets
        ]

        for future in as_completed(futures):
            try:
                job, values = future.result()
                job.update(values)
                enriched += 1
            except Exception as error:
                failures += 1
                LOGGER.warning(
                    "enrichment_detail_failed company=%r "
                    "source=smartrecruiters error_type=%s error_message=%r",
                    company,
                    type(error).__name__,
                    str(error),
                )

    LOGGER.info(
        "enrichment_finished company=%r source=smartrecruiters enriched=%s "
        "failures=%s",
        company,
        enriched,
        failures,
    )

    return _as_jobs(jobs)


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
    url = (
        f"https://{host}/career?"
        + urlencode({
            "company": identifier,
            "career_ns": "job_listing_summary",
            "resultType": "XML",
        })
    )
    page = fetch_text(
        url,
        context=f"company={company!r} source=successfactors",
    )
    blocks = re.findall(
        r"<Job>(.*?)</Job>",
        page,
        re.IGNORECASE | re.DOTALL,
    )

    if not blocks:
        raise ValueError(f"{company}: no jobs found in SuccessFactors feed")

    jobs = []

    for block in blocks:
        job_id = plain_text(successfactors_value(block, "ReqId"))
        title = plain_text(successfactors_value(block, "JobTitle"))

        if not job_id or not title:
            continue

        description = plain_text(
            successfactors_value(block, "Job-Description")
        )
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

        location_parts = []

        for key in (
            "internal posting location",
            "job location (region)",
            "job location (country/region)",
            "country",
        ):
            value = metadata.get(key)

            if value and value not in location_parts:
                location_parts.append(value)

        job_url = url_template.format(
            job_id=quote(job_id),
            slug=quote(successfactors_slug(title)),
        )

        jobs.append({
            "source": f"successfactors:{identifier.casefold()}",
            "source_job_id": job_id,
            "company": company,
            "title": title,
            "location": ", ".join(location_parts) or None,
            "url": job_url,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(jobs)


def _joined_locations(*values):
    return "; ".join(dict.fromkeys(value for value in values if value))


def fetch_recruitee(company, slug):
    data = fetch_json(
        f"https://{slug}.recruitee.com/api/offers/",
        context=f"company={company!r} source=recruitee",
    )
    result = []

    for offer in data["offers"]:
        if offer.get("status") != "published":
            continue

        description = plain_text(
            " ".join(
                offer.get(field) or ""
                for field in ("description", "requirements")
            )
        )
        location = _joined_locations(
            offer.get("location"),
            *(
                ", ".join(
                    value
                    for value in (item.get("city"), item.get("country"))
                    if value
                )
                for item in offer.get("locations") or []
            ),
            "Remote" if offer.get("remote") else None,
        )
        salary = offer.get("salary") or {}

        result.append({
            "source": f"recruitee:{slug}",
            "source_job_id": str(offer["id"]),
            "company": company,
            "title": offer["title"],
            "location": location or None,
            "url": offer["careers_url"],
            "description": description,
            "salary_text": (
                lever_salary({**salary, "interval": salary.get("period")})
                or extract_salary(description)
            ),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


def _xml_text(element, path):
    value = element.findtext(path)
    return value.strip() if value else ""


def fetch_personio(company, host):
    body = fetch_text(
        f"https://{host}/xml",
        context=f"company={company!r} source=personio",
    )

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError(f"{company}: invalid Personio XML: {error}") from error

    result = []

    for position in root.iter("position"):
        job_id = _xml_text(position, "id")
        description = plain_text(
            " ".join(
                _xml_text(item, "value")
                for item in position.iter("jobDescription")
            )
        )
        location = _joined_locations(
            _xml_text(position, "office"),
            *(
                (office.text or "").strip()
                for office in position.findall("additionalOffices/office")
            ),
        )
        salary = position.find("salaryInformation")
        years = _xml_text(position, "yearsOfExperience")

        result.append({
            "source": f"personio:{host.split('.')[0]}",
            "source_job_id": job_id,
            "company": company,
            "title": _xml_text(position, "name"),
            "location": location or None,
            "url": f"https://{host}/job/{job_id}",
            "description": description,
            "salary_text": (
                lever_salary({
                    "min": _xml_text(salary, "min") or None,
                    "max": _xml_text(salary, "max") or None,
                    "currency": _xml_text(salary, "currencyCode"),
                    "interval": _xml_text(salary, "type"),
                })
                if salary is not None
                else None
            ) or extract_salary(description),
            "experience_text": (
                extract_experience(description)
                or (f"{years} years of experience" if years else None)
            ),
        })

    return _as_jobs(result)


def fetch_pinpoint(company, slug):
    data = fetch_json(
        f"https://{slug}.pinpointhq.com/postings.json",
        context=f"company={company!r} source=pinpoint",
    )
    result = []

    for posting in data["data"]:
        description = plain_text(
            " ".join(
                posting.get(field) or ""
                for field in (
                    "description",
                    "key_responsibilities",
                    "skills_knowledge_expertise",
                )
            )
        )
        location = _joined_locations(
            (posting.get("location") or {}).get("name"),
            "Remote" if posting.get("workplace_type") == "remote" else None,
        )
        salary = (
            lever_salary({
                "min": posting.get("compensation_minimum"),
                "max": posting.get("compensation_maximum"),
                "currency": posting.get("compensation_currency"),
                "interval": posting.get("compensation_frequency"),
            })
            if posting.get("compensation_visible")
            else None
        )

        result.append({
            "source": f"pinpoint:{slug}",
            "source_job_id": str(posting["id"]),
            "company": company,
            "title": posting["title"],
            "location": location or None,
            "url": posting["url"],
            "description": description,
            "salary_text": salary or extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


def fetch_workable(company, account):
    data = fetch_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{account}",
        context=f"company={company!r} source=workable",
    )
    jobs = []

    for posting in data["jobs"]:
        locations = [
            ", ".join(
                value
                for value in (item.get("city"), item.get("country"))
                if value
            )
            for item in posting.get("locations") or []
            if not item.get("hidden")
        ]
        jobs.append({
            "source": f"workable:{account}",
            "source_job_id": posting["shortcode"],
            "company": company,
            "title": posting["title"],
            "location": _joined_locations(
                *locations,
                "Remote" if posting.get("telecommuting") else None,
            ) or None,
            "url": posting["url"],
            "description": "",
            "salary_text": None,
            "experience_text": None,
        })

    # El widget no trae descripción: se enriquece solo lo relevante, como
    # en SmartRecruiters, para no multiplicar peticiones en cuentas grandes.
    enrichment_targets = [
        job for job in jobs if workday_relevant_title(job["title"])
    ]

    def fetch_detail(job):
        detail = fetch_json(
            "https://apply.workable.com/api/v2/accounts/"
            f"{account}/jobs/{job['source_job_id']}",
            context=f"company={company!r} source=workable",
        )
        description = plain_text(
            " ".join(
                detail.get(field) or ""
                for field in ("description", "requirements")
            )
        )
        return job, {
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        }

    failures = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_detail, job)
            for job in enrichment_targets
        ]

        for future in as_completed(futures):
            try:
                job, values = future.result()
            except (OSError, HTTPException, ValueError) as error:
                failures += 1
                LOGGER.warning(
                    "enrichment_detail_failed company=%r source=workable "
                    "error_type=%s error_message=%r",
                    company,
                    type(error).__name__,
                    str(error),
                )
                continue

            job.update(values)

    LOGGER.info(
        "enrichment_finished company=%r source=workable enriched=%s "
        "failures=%s",
        company,
        len(enrichment_targets) - failures,
        failures,
    )

    return _as_jobs(jobs)


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
    parser.feed(
        fetch_text(
            f"https://{host}/",
            context=f"company={company!r} source=factorial",
        )
    )
    result = []

    for job in parser.jobs:
        match = re.search(r"/job_posting/[\w-]*?(\d+)/?$", job["url"] or "")

        if not match or not job["title"]:
            continue

        result.append({
            "source": f"factorial:{host.split('.')[0]}",
            "source_job_id": match.group(1),
            "company": company,
            "title": job["title"],
            "location": _joined_locations(
                parser.locations.get(job["location_id"]),
                "Remote" if job["remote"] else None,
            ) or None,
            "url": job["url"],
            "description": "",
            "salary_text": None,
            "experience_text": None,
        })

    if parser.jobs and not result:
        raise ValueError(f"{company}: Factorial job cards found but none parsed")

    return _as_jobs(result)

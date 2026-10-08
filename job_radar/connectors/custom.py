import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from job_radar.domain import Job
from job_radar.matching import extract_experience, extract_salary

from .common import as_jobs as _as_jobs
from .common import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    JSON_TIMEOUT_SECONDS,
    fetch_json,
    fetch_text,
    plain_text,
    read_url,
    request_with_retry,
    workday_relevant_title,
)


LOGGER = logging.getLogger(__name__)


def fetch_dassault_systemes():
    endpoint = "https://www.3ds.com/apisearch/card_search_api"
    limit = 100
    offset = 0
    hits = []
    query = '#all card_content_lang:en  (card_content_type="career") '

    while True:
        url = endpoint + "?" + urlencode({
            "q": query,
            "s": "desc(card_content_start_datetime)",
            "b": offset,
            "hf": limit,
            "output_format": "json",
        })
        data = fetch_json(
            url,
            context="company='Dassault Systèmes' source=dassault",
        )
        page = data.get("hits") or []

        if not page:
            break

        hits.extend(page)
        offset += len(page)

        if offset >= data.get("nhits", offset):
            break

    result = []

    for hit in hits:
        metadata = {
            item.get("name"): item.get("value")
            for item in hit.get("metas") or []
            if item.get("name")
        }
        job_id = str(metadata.get("card_id") or "")
        job_url = (
            metadata.get("content_cta_1_url")
            or metadata.get("content_cta_1_url_id")
        )

        if not job_id or not job_url:
            continue

        description = plain_text(metadata.get("content_summary") or "")

        result.append({
            "source": "dassault:careers",
            "source_job_id": job_id,
            "company": "Dassault Systèmes",
            "title": metadata.get("content_title") or "",
            "location": metadata.get("content_info_2_value"),
            "url": job_url,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


def fetch_visma():
    page = fetch_text(
        "https://www.visma.com/careers/open-positions",
        context="company='Visma' source=visma",
    )
    blocks = re.split(
        r'(?=<div role="listitem" class="openposition-list-item)',
        page,
    )[1:]
    result = []
    seen_ids = set()

    for block in blocks:
        title_match = re.search(r'data-job-title="([^"]+)"', block)
        url_match = re.search(r'<a[^>]+href="([^"]+)"', block)

        if not title_match or not url_match:
            continue

        title = unescape(title_match.group(1)).strip()
        job_url = unescape(url_match.group(1)).strip()
        parsed_url = urlsplit(job_url)
        job_id = f"{parsed_url.netloc}{parsed_url.path}".rstrip("/")

        if not job_id or job_id in seen_ids:
            continue

        seen_ids.add(job_id)

        country_match = re.search(
            r'fs-cmssort-field="countries"[^>]*>(.*?)</div>',
            block,
            re.IGNORECASE | re.DOTALL,
        )
        city_match = re.search(
            r'class="text-size-small text-wrap line-break '
            r'no-gap w-embed">(.*?)</div>',
            block,
            re.IGNORECASE | re.DOTALL,
        )
        area_match = re.search(
            r'fs-cmssort-field="areasofwork"[^>]*>(.*?)</div>',
            block,
            re.IGNORECASE | re.DOTALL,
        )
        tags_match = re.search(
            r'fs-cmssort-field="tags"[^>]*>(.*?)</div>',
            block,
            re.IGNORECASE | re.DOTALL,
        )

        location_parts = []

        for match in (country_match, city_match):
            value = plain_text(match.group(1)) if match else ""
            value = value.strip(" |")

            if value and value not in location_parts:
                location_parts.append(value)

        description = " ".join(
            value
            for value in (
                plain_text(area_match.group(1)) if area_match else "",
                plain_text(tags_match.group(1)) if tags_match else "",
            )
            if value
        )

        result.append({
            "source": "visma:careers",
            "source_job_id": job_id,
            "company": "Visma",
            "title": title,
            "location": ", ".join(location_parts) or None,
            "url": job_url,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    if not result:
        raise ValueError("Visma: no jobs found")

    return _as_jobs(result)


class HiddenInputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)

        if (
            tag == "input"
            and values.get("type") == "hidden"
            and values.get("name")
        ):
            self.values[values["name"]] = values.get("value", "")


def sage_listing_rows(page):
    rows = []

    for row in re.findall(
        r'<tr class="[^"]*dataRow[^"]*"[^>]*>(.*?)</tr>',
        page,
        re.IGNORECASE | re.DOTALL,
    ):
        link = re.search(
            r'href="(/careers/fRecruit__ApplyJob\?'
            r'vacancyNo=(VN\d+)&(?:amp;)?portal=English)">([^<]+)</a>',
            row,
            re.IGNORECASE,
        )

        if not link:
            continue

        cells = re.findall(
            r'<td[^>]*>(.*?)</td>',
            row,
            re.IGNORECASE | re.DOTALL,
        )
        rows.append({
            "id": link.group(2),
            "title": unescape(link.group(3)).strip(),
            "country": plain_text(cells[3]) if len(cells) > 3 else "",
            "office": plain_text(cells[4]) if len(cells) > 4 else "",
        })

    return rows


def fetch_sage():
    base_url = "https://sagehr.my.salesforce-sites.com"
    list_path = "/careers/fRecruit__ApplyJobList"
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    request = Request(
        f"{base_url}{list_path}?portal=English",
        headers={"User-Agent": "Mozilla/5.0 Job-Radar/1.0"},
    )

    body = read_url(
        request,
        timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
        context="company='Sage' source=sagepeople",
        opener=opener,
    )
    page = body.decode("utf-8", errors="ignore")

    postings = []
    page_signatures = set()

    for _page_number in range(100):
        rows = sage_listing_rows(page)
        signature = tuple(row["id"] for row in rows)

        if not rows:
            raise ValueError("Sage: no jobs found")

        if signature in page_signatures:
            raise ValueError("Sage: repeated page while paginating")

        page_signatures.add(signature)
        postings.extend(rows)

        next_anchor = re.search(
            r'<a href="#" onclick="([^"]+)" '
            r'class="link-pagination">Next</a>',
            page,
            re.IGNORECASE,
        )

        if not next_anchor:
            break

        next_values = re.search(
            r"jsfcljs\([^,]+,'([^,]+),([^']+)'",
            next_anchor.group(1),
        )
        form = re.search(
            r'<form id="([^"]+)"[^>]+'
            r'action="/careers/fRecruit__ApplyJobList"',
            page,
            re.IGNORECASE,
        )

        if not next_values or not form:
            raise ValueError("Sage: invalid pagination form")

        parser = HiddenInputParser()
        parser.feed(page)
        fields = parser.values
        fields[form.group(1)] = form.group(1)
        fields[next_values.group(1)] = next_values.group(2)
        request = Request(
            f"{base_url}{list_path}",
            data=urlencode(fields).encode("utf-8"),
            headers={
                "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        body = read_url(
            request,
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            context="company='Sage' source=sagepeople",
            opener=opener,
        )
        page = body.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Sage: pagination limit reached")

    jobs = []
    enrichment_targets = []

    for posting in postings:
        job_url = (
            f"{base_url}/careers/fRecruit__ApplyJob?"
            + urlencode({
                "vacancyNo": posting["id"],
                "portal": "English",
            })
        )
        location = ", ".join(
            value
            for value in (posting["office"], posting["country"])
            if value and value != "\xa0"
        )
        job = {
            "source": "sagepeople:sage",
            "source_job_id": posting["id"],
            "company": "Sage",
            "title": posting["title"],
            "location": location or None,
            "url": job_url,
            "description": "",
            "salary_text": None,
            "experience_text": None,
        }
        jobs.append(job)

        if workday_relevant_title(job["title"]):
            enrichment_targets.append(job)

    def fetch_detail(job):
        page = fetch_text(
            job["url"],
            context="company='Sage' source=sagepeople",
        )
        schema = extract_jobposting_jsonld(page)

        if not schema:
            raise ValueError("JobPosting JSON-LD not found")

        description = plain_text(schema.get("description") or "")

        return job, {
            "location": schema_location(schema) or job["location"],
            "description": description,
            "salary_text": (
                schema_salary(schema)
                or extract_salary(description)
            ),
            "experience_text": extract_experience(description),
        }

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_detail, job)
            for job in enrichment_targets
        ]

        for future in as_completed(futures):
            try:
                job, values = future.result()
                job.update(values)
            except Exception as error:
                LOGGER.warning(
                    "enrichment_detail_failed company='Sage' "
                    "source=sagepeople error_type=%s error_message=%r",
                    type(error).__name__,
                    str(error),
                )

    return _as_jobs(jobs)


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
    data = fetch_json(
        "https://career.spendesk.com/jobs.json",
        context="company='Spendesk' source=teamtailor",
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

    return _as_jobs(result)


def fetch_thetaray():
    url = (
        "https://www.comeet.co/careers-api/2.0/company/"
        "72.00F/positions"
        "?token=27F9FC16779FC13F8EFA13F84FE27F77D"
        "&details=true"
    )

    data = fetch_json(
        url,
        context="company='ThetaRay' source=comeet",
    )

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

    return _as_jobs(result)


def fetch_deel_job(job_id, company="Deel", board="deel"):
    url = (
        f"https://jobs.deel.com/{board}/job-details/"
        f"{job_id}/overview"
    )

    page = fetch_text(
        url,
        context=f"company={company!r} source=deel",
    )
    schema = extract_jobposting_jsonld(page)

    if not schema:
        return None

    description = plain_text(schema.get("description") or "")

    return Job.from_mapping({
        "source": (
            "deel:careers"
            if company == "Deel" and board == "deel"
            else f"deel:{board}"
        ),
        "source_job_id": job_id,
        "company": company,
        "title": schema.get("title") or "Untitled",
        "location": schema_location(schema),
        "url": url,
        "description": description,
        "salary_text": (
            schema_salary(schema)
            or extract_salary(description)
        ),
        "experience_text": extract_experience(description),
    })


def fetch_deel_company(company, board):
    page = fetch_text(
        f"https://jobs.deel.com/{board}",
        context=f"company={company!r} source=deel",
    )
    ids = sorted(set(
        re.findall(
            rf"/{re.escape(board)}/job-details/"
            r"([0-9a-fA-F-]{36})",
            page,
        )
    ))

    if not ids:
        raise ValueError(f"{company}: no job IDs found")

    result = []
    failures = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                fetch_deel_job,
                job_id,
                company,
                board,
            ): job_id
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
            f"{company}: no jobs parsed ({failures} failures)"
        )

    if failures:
        LOGGER.warning(
            "job_pages_failed company=%r source=deel failures=%s",
            company,
            failures,
        )

    return _as_jobs(result)


def fetch_deel():
    page = fetch_text(
        "https://www.deel.com/careers/",
        context="company='Deel' source=deel",
    )

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
        LOGGER.warning(
            "job_pages_failed company='Deel' source=deel failures=%s",
            failures,
        )

    return _as_jobs(result)


def ant_experience(posting, description):
    extracted = extract_experience(description)

    if extracted:
        return extracted

    experience = posting.get("experience") or {}

    if not isinstance(experience, dict):
        return None

    minimum = experience.get("from")
    maximum = experience.get("to")

    if minimum is None:
        return None

    if maximum is not None:
        return f"{minimum}-{maximum} years of experience"

    return f"{minimum}+ years of experience"


def fetch_ant_group():
    url = (
        "https://hrcareersweb.antgroup.com/"
        "api/social/position/search"
    )
    page_index = 1
    page_size = 10
    postings = []

    while True:
        payload = json.dumps({
            "language": "en",
            "channel": "group_official_site",
            "categories": "",
            "key": "",
            "regions": "",
            "subCategories": "",
            "pageIndex": page_index,
            "pageSize": page_size,
            "bgCode": "M7892",
        }).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={
                "User-Agent": "Mozilla/5.0 Job-Radar/1.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.ant-intl.com",
                "Referer": "https://www.ant-intl.com/",
            },
            method="POST",
        )

        body = read_url(
            request,
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            context="company='Ant Group / Ant International' source=ant",
        )
        data = json.loads(body)

        if not data.get("success"):
            raise ValueError(
                "Ant Group: "
                + str(data.get("errorMsg") or "invalid response")
            )

        page = data.get("content") or []

        if not page:
            break

        postings.extend(page)

        if len(postings) >= data.get("totalCount", len(postings)):
            break

        page_index += 1

    result = []

    for posting in postings:
        job_id = str(posting.get("id") or "").strip()
        title = str(posting.get("name") or "").strip()

        if not job_id or not title:
            continue

        description = plain_text(
            " ".join(
                value
                for value in (
                    posting.get("description"),
                    posting.get("requirement"),
                )
                if value
            )
        )

        result.append({
            "source": "ant:careers",
            "source_job_id": job_id,
            "company": "Ant Group / Ant International",
            "title": title,
            "location": "; ".join(
                posting.get("workLocations") or []
            ) or None,
            "url": (
                "https://talent.antgroup.com/off-campus-position?"
                + urlencode({
                    "positionId": job_id,
                    "locale": "US",
                })
            ),
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": ant_experience(posting, description),
        })

    return _as_jobs(result)


def fetch_mambu_job(job_id):
    url = (
        "https://careers-mambu.icims.com/jobs/"
        f"{job_id}/job?in_iframe=1"
    )

    page = fetch_text(
        url,
        context="company='Mambu' source=icims",
    )

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

    return Job.from_mapping({
        "source": "icims:mambu",
        "source_job_id": str(job_id),
        "company": "Mambu",
        "title": title,
        "location": location,
        "url": url,
        "description": description,
        "salary_text": extract_salary(description),
        "experience_text": extract_experience(description),
    })


def fetch_mambu():
    listing_url = (
        "https://careers-mambu.icims.com/jobs/search"
        "?ss=1&searchRelation=keyword_all&in_iframe=1"
    )

    page = fetch_text(
        listing_url,
        context="company='Mambu' source=icims",
    )

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

    return _as_jobs(result)


def fetch_teamtailor_company(
    company,
    base_url,
    source,
):
    data = fetch_json(
        base_url.rstrip("/") + "/jobs.json",
        context=f"company={company!r} source={source}",
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
            "experience_text": extract_experience(description),
        })

    return _as_jobs(result)


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


def fetch_caixabank_tech():
    careers_url = (
        "https://www.caixabanktech.com/"
        "es/join-us-es/"
    )

    listing = fetch_text(
        careers_url,
        context="company='CaixaBank Tech' source=caixabank-tech",
    )

    raw_links = re.findall(
        r'''href=["']([^"']+)["']''',
        listing,
        re.IGNORECASE,
    )

    job_urls = set()

    for raw_link in raw_links:
        link = unescape(raw_link)
        link = link.split("#", 1)[0].split("?", 1)[0]

        if link.startswith("/"):
            link = (
                "https://www.caixabanktech.com"
                + link
            )

        if re.fullmatch(
            r"https://(?:www\.)?caixabanktech\.com/"
            r"es/job/[^/]+/?",
            link,
            re.IGNORECASE,
        ):
            job_urls.add(link.rstrip("/") + "/")

    if not job_urls:
        raise ValueError(
            "CaixaBank Tech: no job links found"
        )

    jobs = []

    for url in sorted(job_urls):
        page = fetch_text(
            url,
            context="company='CaixaBank Tech' source=caixabank-tech",
        )
        page_text = plain_text(page)

        title_match = re.search(
            r"<title[^>]*>\s*"
            r"CaixaBank Tech\s*\|\s*"
            r"(.*?)</title>",
            page,
            re.IGNORECASE | re.DOTALL,
        )

        if not title_match:
            title_match = re.search(
                r"<h1[^>]*>(.*?)</h1>",
                page,
                re.IGNORECASE | re.DOTALL,
            )

        if not title_match:
            raise ValueError(
                f"CaixaBank Tech: title not found: {url}"
            )

        title = plain_text(title_match.group(1))

        location_match = re.search(
            r"Ubicaci[oó]n:\s*(.+?)"
            r"(?=\s+(?:Jornada Laboral|Contrato|Vacantes):)",
            page_text,
            re.IGNORECASE,
        )

        location = None

        if location_match:
            raw_location = location_match.group(1).strip()

            cities = re.findall(
                r"\b(?:Barcelona|Madrid|Sevilla)\b",
                raw_location,
                re.IGNORECASE,
            )

            if cities:
                location = "; ".join(dict.fromkeys(
                    city.title()
                    for city in cities
                ))
            else:
                location = raw_location

        description_match = re.search(
            r"\bBuscamos personas\b",
            page_text,
            re.IGNORECASE,
        )

        description = (
            page_text[description_match.start():]
            if description_match
            else page_text
        )

        source_job_id = url.rstrip("/").rsplit("/", 1)[-1]

        jobs.append({
            "source": "caixabank-tech:careers",
            "source_job_id": source_job_id,
            "company": "CaixaBank Tech",
            "title": title,
            "location": location,
            "url": url,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    return _as_jobs(jobs)


def revolut_slug(title):
    value = (
        unicodedata.normalize("NFKD", title)
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    value = value.lower()

    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def revolut_locations(position):
    locations = []

    for item in position.get("locations") or []:
        name = (item.get("name") or "").strip()

        if name and name not in locations:
            locations.append(name)

    return "; ".join(locations) or None


def _curl_transient_connection_error(error, curl_requests):
    exceptions = getattr(curl_requests, "exceptions", None)
    transient_types = tuple(
        exception_type
        for name in ("Timeout", "ConnectionError")
        if isinstance(
            exception_type := getattr(exceptions, name, None),
            type,
        )
    )
    return bool(transient_types and isinstance(error, transient_types))


def _curl_get_with_retry(curl_requests, url, *, context, **kwargs):
    def get():
        response = curl_requests.get(url, **kwargs)
        response.raise_for_status()
        return response

    return request_with_retry(
        get,
        context=context,
        extra_transient_error=lambda error: _curl_transient_connection_error(
            error,
            curl_requests,
        ),
    )


def fetch_revolut():
    from curl_cffi import requests as curl_requests

    careers_url = "https://www.revolut.com/en-US/careers/"

    response = _curl_get_with_retry(
        curl_requests,
        careers_url,
        context="company='Revolut' source=revolut",
        impersonate="chrome",
        headers={
            "Accept-Language": "en-GB,en;q=0.9",
        },
        timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
    )

    next_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>'
        r'(.*?)</script>',
        response.text,
        re.IGNORECASE | re.DOTALL,
    )

    if not next_match:
        raise ValueError("Revolut: __NEXT_DATA__ no encontrado")

    next_data = json.loads(next_match.group(1))

    build_id = next_data.get("buildId")
    locale = next_data.get("locale") or "en-GB"

    positions = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("positions", [])
    )

    if not build_id:
        raise ValueError("Revolut: buildId no encontrado")

    if not positions:
        raise ValueError("Revolut: listado de ofertas vacío")

    LOGGER.info(
        "listing_fetched company='Revolut' source=revolut jobs_seen=%s",
        len(positions),
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
        if interesting_title.search(position.get("text") or "")
    ]

    LOGGER.info(
        "enrichment_started company='Revolut' source=revolut candidates=%s",
        len(candidates),
    )

    result = []
    enriched = 0
    failures = 0

    candidate_ids = {str(position.get("id")) for position in candidates}

    for position in positions:
        job_id = str(position.get("id") or "").strip()
        title = (position.get("text") or "").strip()

        if not job_id or not title:
            continue

        slug = revolut_slug(title)
        identifier = f"{slug}-{job_id}"
        public_url = (
            "https://www.revolut.com/"
            "careers/position/"
            f"{identifier}/"
        )
        description = ""

        if job_id in candidate_ids:
            detail_url = (
                "https://www.revolut.com/"
                f"_next/data/{build_id}/"
                f"{locale}/careers/position/"
                f"{identifier}.json"
                f"?id={identifier}"
            )

            try:
                detail_response = _curl_get_with_retry(
                    curl_requests,
                    detail_url,
                    context="company='Revolut' source=revolut",
                    impersonate="chrome",
                    headers={
                        "Accept-Language": "en-GB,en;q=0.9",
                        "Referer": careers_url,
                        "X-Nextjs-Data": "1",
                    },
                    timeout=JSON_TIMEOUT_SECONDS,
                )

                detail_json = detail_response.json()
                detail_position = (
                    detail_json
                    .get("pageProps", {})
                    .get("position", {})
                )
                description = plain_text(
                    detail_position.get("description") or ""
                )

                if detail_position.get("locations"):
                    position = {
                        **position,
                        "locations": detail_position["locations"],
                    }

                enriched += 1

            except Exception as error:
                failures += 1

                LOGGER.warning(
                    "enrichment_detail_failed company='Revolut' "
                    "source=revolut title=%r error_type=%s",
                    title,
                    type(error).__name__,
                )

        result.append({
            "source": "revolut:careers",
            "source_job_id": job_id,
            "company": "Revolut",
            "title": title,
            "location": revolut_locations(position),
            "url": public_url,
            "description": description,
            "salary_text": extract_salary(description),
            "experience_text": extract_experience(description),
        })

    LOGGER.info(
        "enrichment_finished company='Revolut' source=revolut "
        "enriched=%s failures=%s",
        enriched,
        failures,
    )

    if failures:
        LOGGER.warning(
            "enrichment_failures company='Revolut' source=revolut "
            "failures=%s",
            failures,
        )

    return _as_jobs(result)

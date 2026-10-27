import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from job_radar.domain import Job

from .common import (
    BROWSER_USER_AGENT,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    RECOVERABLE_FETCH_ERRORS,
    IncompleteSnapshot,
    as_jobs,
    enrich_jobs,
    fetch_json,
    fetch_offset_pages,
    fetch_text,
    is_relevant_title,
    join_unique,
    plain_text,
    read_browser_json,
    read_url,
    source_context,
    text_fields,
)


LOGGER = logging.getLogger(__name__)


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

        for candidate in data if isinstance(data, list) else [data]:
            if (
                isinstance(candidate, dict)
                and candidate.get("@type") == "JobPosting"
            ):
                return candidate

    return None


def _as_list(value):
    return [value] if isinstance(value, dict) else value or []


def _schema_address(item):
    address = item.get("address") if isinstance(item, dict) else None

    if not isinstance(address, dict):
        return None

    parts = []

    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(key)

        if isinstance(value, dict):
            value = value.get("name")

        if value:
            parts.append(str(value))

    return join_unique(parts, separator=", ")


def schema_location(jobposting):
    locations = [
        _schema_address(item)
        for item in _as_list(jobposting.get("jobLocation"))
    ]

    if jobposting.get("jobLocationType") == "TELECOMMUTE":
        required_places = [
            str(item["name"])
            for item in _as_list(
                jobposting.get("applicantLocationRequirements")
            )
            if isinstance(item, dict) and item.get("name")
        ]
        remote = "Remote"

        if required_places:
            remote += " - " + ", ".join(required_places)

        locations.append(remote)

    return join_unique(locations)


def schema_salary(jobposting):
    base = jobposting.get("baseSalary")

    if not isinstance(base, dict):
        return None

    value = base.get("value") or {}

    if not isinstance(value, dict):
        return None

    minimum = value.get("minValue")
    maximum = value.get("maxValue")

    if minimum is not None and maximum is not None:
        result = f"{minimum} – {maximum}"
    elif value.get("value") is not None:
        result = str(value["value"])
    else:
        return None

    if base.get("currency"):
        result += f" {base['currency']}"

    if value.get("unitText"):
        result += f" / {str(value['unitText']).lower()}"

    return result


def _jsonld_fields(schema, fallback_location=None):
    description = plain_text(schema.get("description") or "")

    return {
        "location": schema_location(schema) or fallback_location,
        **text_fields(description, salary_text=schema_salary(schema)),
    }


def fetch_dassault_systemes():
    company = "Dassault Systèmes"
    context = source_context(company, "dassault")

    def fetch_page(offset):
        query = urlencode({
            "q": '#all card_content_lang:en  (card_content_type="career") ',
            "s": "desc(card_content_start_datetime)",
            "b": offset,
            "hf": 100,
            "output_format": "json",
        })
        return fetch_json(
            f"https://www.3ds.com/apisearch/card_search_api?{query}",
            context=context,
        )

    jobs = []

    for hit in fetch_offset_pages(
        fetch_page,
        items_key="hits",
        total_key="nhits",
    ):
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

        jobs.append({
            "source": "dassault:careers",
            "source_job_id": job_id,
            "company": company,
            "title": metadata.get("content_title") or "",
            "location": metadata.get("content_info_2_value"),
            "url": job_url,
            **text_fields(plain_text(metadata.get("content_summary") or "")),
        })

    return as_jobs(jobs)


def _visma_field(block, opening_tag):
    match = re.search(
        rf"{opening_tag}(.*?)</div>",
        block,
        re.IGNORECASE | re.DOTALL,
    )
    return plain_text(match.group(1)) if match else ""


def fetch_visma():
    page = fetch_text(
        "https://www.visma.com/careers/open-positions",
        context=source_context("Visma", "visma"),
    )
    blocks = re.split(
        r'(?=<div role="listitem" class="openposition-list-item)',
        page,
    )[1:]
    jobs = []
    seen_ids = set()

    for block in blocks:
        title_match = re.search(r'data-job-title="([^"]+)"', block)
        url_match = re.search(r'<a[^>]+href="([^"]+)"', block)

        if not title_match or not url_match:
            continue

        job_url = unescape(url_match.group(1)).strip()
        parsed_url = urlsplit(job_url)
        job_id = f"{parsed_url.netloc}{parsed_url.path}".rstrip("/")

        if not job_id or job_id in seen_ids:
            continue

        seen_ids.add(job_id)
        country = _visma_field(block, r'fs-cmssort-field="countries"[^>]*>')
        city = _visma_field(
            block,
            r'class="text-size-small text-wrap line-break no-gap w-embed">',
        )
        area = _visma_field(block, r'fs-cmssort-field="areasofwork"[^>]*>')
        tags = _visma_field(block, r'fs-cmssort-field="tags"[^>]*>')

        jobs.append({
            "source": "visma:careers",
            "source_job_id": job_id,
            "company": "Visma",
            "title": unescape(title_match.group(1)).strip(),
            "location": join_unique(
                (country.strip(" |"), city.strip(" |")),
                separator=", ",
            ),
            "url": job_url,
            **text_fields(" ".join(value for value in (area, tags) if value)),
        })

    if not jobs:
        raise ValueError("Visma: no jobs found")

    return as_jobs(jobs)


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


def _sage_next_page_form(page):
    """Campos del postback de Salesforce para la página siguiente.

    Devuelve ``None`` en la última página.
    """
    next_anchor = re.search(
        r'<a href="#" onclick="([^"]+)" '
        r'class="link-pagination">Next</a>',
        page,
        re.IGNORECASE,
    )

    if not next_anchor:
        return None

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
    return fields


def fetch_sage():
    company = "Sage"
    context = source_context(company, "sagepeople")
    base_url = "https://sagehr.my.salesforce-sites.com"
    list_url = f"{base_url}/careers/fRecruit__ApplyJobList"
    # La paginación es un postback de Visualforce ligado a la sesión.
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def read_page(request):
        body = read_url(
            request,
            timeout=DEFAULT_HTTP_TIMEOUT_SECONDS,
            context=context,
            opener=opener,
        )
        return body.decode("utf-8", errors="ignore")

    page = read_page(Request(
        f"{list_url}?portal=English",
        headers={"User-Agent": BROWSER_USER_AGENT},
    ))
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
        fields = _sage_next_page_form(page)

        if fields is None:
            break

        page = read_page(Request(
            list_url,
            data=urlencode(fields).encode("utf-8"),
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        ))
    else:
        raise ValueError("Sage: pagination limit reached")

    jobs = []

    for posting in postings:
        query = urlencode({"vacancyNo": posting["id"], "portal": "English"})
        location = ", ".join(
            value
            for value in (posting["office"], posting["country"])
            if value and value != "\xa0"
        )
        jobs.append({
            "source": "sagepeople:sage",
            "source_job_id": posting["id"],
            "company": company,
            "title": posting["title"],
            "location": location or None,
            "url": f"{base_url}/careers/fRecruit__ApplyJob?{query}",
            **text_fields(""),
        })

    def fetch_detail(job):
        schema = extract_jobposting_jsonld(
            fetch_text(job["url"], context=context)
        )

        if not schema:
            raise ValueError("JobPosting JSON-LD not found")

        return job, _jsonld_fields(schema, fallback_location=job["location"])

    enrich_jobs(
        [job for job in jobs if is_relevant_title(job["title"])],
        fetch_detail,
        company=company,
        source="sagepeople",
        recoverable=Exception,
    )
    return as_jobs(jobs)


def fetch_teamtailor_company(company, base_url, source):
    data = fetch_json(
        base_url.rstrip("/") + "/jobs.json",
        context=source_context(company, source),
    )
    jobs = []

    for item in data.get("items", []):
        schema = item.get("_jobposting") or {}
        description = plain_text(
            item.get("content_html")
            or schema.get("description")
            or ""
        )

        jobs.append({
            "source": source,
            "source_job_id": str(item["id"]),
            "company": company,
            "title": item["title"],
            "location": schema_location(schema),
            "url": item["url"],
            **text_fields(description, salary_text=schema_salary(schema)),
        })

    return as_jobs(jobs)


def fetch_thetaray():
    data = fetch_json(
        "https://www.comeet.co/careers-api/2.0/company/72.00F/positions"
        "?token=27F9FC16779FC13F8EFA13F84FE27F77D&details=true",
        context=source_context("ThetaRay", "comeet"),
    )
    jobs = []

    for item in data:
        job_url = (
            item.get("url_active_page")
            or item.get("url_comeet_hosted_page")
            or item.get("url_recruit_hosted_page")
        )

        if item.get("is_internal") or not job_url:
            continue

        location = item.get("location") or {}
        description = plain_text(" ".join(
            detail["value"]
            for detail in item.get("details") or []
            if detail.get("value")
        ))

        jobs.append({
            "source": "comeet:thetaray",
            "source_job_id": str(item["uid"]),
            "company": "ThetaRay",
            "title": item["name"],
            "location": (
                location.get("name") if isinstance(location, dict) else None
            ),
            "url": job_url,
            **text_fields(description),
        })

    return as_jobs(jobs)


def fetch_deel_job(job_id, company, board):
    url = f"https://jobs.deel.com/{board}/job-details/{job_id}/overview"
    schema = extract_jobposting_jsonld(
        fetch_text(url, context=source_context(company, "deel"))
    )

    if not schema:
        return None

    # La fuente histórica del portal propio de Deel se conserva por
    # identidad estable; el resto de boards usan deel:<board>.
    source = (
        "deel:careers"
        if company == "Deel" and board == "deel"
        else f"deel:{board}"
    )

    return Job.from_mapping({
        "source": source,
        "source_job_id": job_id,
        "company": company,
        "title": schema.get("title") or "Untitled",
        "url": url,
        **_jsonld_fields(schema),
    })


def _fetch_deel_jobs(company, board, ids):
    if not ids:
        raise ValueError(f"{company}: no job IDs found")

    jobs = []
    failed_ids = []
    error_types = set()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_deel_job, job_id, company, board): job_id
            for job_id in ids
        }

        for future in as_completed(futures):
            try:
                job = future.result()
            except RECOVERABLE_FETCH_ERRORS as error:
                failed_ids.append(futures[future])
                error_types.add(type(error).__name__)
                continue

            if job is None:
                # Página sin JSON-LD (p. ej. un challenge con 200): el ID
                # sigue listado, así que tampoco es una oferta cerrada.
                failed_ids.append(futures[future])
                error_types.add("missing_jsonld")
            else:
                jobs.append(job)

    if not jobs:
        raise ValueError(
            f"{company}: no jobs parsed ({len(failed_ids)} failures)"
        )

    if not failed_ids:
        return jobs

    LOGGER.warning(
        "job_pages_failed company=%r source=deel failures=%s "
        "ids_total=%s error_types=%s failed_ids=%s",
        company,
        len(failed_ids),
        len(ids),
        ",".join(sorted(error_types)),
        ",".join(sorted(failed_ids)[:10]),
    )
    return IncompleteSnapshot(jobs, failed_ids=failed_ids)


def _deel_job_ids(page, pattern):
    return sorted(set(re.findall(pattern, page)))


def fetch_deel_company(company, board):
    page = fetch_text(
        f"https://jobs.deel.com/{board}",
        context=source_context(company, "deel"),
    )
    ids = _deel_job_ids(
        page,
        rf"/{re.escape(board)}/job-details/([0-9a-fA-F-]{{36}})",
    )
    return _fetch_deel_jobs(company, board, ids)


def fetch_deel():
    page = fetch_text(
        "https://www.deel.com/careers/",
        context=source_context("Deel", "deel"),
    )
    ids = _deel_job_ids(page, r"job-details/([0-9a-fA-F-]{36})")
    return _fetch_deel_jobs("Deel", "deel", ids)


def _ant_experience_range(posting):
    experience = posting.get("experience") or {}

    if not isinstance(experience, dict) or experience.get("from") is None:
        return None

    if experience.get("to") is not None:
        return f"{experience['from']}-{experience['to']} years of experience"

    return f"{experience['from']}+ years of experience"


def fetch_ant_group():
    company = "Ant Group / Ant International"
    page_index = 1
    postings = []

    while True:
        data = read_browser_json(
            "https://hrcareersweb.antgroup.com/api/social/position/search",
            context=source_context(company, "ant"),
            payload={
                "language": "en",
                "channel": "group_official_site",
                "categories": "",
                "key": "",
                "regions": "",
                "subCategories": "",
                "pageIndex": page_index,
                "pageSize": 10,
                "bgCode": "M7892",
            },
            headers={
                "Origin": "https://www.ant-intl.com",
                "Referer": "https://www.ant-intl.com/",
            },
        )

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

    jobs = []

    for posting in postings:
        job_id = str(posting.get("id") or "").strip()
        title = str(posting.get("name") or "").strip()

        if not job_id or not title:
            continue

        description = plain_text(" ".join(
            value
            for value in (
                posting.get("description"),
                posting.get("requirement"),
            )
            if value
        ))
        query = urlencode({"positionId": job_id, "locale": "US"})

        jobs.append({
            "source": "ant:careers",
            "source_job_id": job_id,
            "company": company,
            "title": title,
            "location": "; ".join(posting.get("workLocations") or []) or None,
            "url": f"https://talent.antgroup.com/off-campus-position?{query}",
            **text_fields(
                description,
                experience_fallback=_ant_experience_range(posting),
            ),
        })

    return as_jobs(jobs)


def fetch_mambu_job(job_id):
    url = f"https://careers-mambu.icims.com/jobs/{job_id}/job?in_iframe=1"
    page = fetch_text(url, context=source_context("Mambu", "icims"))
    title_match = re.search(
        r"<h1[^>]*>(.*?)</h1>",
        page,
        re.IGNORECASE | re.DOTALL,
    )

    if not title_match:
        return None

    description = plain_text(page)
    location_match = re.search(
        r"Job Locations?\s+(.+?)\s+(?:Posted Date|Job ID)",
        description,
        re.IGNORECASE,
    )

    return Job.from_mapping({
        "source": "icims:mambu",
        "source_job_id": str(job_id),
        "company": "Mambu",
        "title": plain_text(title_match.group(1)),
        "location": (
            location_match.group(1).strip() if location_match else None
        ),
        "url": url,
        **text_fields(description),
    })


def fetch_mambu():
    page = fetch_text(
        "https://careers-mambu.icims.com/jobs/search"
        "?ss=1&searchRelation=keyword_all&in_iframe=1",
        context=source_context("Mambu", "icims"),
    )
    ids = sorted(set(re.findall(r"/jobs/(\d+)/[^\"'<>\s]+", page)))

    if not ids:
        raise ValueError("Mambu: no job IDs found")

    jobs = [job for job in map(fetch_mambu_job, ids) if job]

    if not jobs:
        raise ValueError("Mambu: jobs found but none parsed")

    return jobs


def _caixabank_location(page_text):
    match = re.search(
        r"Ubicaci[oó]n:\s*(.+?)"
        r"(?=\s+(?:Jornada Laboral|Contrato|Vacantes):)",
        page_text,
        re.IGNORECASE,
    )

    if not match:
        return None

    raw_location = match.group(1).strip()
    cities = re.findall(
        r"\b(?:Barcelona|Madrid|Sevilla)\b",
        raw_location,
        re.IGNORECASE,
    )

    if not cities:
        return raw_location

    return "; ".join(dict.fromkeys(city.title() for city in cities))


def fetch_caixabank_tech():
    company = "CaixaBank Tech"
    context = source_context(company, "caixabank-tech")
    listing = fetch_text(
        "https://www.caixabanktech.com/es/join-us-es/",
        context=context,
    )
    job_urls = set()

    for raw_link in re.findall(
        r'''href=["']([^"']+)["']''',
        listing,
        re.IGNORECASE,
    ):
        link = unescape(raw_link).split("#", 1)[0].split("?", 1)[0]

        if link.startswith("/"):
            link = "https://www.caixabanktech.com" + link

        if re.fullmatch(
            r"https://(?:www\.)?caixabanktech\.com/es/job/[^/]+/?",
            link,
            re.IGNORECASE,
        ):
            job_urls.add(link.rstrip("/") + "/")

    if not job_urls:
        raise ValueError("CaixaBank Tech: no job links found")

    jobs = []

    for url in sorted(job_urls):
        page = fetch_text(url, context=context)
        title_match = re.search(
            r"<title[^>]*>\s*CaixaBank Tech\s*\|\s*(.*?)</title>",
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
            raise ValueError(f"CaixaBank Tech: title not found: {url}")

        page_text = plain_text(page)
        # La descripción empieza en "Buscamos personas"; sin ese marcador se
        # conserva el texto completo de la página.
        description_match = re.search(
            r"\bBuscamos personas\b",
            page_text,
            re.IGNORECASE,
        )

        jobs.append({
            "source": "caixabank-tech:careers",
            "source_job_id": url.rstrip("/").rsplit("/", 1)[-1],
            "company": company,
            "title": plain_text(title_match.group(1)),
            "location": _caixabank_location(page_text),
            "url": url,
            **text_fields(
                page_text[description_match.start():]
                if description_match
                else page_text
            ),
        })

    return as_jobs(jobs)

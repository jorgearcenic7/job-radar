from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from job_radar.domain import Job

from .ats import (
    fetch_ashby,
    fetch_bamboohr,
    fetch_eightfold,
    fetch_factorial,
    fetch_greenhouse,
    fetch_lever,
    fetch_personio,
    fetch_pinpoint,
    fetch_recruitee,
    fetch_smartrecruiters,
    fetch_successfactors,
    fetch_workable,
    fetch_workday,
)
from .custom import (
    fetch_ant_group,
    fetch_caixabank_tech,
    fetch_dassault_systemes,
    fetch_deel,
    fetch_deel_company,
    fetch_mambu,
    fetch_sage,
    fetch_teamtailor_company,
    fetch_thetaray,
    fetch_visma,
)


GREENHOUSE_COMPANIES = {
    "Typeform": "typeform",
    "N26": "n26",
    "Stripe": "stripe",
    "Adyen": "adyen",
    "Block (incl. Afterpay)": "block",
    "Chime": "chime",
    "Robinhood": "robinhood",
    "SoFi": "sofi",
    "Coinbase": "coinbase",
    "Datadog": "datadog",
    "Fever": "feverup",
    "Cabify": "cabify",
    "Aircall": "aircallioinc",
    "Auctane": "auctane",
    "Celonis": "celonis",
    "Taxbit": "taxbit",
    "Ebury": "ebury",
    "Lynx": "lynxtech",
    "Monzo": "monzo",
    "Make": "make",
    "Awin": "awin",
    "Blip Global": "blip-global",
    "OneTrust": "onetrust",
    "nCino": "ncinoinc",
    "Affirm": "affirm",
    "Raisin": "raisin",
    "Wallapop": "wallapop",
    "Databricks": "databricks",
    "Fivetran": "fivetran",
    "SumUp": "sumup",
    "Bitpanda": "bitpanda",
    "Elastic": "elastic",
    "GitLab": "gitlab",
    "Grafana Labs": "grafanalabs",
    "TheFork": "thefork",
    "Payhawk": "payhawkio",
}

ASHBY_COMPANIES = {
    "Pleo": "pleo",
    "Plaid": "plaid",
    "Qonto": "qonto",
    "Mollie": "mollie",
    "Capchase": "capchase",
    "Invopop": "invopop",
    "Airwallex": "airwallex",
    "Checkout.com": "checkout.com",
    "Rain": "rain",
    "Lovable": "lovable",
    "n8n": "n8n",
    "ClickHouse": "clickhouse",
    "Ashby": "ashby",
    "StackAI": "stack-ai",
    "Camunda": "camunda",
    "Supabase": "supabase",
    "Preply": "preply",
    "Perk (TravelPerk)": "perk",
    "Docplanner (Doctoralia)": "docplanner",
    "Pennylane": "pennylane",
    "Kraken": "kraken.com",
    "Alan": "alan",
    "Satispay": "satispay",
    "ElevenLabs": "elevenlabs",
    "Oyster HR": "oyster",
    "Amenitiz": "amenitiz",
    "Pliant": "pliant",
    "Mistral AI": "mistral.ai",
    "Prefect": "prefect",
    "PostHog": "posthog",
    "Primer": "primer",
    "Nubank": "nubank",
}

WORKDAY_COMPANIES = {
    "Mastercard": (
        "mastercard.wd1.myworkdayjobs.com",
        "mastercard",
        "CorporateCareers",
    ),
    "BBVA": ("bbva.wd3.myworkdayjobs.com", "bbva", "BBVA"),
    "Santander": (
        "santander.wd3.myworkdayjobs.com",
        "santander",
        "SantanderCareers",
    ),
    "Amadeus": ("amadeus.wd502.myworkdayjobs.com", "amadeus", "jobs"),
    "AVEVA": ("aveva.wd3.myworkdayjobs.com", "aveva", "AVEVA_careers"),
}

SMARTRECRUITERS_COMPANIES = {
    "IFS": "IFS1",
    "Wise": "Wise",
    "Grab / Grab Financial Group": "Grab",
}

LEVER_COMPANIES = {
    "Paytm": "paytm",
    "Jobandtalent": "jobandtalent",
    "Metabase": "metabase",
    "Tinybird": "tinybird",
}
DEEL_COMPANIES = {"Klarna": "klarna"}
BAMBOOHR_COMPANIES = {"Flutterwave": "flutterwavego"}
EIGHTFOLD_COMPANIES = {"PayPal": ("paypal.eightfold.ai", "paypal.com")}
SUCCESSFACTORS_COMPANIES = {
    "SAP": (
        "career5.successfactors.eu",
        "SAP",
        "https://jobs.sap.com/search/?q={job_id}",
    ),
    "Hexagon": (
        "career74.sapsf.eu",
        "HexagonGlobP",
        "https://careers.hexagon.com/job/{slug}/{job_id}-en_US/",
    ),
}

RECRUITEE_COMPANIES = {"SeQura": "sequra", "Holded": "holded"}
PERSONIO_COMPANIES = {
    "Indexa Capital": "talento-indexa-capital.jobs.personio.com",
}
PINPOINT_COMPANIES = {"CARTO": "carto"}
WORKABLE_COMPANIES = {"Kantox": "kantox", "Yapily": "yapily"}
TEAMTAILOR_COMPANIES = {
    "Freepik": ("https://careers.freepik.com", "teamtailor:freepik"),
    "PayFit": ("https://careers.payfit.com", "teamtailor:payfit"),
}


@dataclass(frozen=True, slots=True)
class FunctionConnector:
    company: str
    source: str
    fetcher: Callable[[], list[Job]]
    required: bool = True
    catch_all: bool = False

    def fetch(self) -> list[Job]:
        return self.fetcher()


def _configured(
    source: str,
    companies: dict[str, object],
    fetcher: Callable[..., list[Job]],
) -> list[FunctionConnector]:
    connectors = []

    for company, config in companies.items():
        args = config if isinstance(config, tuple) else (config,)
        connectors.append(
            FunctionConnector(
                company=company,
                source=source,
                fetcher=partial(fetcher, company, *args),
            )
        )

    return connectors


def _catch_all(
    company: str,
    source: str,
    fetcher: Callable[[], list[Job]],
) -> FunctionConnector:
    return FunctionConnector(company, source, fetcher, catch_all=True)


def _teamtailor(company: str, base_url: str, source: str) -> FunctionConnector:
    return _catch_all(
        company,
        "teamtailor",
        partial(fetch_teamtailor_company, company, base_url, source),
    )


CONFIGURED_SOURCES = (
    ("greenhouse", GREENHOUSE_COMPANIES, fetch_greenhouse),
    ("ashby", ASHBY_COMPANIES, fetch_ashby),
    ("workday", WORKDAY_COMPANIES, fetch_workday),
    ("smartrecruiters", SMARTRECRUITERS_COMPANIES, fetch_smartrecruiters),
    ("lever", LEVER_COMPANIES, fetch_lever),
    ("deel", DEEL_COMPANIES, fetch_deel_company),
    ("bamboohr", BAMBOOHR_COMPANIES, fetch_bamboohr),
    ("eightfold", EIGHTFOLD_COMPANIES, fetch_eightfold),
    ("successfactors", SUCCESSFACTORS_COMPANIES, fetch_successfactors),
    ("recruitee", RECRUITEE_COMPANIES, fetch_recruitee),
    ("personio", PERSONIO_COMPANIES, fetch_personio),
    ("pinpoint", PINPOINT_COMPANIES, fetch_pinpoint),
    ("workable", WORKABLE_COMPANIES, fetch_workable),
    ("teamtailor", TEAMTAILOR_COMPANIES, fetch_teamtailor_company),
)

# Portales propios o ATS con una sola empresa: un fallo se aísla por fuente.
CATCH_ALL_CONNECTORS = (
    _catch_all(
        "Ant Group / Ant International",
        "portal propio",
        fetch_ant_group,
    ),
    _teamtailor(
        "Spendesk",
        "https://career.spendesk.com",
        "teamtailor:spendesk",
    ),
    _catch_all("ThetaRay", "comeet", fetch_thetaray),
    _catch_all("Deel", "portal propio", fetch_deel),
    _catch_all("Mambu", "icims", fetch_mambu),
    _teamtailor("Seedtag", "https://jobs.seedtag.com", "teamtailor:seedtag"),
    _teamtailor(
        "Lingokids",
        "https://jobs.lingokids.com",
        "teamtailor:lingokids",
    ),
    _catch_all("CaixaBank Tech", "portal propio", fetch_caixabank_tech),
    _catch_all("Dassault Systèmes", "portal propio", fetch_dassault_systemes),
    _catch_all("Visma", "portal propio", fetch_visma),
    _catch_all("Sage", "sagepeople", fetch_sage),
    # Factorial no expone API ni datos estructurados: se lee el HTML de
    # la página de empleo, así que cualquier cambio de maqueta se aísla.
    _catch_all(
        "Embat",
        "factorial",
        partial(fetch_factorial, "Embat", "embat.factorialhr.com"),
    ),
)

CONNECTORS = (
    *(
        connector
        for source, companies, fetcher in CONFIGURED_SOURCES
        for connector in _configured(source, companies, fetcher)
    ),
    *CATCH_ALL_CONNECTORS,
)

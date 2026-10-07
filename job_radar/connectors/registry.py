from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from job_radar.domain import Job

from .ats import (
    fetch_ashby,
    fetch_bamboohr,
    fetch_eightfold,
    fetch_greenhouse,
    fetch_lever,
    fetch_smartrecruiters,
    fetch_successfactors,
    fetch_workday,
)
from .custom import (
    fetch_ant_group,
    fetch_caixabank_tech,
    fetch_dassault_systemes,
    fetch_deel,
    fetch_deel_company,
    fetch_lingokids,
    fetch_mambu,
    fetch_revolut,
    fetch_sage,
    fetch_seedtag,
    fetch_spendesk,
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
    "Nubank": "nubank",
    "Robinhood": "robinhood",
    "SoFi": "sofi",
    "Coinbase": "coinbase",
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
    "Make": "make",
    "Awin": "awin",
    "Blip Global": "blip-global",
    "OneTrust": "onetrust",
    "nCino": "ncinoinc",
    "Affirm": "affirm",
    "Raisin": "raisin",
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

LEVER_COMPANIES = {"Paytm": "paytm"}
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


CONNECTORS = tuple(
    _configured("greenhouse", GREENHOUSE_COMPANIES, fetch_greenhouse)
    + _configured("ashby", ASHBY_COMPANIES, fetch_ashby)
    + _configured("workday", WORKDAY_COMPANIES, fetch_workday)
    + _configured(
        "smartrecruiters",
        SMARTRECRUITERS_COMPANIES,
        fetch_smartrecruiters,
    )
    + _configured("lever", LEVER_COMPANIES, fetch_lever)
    + _configured("deel", DEEL_COMPANIES, fetch_deel_company)
    + _configured("bamboohr", BAMBOOHR_COMPANIES, fetch_bamboohr)
    + _configured("eightfold", EIGHTFOLD_COMPANIES, fetch_eightfold)
    + _configured(
        "successfactors",
        SUCCESSFACTORS_COMPANIES,
        fetch_successfactors,
    )
    + [
        FunctionConnector(
            "Ant Group / Ant International",
            "portal propio",
            fetch_ant_group,
            catch_all=True,
        ),
        FunctionConnector(
            "Spendesk", "teamtailor", fetch_spendesk,
            catch_all=True,
        ),
        FunctionConnector(
            "ThetaRay", "comeet", fetch_thetaray,
            catch_all=True,
        ),
        FunctionConnector(
            "Deel", "portal propio", fetch_deel,
            catch_all=True,
        ),
        FunctionConnector(
            "Mambu", "icims", fetch_mambu,
            catch_all=True,
        ),
        FunctionConnector(
            "Seedtag", "teamtailor", fetch_seedtag,
            catch_all=True,
        ),
        FunctionConnector(
            "Lingokids", "teamtailor", fetch_lingokids,
            catch_all=True,
        ),
        FunctionConnector(
            "Revolut",
            "portal propio",
            fetch_revolut,
            required=False,
            catch_all=True,
        ),
        FunctionConnector(
            "CaixaBank Tech",
            "portal propio",
            fetch_caixabank_tech,
            catch_all=True,
        ),
        FunctionConnector(
            "Dassault Systèmes",
            "portal propio",
            fetch_dassault_systemes,
            catch_all=True,
        ),
        FunctionConnector(
            "Visma", "portal propio", fetch_visma,
            catch_all=True,
        ),
        FunctionConnector(
            "Sage", "sagepeople", fetch_sage,
            catch_all=True,
        ),
    ]
)

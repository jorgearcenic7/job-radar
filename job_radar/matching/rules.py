import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from job_radar.domain import Job


MATCH_RULES_VERSION = 2


class MatchReasonCode(StrEnum):
    CORE_DATA_ROLE = "CORE_DATA_ROLE"
    TECHNICAL_DATA_ANALYST = "TECHNICAL_DATA_ANALYST"
    TECHNICAL_ML_ROLE = "TECHNICAL_ML_ROLE"
    DATA_HEAVY_ADJACENT_ROLE = "DATA_HEAVY_ADJACENT_ROLE"
    EXPERIENCE_COMPATIBLE = "EXPERIENCE_COMPATIBLE"
    THREE_YEARS_STRETCH = "THREE_YEARS_STRETCH"
    MID_LEVEL_STRETCH = "MID_LEVEL_STRETCH"
    LEVEL_III_STRETCH = "LEVEL_III_STRETCH"
    JUNIOR_LEVEL = "JUNIOR_LEVEL"
    LEVEL_I_II = "LEVEL_I_II"
    LEVEL_UNSPECIFIED = "LEVEL_UNSPECIFIED"
    STRONG_DATA_SIGNALS = "STRONG_DATA_SIGNALS"


@dataclass(frozen=True, slots=True)
class MatchResult:
    status: str
    level: str
    reason: str
    reason_codes: tuple[MatchReasonCode, ...]
    rules_version: int


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


# =========================================================
# UBICACIÓN
# =========================================================

_SPAIN = re.compile(r"\b(?:spain|españa)\b")
_REMOTE = re.compile(
    r"\b(?:remote|remotely|remoto|remota|teletrabajo|distributed|anywhere|"
    r"work from (?:anywhere|home))\b"
)
_OPEN_TO_EUROPE = re.compile(r"\b(?:europe|europa|european union|eu|emea)\b")
# infer_countries no detecta "Remote - US" porque solo reconoce códigos
# separados por comas o paréntesis.
_RESTRICTED_REGION = re.compile(
    r"(?<![a-z])(?:us|usa|u\.s\.(?:a\.)?|united states|canada|"
    r"latam|latin america|americas|north america|south america|"
    r"apac|asia|india)(?![a-z])"
)


def matches_target_location(location):
    value = normalize(str(location or ""))

    if not value:
        return False

    # España en cualquier parte, también en ubicaciones múltiples:
    # "Madrid, Spain; Remote - US"
    if _SPAIN.search(value):
        return True

    # Los códigos ES/ESP los resuelve infer_countries, que además descarta
    # un "ES" regional junto a otro país ("Vitória, ES, Brazil").
    countries = infer_countries(location)

    if "Spain" in countries:
        return True

    if not _REMOTE.search(value):
        return False

    # Remoto abierto a una región que incluye España.
    if _OPEN_TO_EUROPE.search(value):
        return True

    # Remoto limitado a otro país o a una región sin España.
    if countries:
        return False

    return not _RESTRICTED_REGION.search(value)


# =========================================================
# SALARIO
# =========================================================

# El prefijo de país se conserva ("R$900", "CA$120,000"): sin él, el texto
# devuelto atribuiría la cifra a otra moneda.
_CURRENCY = (
    r"(?:€|(?:USD|US|CAD|CA|AUD|AU|NZ|HK|SG|R|A|C|S)?\$|£|"
    r"EUR\b|USD\b|GBP\b)"
)
_AMOUNT = (
    r"(?:"
    r"\d{1,3}(?:[.,   ]\d{3})+|"
    r"\d{4,6}|"
    r"\d{1,3}(?:[.,]\d{1,2})?\s?[kK]|"
    r"\d{2,3}"
    r")"
)
_SALARY_VALUE = rf"(?:{_CURRENCY}\s*)?{_AMOUNT}(?:\s*{_CURRENCY})?"
_SALARY_PERIOD = (
    r"(?:\s*(?:"
    r"/(?:year|yr|annum|month|mo|hour|hr|day|week)|"
    r"per\s+(?:year|annum|month|hour|day|week)|"
    r"a\s+year|annually|yearly|monthly|weekly|daily|hourly|"
    r"p\.?\s*a\.?)"
    r")?"
)
_SALARY_RANGE = re.compile(
    rf"(?<![\w.,]){_SALARY_VALUE}(?![\d.,])\s*(?:[-–—]|\bto\b)\s*"
    rf"{_SALARY_VALUE}(?![\d.,]){_SALARY_PERIOD}(?!\w)",
    re.IGNORECASE,
)
_SALARY_SINGLE = re.compile(
    rf"(?<![\w.,]){_SALARY_VALUE}(?![\d.,]){_SALARY_PERIOD}(?!\w)",
    re.IGNORECASE,
)
_HAS_CURRENCY = re.compile(_CURRENCY, re.IGNORECASE)

# Palabras que, junto a una cifra con moneda, indican que no es el salario:
# ventajas ("home office setup budget up to €1,000", "Meal Card: R$900"),
# topes legales ("$6,800 monthly wage ceiling") o datos de negocio
# ("$250 - $1B revenue"). "Budget:" como etiqueta sí anuncia el salario.
_NON_SALARY_CONTEXT = re.compile(
    r"\b(?:"
    r"stipend|allowance|budget(?!\s*:)|set[- ]?up|home office|meal|card|"
    r"voucher|reimburs\w*|revenue|funding|raised|valuation|ceiling|"
    r"referral|gym|wellness|equipment"
    r")\b",
    re.IGNORECASE,
)
# Una etiqueta salarial justo antes de la cifra prevalece sobre el contexto
# anterior: "Salary: £42,180 + Car Allowance £6,000".
_SALARY_LABEL = re.compile(
    r"\b(?:"
    r"salary|pay|compensation|base|ote|rate|"
    r"sueldo|salario|retribuci[oó]n|remuneraci[oó]n"
    r")\b",
    re.IGNORECASE,
)
_SALARY_CONTEXT_CHARS = 40
_SENTENCE_BREAK = re.compile(r"[!?;\n•]|\.\s")
_SALARY_INTERVAL = re.compile(
    r"/\s*\w|\bper\b|\ba\s+year\b|"
    r"\b(?:annually|yearly|monthly|weekly|daily|hourly)\b|"
    r"\bp\.?\s*a\b",
    re.IGNORECASE,
)
_THOUSANDS = re.compile(r"\d\s?k\b", re.IGNORECASE)


def _in_non_salary_context(description, start, end):
    """
    Indica si la frase de la cifra habla de ventajas o de negocio sin una
    etiqueta salarial justo antes.
    """

    before = description[max(0, start - _SALARY_CONTEXT_CHARS):start]
    before = _SENTENCE_BREAK.split(before)[-1]
    after = description[end:end + _SALARY_CONTEXT_CHARS]
    after = _SENTENCE_BREAK.split(after)[0]

    return not _SALARY_LABEL.search(before) and bool(
        _NON_SALARY_CONTEXT.search(f"{before} {after}")
    )


def _is_small_amount_without_interval(salary):
    # Una cifra suelta de 2-3 dígitos sin periodo ("$500") no es un sueldo;
    # con periodo sí lo es ("$25/hour", "$500/day").
    if _SALARY_INTERVAL.search(salary) or _THOUSANDS.search(salary):
        return False

    return int(re.sub(r"\D", "", salary)) < 1000


def extract_salary(description):
    description = description or ""

    for pattern in (_SALARY_RANGE, _SALARY_SINGLE):
        for match in pattern.finditer(description):
            salary = match.group(0).strip()

            if not _HAS_CURRENCY.search(salary):
                continue

            if _in_non_salary_context(description, match.start(), match.end()):
                continue

            if (
                pattern is _SALARY_SINGLE
                and _is_small_amount_without_interval(salary)
            ):
                continue

            return salary

    return None


# =========================================================
# EXPERIENCIA
# =========================================================

_EXPERIENCE_QUALIFIER = (
    r"(?:relevant\s+|professional\s+|hands-on\s+|industry\s+)?"
)
_EXPERIENCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:at least|minimum(?: of)?|min\.?)?\s*"
        r"\d{1,2}\+?\s+years?\s+(?:of\s+)?"
        + _EXPERIENCE_QUALIFIER
        + r"experience\b",
        r"\b\d{1,2}\s*(?:-|–|—|to)\s*\d{1,2}\s+years?"
        r"\s+(?:of\s+)?(?:relevant\s+|professional\s+)?experience\b",
        r"\b\d{1,2}\+?\s+years['’]?\s+experience\b",
        r"\b(?:mínimo|minimo|al menos)?\s*"
        r"\d{1,2}\+?\s+años?\s+de\s+experiencia\b",
    )
)
# Más de 15 años no es un requisito plausible: suele ser la antigüedad de
# la empresa.
_MAX_PLAUSIBLE_YEARS = 15
_COMPANY_CONTEXT_CHARS = 60
_COMPANY_EXPERIENCE = re.compile(
    r"\b(?:"
    r"for over|founded|since|we have|we've|our|"
    r"company|history|track record|"
    r"nuestra|nuestro|empresa|trayectoria"
    r")\b",
    re.IGNORECASE,
)

# El grupo 1 es el mínimo exigido; gana el primer patrón que coincida.
_REQUIRED_YEARS_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "at least 3 years", "minimum of 3 years"
        r"\b(?:at least|minimum(?: of)?|min\.?)\s*"
        r"(\d{1,2})\+?\s*(?:years?|yrs?)\b",
        # "3-5 years", "3 to 5 years"
        r"\b(\d{1,2})\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years?|yrs?)\b",
        # "7+ years"
        r"\b(\d{1,2})\+\s*(?:years?|yrs?)\b",
        # "2 years of experience", "5 years' experience"
        r"\b(\d{1,2})\s*(?:years?|yrs?)['’]?\s+(?:of\s+)?"
        + _EXPERIENCE_QUALIFIER
        + r"experience\b",
        # "mínimo 3 años de experiencia"
        r"\b(?:mínimo|minimo|al menos)?\s*"
        r"(\d{1,2})\+?\s*años?\s+de\s+experiencia\b",
    )
)


def extract_experience(description):
    description = description or ""

    matches = sorted(
        (
            match
            for pattern in _EXPERIENCE_PATTERNS
            for match in pattern.finditer(description)
        ),
        key=lambda match: match.start(),
    )

    for match in matches:
        years = int(re.search(r"\d{1,2}", match.group(0)).group(0))

        if years > _MAX_PLAUSIBLE_YEARS:
            continue

        if _is_company_experience(description, match.start()):
            continue

        return match.group(0).strip()

    return None


def _is_company_experience(description, start):
    """
    Detecta menciones de años que describen a la empresa, no al candidato:
    "for over 20 years", "our track record"...

    Solo mira la frase actual dentro de los 60 caracteres previos.
    """

    context = description[max(0, start - _COMPANY_CONTEXT_CHARS):start]
    sentence = re.split(r"[.!?;\n•]", context)[-1]

    return bool(_COMPANY_EXPERIENCE.search(sentence))


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

    for pattern in _REQUIRED_YEARS_PATTERNS:
        match = pattern.search(value)

        if match:
            return int(match.group(1))

    return None


# =========================================================
# CLASIFICACIÓN
# =========================================================

# Título y descripción se comparan ya normalizados (en minúsculas).
_EXCLUDED_TITLES = (
    # Early career.
    re.compile(
        r"\b(?:intern|internship|working student|student|graduate|"
        r"new grad|new graduate|trainee|apprentice|apprenticeship)\b"
    ),
    # Seniority por encima del perfil.
    re.compile(
        r"\b(?:senior|sr|staff|principal|lead|head|manager|director|"
        r"architect|distinguished|expert|vice president|vp)\b"
    ),
    # III / 3 se permiten; IV / 4 o superior, no.
    re.compile(r"\b(?:iv|v|vi|vii|viii|ix|x|[4-9])\b"),
)

_CORE_DATA_ROLE = re.compile(
    r"\b(?:data engineer(?:ing)?|analytics engineer(?:ing)?|"
    r"data platform engineer|data infrastructure engineer|"
    r"data warehouse engineer|etl engineer|elt engineer|dataops engineer|"
    r"bi engineer|business intelligence engineer)\b"
)
_DATA_ANALYST_ROLE = re.compile(
    r"\b(?:data analyst|product data analyst|analytics analyst|"
    r"business intelligence analyst)\b"
)
_DATA_SCIENCE_ROLE = re.compile(
    r"\b(?:data scientist|machine learning engineer|ml engineer|"
    r"mlops engineer|applied ai engineer|ai engineer)\b"
)
_ADJACENT_ENGINEERING_ROLE = re.compile(
    r"\b(?:platform engineer|software engineer|backend engineer|"
    r"cloud engineer|devops engineer)\b"
)

_JUNIOR_LEVEL = re.compile(r"\b(?:junior|jr|associate|entry)\b")
_LEVEL_I_II = re.compile(r"\b(?:engineer\s+(?:i|ii)|level\s*[12])\b")
_MID_LEVEL = re.compile(r"\b(?:mid|mid-level|intermediate)\b")
_LEVEL_III = re.compile(r"\b(?:iii|level\s*3)\b")


def _word_patterns(*words):
    return tuple(re.compile(rf"\b{re.escape(word)}\b") for word in words)


_STRONG_DATA_SIGNALS = tuple(
    re.compile(signal)
    for signal in (
        r"\bdata pipelines?\b|\bpipelines?\s+de\s+datos\b",
        r"\b(?:etl|elt)\b|extract.{0,30}transform.{0,30}load",
        r"\bdata ingestion\b|\bingesta de datos\b",
        r"\bdata warehouse\b|\bdata lake\b|\blakehouse\b",
        r"\bdata model(?:ing|ling)\b|\bmodelado de datos\b",
        r"\b(?:batch|stream) processing\b",
        r"\b(?:spark|pyspark|databricks)\b",
        r"\b(?:airflow|dbt)\b",
        r"\b(?:kafka|event streaming)\b",
        r"\b(?:snowflake|bigquery|redshift|microsoft fabric)\b",
    )
)
_TECHNICAL_SIGNALS = _word_patterns(
    "sql", "python", "java", "scala", "azure", "aws", "gcp", "terraform",
    "kubernetes", "docker",
)
_ML_SIGNALS = _word_patterns(
    "machine learning", "deep learning", "nlp", "mlops", "model deployment",
    "model training", "feature engineering", "python", "spark", "databricks",
)


def _count_signals(signals, text):
    return sum(1 for signal in signals if signal.search(text))


def classify(job: Job) -> MatchResult | None:
    """
    JOB RADAR - REGLAS GLOBALES DE COINCIDENCIA

    PERFIL OBJETIVO
    ----------------
    Experiencia actual aproximada: 1 año.

    Roles prioritarios: Data Engineer, Analytics Engineer, Data Platform /
    Infrastructure / Warehouse Engineer, ETL / ELT Engineer, DataOps
    Engineer y BI Engineer.

    También mostramos como STRETCH:
    - Mid / III
    - hasta 3 años requeridos
    - Data Analyst / Product Data Analyst técnico
    - Data Scientist técnico
    - ML / MLOps / Applied AI
    - Platform / Software Engineer con fuerte componente Data

    NO queremos:
    - Internships, Graduate / New Grad, Trainee / Apprentice, Working Student
    - Senior, Staff, Principal, Lead, Manager, Director, Architect
    - 4+ años mínimos de experiencia
    - nivel IV / 4 o superior
    - ubicaciones fuera de España que no sean remotas

    RESULTADOS
    ----------
    Buena coincidencia: claramente alineado y nivel compatible.
    Stretch: algo por encima o rol adyacente pero razonable.
    None: fuera de objetivo.
    """

    if not matches_target_location(job.location):
        return None

    title = normalize(job.title)

    if any(pattern.search(title) for pattern in _EXCLUDED_TITLES):
        return None

    experience_text = (
        job.experience_text or extract_experience(job.description)
    )
    years = required_experience_years(experience_text)

    # 0-2 años -> objetivo; 3 años -> stretch; 4 o más -> fuera.
    if years is not None and years >= 4:
        return None

    description = normalize(job.description)
    strong_data_count = _count_signals(_STRONG_DATA_SIGNALS, description)
    role = _match_role(title, description, strong_data_count)

    if role is None:
        return None

    role_code, role_reason = role

    if role_code is MatchReasonCode.CORE_DATA_ROLE:
        status, level, level_codes = _core_level(title, years)
    else:
        # Los roles adyacentes siempre son Stretch.
        status = "Stretch"
        level = "Rol técnico adyacente razonable para el perfil"
        level_codes = []

    reason_codes = [role_code, *level_codes]
    details = [role_reason]

    if strong_data_count:
        reason_codes.append(MatchReasonCode.STRONG_DATA_SIGNALS)
        details.append(
            f"{strong_data_count} señales fuertes de Data Engineering"
        )

    if experience_text:
        details.append(f"Experiencia publicada: {experience_text}")

    return MatchResult(
        status=status,
        level=level,
        reason=" | ".join(details),
        reason_codes=tuple(reason_codes),
        rules_version=MATCH_RULES_VERSION,
    )


def _match_role(title, description, strong_data_count):
    """
    Familia del rol como (reason code, motivo), o None si no encaja. Solo
    cuenta la primera familia que aparece en el título.
    """

    if _CORE_DATA_ROLE.search(title):
        return (
            MatchReasonCode.CORE_DATA_ROLE,
            "Rol directamente alineado con Data Engineering",
        )

    if _DATA_ANALYST_ROLE.search(title):
        # Queremos analistas técnicos, no puestos puramente business.
        technical_count = _count_signals(_TECHNICAL_SIGNALS, description)

        if strong_data_count >= 1 or technical_count >= 2:
            return (
                MatchReasonCode.TECHNICAL_DATA_ANALYST,
                "Data Analyst con contenido técnico relevante",
            )

        return None

    if _DATA_SCIENCE_ROLE.search(title):
        ml_count = _count_signals(_ML_SIGNALS, description)

        if ml_count >= 2 or strong_data_count >= 1:
            return (
                MatchReasonCode.TECHNICAL_ML_ROLE,
                "Data Science / ML con contenido técnico relevante",
            )

        return None

    # Software / Platform solo interesa si realmente hay mucho Data
    # Engineering.
    if _ADJACENT_ENGINEERING_ROLE.search(title) and strong_data_count >= 3:
        return (
            MatchReasonCode.DATA_HEAVY_ADJACENT_ROLE,
            f"Rol de ingeniería con {strong_data_count} señales "
            "fuertes de Data Engineering",
        )

    return None


def _core_level(title, years):
    """Estado, nivel y reason codes de un rol core con 0-3 años o sin años."""

    mid_level = _MID_LEVEL.search(title)
    level_iii = _LEVEL_III.search(title)

    if years == 3 or mid_level or level_iii:
        codes = []

        if years == 3:
            codes.append(MatchReasonCode.THREE_YEARS_STRETCH)

        if level_iii:
            codes.append(MatchReasonCode.LEVEL_III_STRETCH)
        elif mid_level:
            codes.append(MatchReasonCode.MID_LEVEL_STRETCH)

        return "Stretch", "Mid / III / hasta 3 años: stretch razonable", codes

    if years is not None and years <= 2:
        return (
            "Buena coincidencia",
            f"Experiencia compatible: {years} año(s)",
            [MatchReasonCode.EXPERIENCE_COMPATIBLE],
        )

    junior_level = _JUNIOR_LEVEL.search(title)
    level_i_ii = _LEVEL_I_II.search(title)

    if junior_level or level_i_ii:
        codes = []

        if junior_level:
            codes.append(MatchReasonCode.JUNIOR_LEVEL)

        if level_i_ii:
            codes.append(MatchReasonCode.LEVEL_I_II)

        return (
            "Buena coincidencia",
            "Nivel inicial/intermedio compatible",
            codes,
        )

    # Data Engineer sin nivel ni años: lo mostramos, pero no asumimos que sea
    # junior.
    return (
        "Stretch",
        "Rol muy alineado, pero nivel no especificado",
        [MatchReasonCode.LEVEL_UNSPECIFIED],
    )


# =========================================================
# GEOGRAFÍA
# =========================================================

# Cargados bajo demanda por _prepare_geography().
_COUNTRY_ALIASES = None
_CITY_COUNTRIES = None
_US_CITY_NAMES = None

_UNITED_STATES = "United States"

# Abreviaturas USPS. Muchas son también códigos ISO de país ("Atlanta, GA"
# no es Gabón ni "Chicago, IL" Israel), así que junto a una ciudad de EE. UU.
# se interpretan como estado.
_US_STATE_CODES = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn",
    "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
    "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa",
    "wv", "wi", "wy",
})

# Nombres de estado inequívocos: "Florida" no es la ciudad cubana. Georgia
# se trata aparte porque también es un país.
_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
)
# El más largo primero: "west virginia" antes que "virginia".
_US_STATE_NAME = re.compile(
    r"(?<![a-z])(?:"
    + "|".join(
        re.escape(state)
        for state in sorted(_US_STATE_NAMES, key=len, reverse=True)
    )
    + r")(?![a-z])"
)
_US_STATE_SHARED_WITH_COUNTRY = "Georgia"

_LOCATION_PIECE_SEPARATORS = re.compile(r"[,/()]")
_WORK_MODE_WORDS = re.compile(
    r"\b(?:remote|office|hybrid|onsite|on-site|based)\b"
)


def _geo_normalize(value):
    """Como normalize(), pero sin acentos: "Bogotá" -> "bogota"."""

    ascii_value = (
        unicodedata.normalize("NFKD", value or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    return normalize(ascii_value)


def _prepare_geography():
    global _COUNTRY_ALIASES
    global _CITY_COUNTRIES
    global _US_CITY_NAMES

    if _COUNTRY_ALIASES is not None:
        return

    import geonamescache

    gc = geonamescache.GeonamesCache()

    aliases = {}
    code_to_name = {}

    for code, data in gc.get_countries().items():
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
        # Sin este alias, "ireland" dentro de "Northern Ireland" daría
        # Irlanda en lugar de Reino Unido.
        "northern ireland": "United Kingdom",
        "bosnia & herzegovina": "Bosnia and Herzegovina",
    })

    # Ante ciudades homónimas gana la más poblada.
    city_candidates = {}
    us_city_names = set()

    for city in gc.get_cities().values():
        country = code_to_name.get((city.get("countrycode") or "").upper())

        if not country:
            continue

        population = city.get("population") or 0

        for city_name in {city.get("name"), city.get("ascii")}:
            if not city_name:
                continue

            key = _geo_normalize(city_name)

            if country == _UNITED_STATES:
                us_city_names.add(key)

            previous = city_candidates.get(key)

            if previous is None or population > previous[0]:
                city_candidates[key] = (population, country)

    _COUNTRY_ALIASES = aliases
    _US_CITY_NAMES = frozenset(us_city_names)
    _CITY_COUNTRIES = {
        key: country for key, (_, country) in city_candidates.items()
    }


def _city_keys(name):
    # geonames escribe "St. Louis"; las ofertas, a menudo "Saint Louis".
    yield name

    if name.startswith("saint "):
        yield "st. " + name.removeprefix("saint ")


def _city_country(name):
    """País de la ciudad más poblada con ese nombre, si existe."""

    for key in _city_keys(name):
        country = _CITY_COUNTRIES.get(key)

        if country:
            return country

    return None


def _is_us_city(name, alternative_country):
    """
    Indica si `name` debe leerse como ciudad de EE. UU. frente a
    `alternative_country`.

    Cuenta la ciudad más poblada con ese nombre, y también una homónima de
    EE. UU. cuando la más poblada no está en `alternative_country`: así
    "Birmingham, AL" es Alabama y no Albania, pero "Panama City, PA" sigue
    siendo Panamá.
    """

    best = _city_country(name)

    if best == _UNITED_STATES:
        return True

    return best != alternative_country and any(
        key in _US_CITY_NAMES for key in _city_keys(name)
    )


def _explicit_country_names(normalized):
    """
    Países escritos completos. Gana la coincidencia más larga para que
    "Papua New Guinea" no añada Guinea ni "Northern Ireland" Irlanda.
    """

    found = []

    for alias, country in _COUNTRY_ALIASES.items():
        if len(alias) <= 3:
            continue

        for match in re.finditer(
            r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])",
            normalized,
        ):
            found.append((match.start(), match.end(), country))

    # Las más largas primero; a igual longitud se conserva el orden.
    found.sort(key=lambda item: item[1] - item[0], reverse=True)

    accepted = []
    countries = []

    for start, end, country in found:
        if any(
            start < other_end and other_start < end
            for other_start, other_end in accepted
        ):
            continue

        accepted.append((start, end))

        if country not in countries:
            countries.append(country)

    return countries


def _beside_us_city(pieces, piece, alternative_country):
    """Indica si otra pieza del segmento es una ciudad de EE. UU."""

    return any(
        _is_us_city(other, alternative_country)
        for other in pieces
        if other != piece
    )


def _explicit_countries(segment):
    """
    Países escritos o codificados en un segmento de ubicación. Las ciudades
    solo se consultan para decidir si un código o "Georgia" es un estado de
    EE. UU.
    """

    pieces = [
        _geo_normalize(piece)
        for piece in _LOCATION_PIECE_SEPARATORS.split(segment)
        if piece.strip()
    ]

    # Un estado de EE. UU. escrito completo indica EE. UU. y no debe
    # leerse como país: "New Jersey" no es Jersey ni "New Mexico" México.
    normalized, states_named = _US_STATE_NAME.subn(
        " ", _geo_normalize(segment)
    )

    # 1. País completo escrito en el texto
    names = _explicit_country_names(normalized)

    # 2. Código ISO independiente: "Madrid, ES", "Berlin, DE", "Porto, PT"
    codes = [
        (piece, _COUNTRY_ALIASES[piece])
        for piece in pieces
        if len(piece) <= 3 and piece in _COUNTRY_ALIASES
    ]

    us_explicit = (
        states_named > 0
        or _UNITED_STATES in names
        or any(country == _UNITED_STATES for _, country in codes)
    )

    # "Atlanta, Georgia" o "Remote, US, Georgia" es el estado.
    georgia = _US_STATE_SHARED_WITH_COUNTRY

    if georgia in names and (
        us_explicit
        or _beside_us_city(pieces, georgia.casefold(), georgia)
    ):
        names.remove(georgia)

    countries = list(names)

    for piece, country in codes:
        if piece in _US_STATE_CODES and (
            us_explicit
            or _beside_us_city(pieces, piece, country)
        ):
            country = _UNITED_STATES

        # Un país escrito completo prevalece sobre códigos de dos letras del
        # mismo segmento, que suelen ser regiones: "India, MH, Pune" es
        # Maharashtra y no Islas Marshall; "Vitória, ES, Brazil", Espírito
        # Santo y no España. US y UK no son abreviaturas regionales
        # ("US / Canada").
        if (
            names
            and len(piece) == 2
            and piece not in {"us", "uk"}
            and country not in names
        ):
            continue

        if country not in countries:
            countries.append(country)

    if not countries and states_named:
        countries.append(_UNITED_STATES)

    return countries


def _country_from_city(segment):
    """
    País de la primera ciudad reconocida: primero el segmento completo sin
    palabras de modalidad y después cada una de sus piezas.
    """

    cleaned = _WORK_MODE_WORDS.sub(" ", _geo_normalize(segment))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")
    pieces = [
        piece.strip()
        for piece in _LOCATION_PIECE_SEPARATORS.split(cleaned)
        if piece.strip()
    ]

    for candidate in [cleaned, *pieces]:
        country = _city_country(candidate)

        if country:
            return country

    return None


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

    for raw_segment in re.split(r"[;|]", str(location)):
        segment = raw_segment.strip()

        if not segment:
            continue

        # Si ya sabemos el país por texto o código, no reinterpretamos la
        # ciudad.
        countries = _explicit_countries(segment) or [
            _country_from_city(segment)
        ]

        for country in countries:
            if country and country not in result:
                result.append(country)

    return result

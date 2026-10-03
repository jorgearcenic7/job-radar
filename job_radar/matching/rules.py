import re
import unicodedata

from job_radar.domain import Job


_COUNTRY_ALIASES = None
_CITY_COUNTRIES = None


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def matches_target_location(location):
    value = normalize(str(location or ""))

    if not value:
        return False

    # España en cualquier parte, también en ubicaciones múltiples:
    # "Madrid, Spain; Remote - US"
    if re.search(r"\b(?:spain|españa)\b", value):
        return True

    location_parts = {
        normalize(part)
        for part in re.split(r"[,/()]", value)
        if part.strip()
    }

    if "es" in location_parts or "esp" in location_parts:
        return True

    countries = infer_countries(location)

    if "Spain" in countries:
        return True

    remote = re.search(
        r"\b("
        r"remote|remotely|remoto|remota|"
        r"teletrabajo|distributed|anywhere|"
        r"work from (?:anywhere|home)"
        r")\b",
        value,
    )

    if not remote:
        return False

    # Remoto abierto a una región que incluye España.
    open_to_europe = re.search(
        r"\b("
        r"europe|europa|european union|"
        r"eu|emea"
        r")\b",
        value,
    )

    if open_to_europe:
        return True

    # Remoto limitado a otro país.
    if countries:
        return False

    # Remoto limitado a un país o región que no incluye España.
    # infer_countries no detecta "Remote - US" porque solo
    # reconoce códigos separados por comas o paréntesis.
    restricted = re.search(
        r"(?<![a-z])("
        r"us|usa|u\.s\.(?:a\.)?|united states|"
        r"canada|"
        r"latam|latin america|americas|"
        r"north america|south america|"
        r"apac|asia|india"
        r")(?![a-z])",
        value,
    )

    return not restricted


def extract_salary(description):
    patterns = [
        (
            r"(?:€|\$|£)\s?\d{2,3}(?:[.,]\d{3})*(?:\s?[kK])?"
            + r"\s*(?:-|–|—|to)\s*"
            + r"(?:€|\$|£)?\s?\d{2,3}(?:[.,]\d{3})*(?:\s?[kK])?"
            + r"(?:\s*(?:EUR|USD|GBP))?"
        ),

        (
            r"\b\d{2,3}(?:[.,]\d{3})+\s*(?:EUR|USD|GBP)"
            + r"\s*(?:-|–|—|to)\s*"
            + r"\d{2,3}(?:[.,]\d{3})+\s*(?:EUR|USD|GBP)?\b"
        ),

        (
            r"\b\d{2,3}\s?[kK]\s*(?:-|–|—|to)\s*"
            + r"\d{2,3}\s?[kK]\s*(?:EUR|USD|GBP)\b"
        ),

        (
            r"(?:€|\$|£)\s?\d{2,3}(?:[.,]\d{3})+"
            + r"(?:\s*(?:EUR|USD|GBP))?"
            + r"(?:\s*(?:per year|annually|a year|/year))?"
        ),
    ]

    for pattern in patterns:
        match = re.search(pattern, description or "", re.IGNORECASE)

        if match:
            return match.group(0).strip()

    return None


def extract_experience(description):
    patterns = [
        (
            r"\b(?:at least|minimum(?: of)?|min\.?)?\s*"
            + r"\d{1,2}\+?\s+years?\s+(?:of\s+)?"
            + r"(?:relevant\s+|professional\s+|hands-on\s+|industry\s+)?"
            + r"experience\b"
        ),

        (
            r"\b\d{1,2}\s*(?:-|–|—|to)\s*\d{1,2}\s+years?"
            + r"\s+(?:of\s+)?(?:relevant\s+|professional\s+)?experience\b"
        ),

        r"\b\d{1,2}\+?\s+years['’]?\s+experience\b",

        (
            r"\b(?:mínimo|minimo|al menos)?\s*"
            + r"\d{1,2}\+?\s+años?\s+de\s+experiencia\b"
        ),
    ]

    description = description or ""

    matches = sorted(
        (
            match
            for pattern in patterns
            for match in re.finditer(
                pattern,
                description,
                re.IGNORECASE,
            )
        ),
        key=lambda match: match.start(),
    )

    for match in matches:
        if _is_company_experience(description, match.start()):
            continue

        # Más de 15 años no es un requisito plausible:
        # suele ser la antigüedad de la empresa.
        years = int(re.search(r"\d{1,2}", match.group(0)).group(0))

        if years > 15:
            continue

        return match.group(0).strip()

    return None


def _is_company_experience(description, start):
    """
    Detecta menciones de años que describen a la empresa,
    no al candidato: "for over 20 years", "our track record"...

    Solo mira la frase actual dentro de los 60 caracteres previos.
    """

    context = description[max(0, start - 60):start]
    context = re.split(r"[.!?;\n•]", context)[-1]

    return bool(
        re.search(
            r"\b("
            r"for over|founded|since|"
            r"we have|we've|our|"
            r"company|history|track record|"
            r"nuestra|nuestro|empresa|trayectoria"
            r")\b",
            context,
            re.IGNORECASE,
        )
    )


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
        (
            r"\b(?:at least|minimum(?: of)?|min\.?)\s*"
            + r"(\d{1,2})\+?\s*(?:years?|yrs?)\b"
        ),

        # 3-5 years / 3 to 5 years
        (
            r"\b(\d{1,2})\s*(?:-|–|—|to)\s*"
            + r"\d{1,2}\s*(?:years?|yrs?)\b"
        ),

        # 7+ years
        r"\b(\d{1,2})\+\s*(?:years?|yrs?)\b",

        # 2 years of experience / 5 years' experience
        (
            r"\b(\d{1,2})\s*(?:years?|yrs?)['’]?\s+"
            + r"(?:of\s+)?"
            + r"(?:relevant\s+|professional\s+|hands-on\s+|industry\s+)?"
            + r"experience\b"
        ),

        # español
        (
            r"\b(?:mínimo|minimo|al menos)?\s*"
            + r"(\d{1,2})\+?\s*años?\s+de\s+experiencia\b"
        ),
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


def classify(job: Job):
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
    - ubicaciones fuera de España que no sean remotas

    RESULTADOS
    ----------
    Buena coincidencia:
        claramente alineado y nivel compatible.

    Stretch:
        algo por encima o rol adyacente pero razonable.

    None:
        fuera de objetivo.
    """

    title = normalize(job.title or "")

    raw_description = job.description or ""

    description = normalize(
        raw_description
    )

    location = job.location

    if not matches_target_location(location):
        return None

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
        job.experience_text
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
        r"\bdata pipelines?\b" + r"|\bpipelines?\s+de\s+datos\b",

        r"\b(?:etl|elt)\b" + r"|extract.{0,30}transform.{0,30}load",

        r"\bdata ingestion\b" + r"|\bingesta de datos\b",

        r"\bdata warehouse\b" + r"|\bdata lake\b" + r"|\blakehouse\b",

        r"\bdata model(?:ing|ling)\b" + r"|\bmodelado de datos\b",

        r"\b(?:batch|stream) processing\b",

        r"\b(?:spark|pyspark|databricks)\b",

        r"\b(?:airflow|dbt)\b",

        r"\b(?:kafka|event streaming)\b",

        r"\b(?:snowflake|bigquery|redshift|" + r"microsoft fabric)\b",
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


def _geo_normalize(value):
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

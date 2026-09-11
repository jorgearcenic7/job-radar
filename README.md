# Job Radar

Job Radar is a personal job-monitoring platform focused on Data Engineering and adjacent technical roles at product, software, SaaS and FinTech companies.

**Production:** https://job-radar-snowy.vercel.app

The platform automatically retrieves public job listings from multiple recruitment systems, normalizes them into a common model, evaluates their relevance, stores them in PostgreSQL and exposes them through a Next.js web application.

## Current status

Implemented:

- Automated daily ingestion at **06:00 Europe/Madrid**.
- GitHub Actions scheduling.
- Production PostgreSQL with Neon.
- Production web application on Vercel.
- Multiple ATS and careers integrations.
- Rule-based job matching.
- Salary extraction when published.
- Experience extraction when published.
- Country normalization.
- Country-only filtering in the web.
- Spain selected by default.
- PostgreSQL upserts to avoid duplicates.

Not implemented yet:

- Email notifications.
- Closed-listing detection.
- Pagination.
- Complete automated connector tests.

## Supported companies

| Company | Source |
| --- | --- |
| Typeform | Greenhouse |
| N26 | Greenhouse |
| Datadog | Greenhouse |
| Clarity AI | Greenhouse |
| Fever | Greenhouse |
| Cabify | Greenhouse |
| Aircall | Greenhouse |
| Auctane | Greenhouse |
| Celonis | Greenhouse |
| Taxbit | Greenhouse |
| Ebury | Greenhouse |
| Lynx | Greenhouse |
| Pleo | Ashby |
| Capchase | Ashby |
| Invopop | Ashby |
| Airwallex | Ashby |
| Checkout.com | Ashby |
| Spendesk | Teamtailor |
| Seedtag | Teamtailor |
| Lingokids | Teamtailor |
| ThetaRay | Comeet |
| Mambu | iCIMS |
| Deel | Deel Careers |
| Revolut | Revolut Careers |

Job availability changes continuously. A supported company may temporarily have zero active openings.

## Matching logic

The current target profile is approximately one year of professional experience, mainly focused on Data Engineering.

Primary roles include:

- Data Engineer
- Analytics Engineer
- Data Platform Engineer
- Data Infrastructure Engineer
- Data Warehouse Engineer
- ETL / ELT Engineer
- DataOps Engineer
- BI Engineer

Adjacent technical roles can also be retained when their descriptions contain sufficient relevant evidence:

- Data Analyst
- Product Data Analyst
- Data Scientist
- Machine Learning Engineer
- MLOps Engineer
- Applied AI Engineer
- Platform Engineer
- Software Engineer with strong Data Engineering responsibilities

### Seniority and experience

Excluded:

- Intern / Internship
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
- Level IV / 4 or above
- Roles requiring **4+ years** when detected

Allowed:

- Junior
- Associate
- Level I
- Level II
- Mid
- Level III
- Up to 3 years of required experience

Selected jobs receive one of two labels:

- **Buena coincidencia**: strongly aligned with the target profile.
- **Stretch**: relevant but slightly broader or above the current profile.

## Technical signals

The matching engine uses signals such as:

- Data pipelines
- ETL / ELT
- Data ingestion
- Data warehouse
- Data lake
- Lakehouse
- Data modeling
- Batch / stream processing
- Spark / PySpark
- Databricks
- Airflow
- dbt
- Kafka
- Snowflake
- BigQuery
- Redshift
- Microsoft Fabric
- SQL
- Python
- Cloud platforms

## Salary and experience extraction

When published by the company, Job Radar attempts to extract:

- Salary / compensation.
- Minimum years of experience.

No value is invented when the information is absent.

## Location and country normalization

The original location published by each company is preserved.

A separate `countries` field is generated for filtering.

Examples:

```text
Madrid -> Spain
Madrid, ES -> Spain
Málaga -> Spain
Barcelona -> Spain
Spain - Remote -> Spain
London -> United Kingdom

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
- Safe closed-listing detection by company snapshot.
- Automated connector and lifecycle tests in GitHub Actions.

Not implemented yet:

- Email notifications.
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
| Monzo | Greenhouse |
| Pleo | Ashby |
| Capchase | Ashby |
| Invopop | Ashby |
| Airwallex | Ashby |
| Checkout.com | Ashby |
| Rain | Ashby |
| Mastercard | Workday |
| BBVA | Workday |
| Santander | Workday |
| CaixaBank Tech | Official careers site |
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
```

If several locations are published, a job can belong to several countries.

Explicit country names and ISO country codes take priority over city inference. For example, `Valencia, ES` is classified as Spain.

The web filter contains countries only. Spain is selected by default, while each job card keeps the original location published by the company.

## Technology stack

- Python 3.12 for ingestion, enrichment and matching.
- PostgreSQL 17 and Neon for persistent production storage.
- Psycopg 3 for database access.
- Docker for reproducible pipeline execution.
- Next.js, React and TypeScript for the web application.
- Tailwind CSS for interface styling.
- Vercel for production hosting.
- GitHub for source control.
- GitHub Actions for daily automation.
- geonamescache for country normalization.
- curl_cffi for the Revolut Careers integration.

## Automation

The ingestion pipeline runs automatically every day at **06:00 Europe/Madrid** through GitHub Actions.

The local computer does not need to remain powered on.

## Web application

Current functionality includes:

- Job title search.
- Company filter.
- Country-only filter.
- Spain selected by default.
- Option to show only matches.
- Original published location.
- Salary when available.
- Experience requirement when available.
- Direct link to the original job listing.
- Paginated results with 20 job listings per page.
- Filter preservation when moving between pages.

The web displays the total number of filtered results and allows navigation between result pages.

## Current limitations

- No email notifications yet.
- Matching remains rule-based.
- Not every connector has automated tests.
- External ATS and career websites may change their APIs or HTML.

## Roadmap

- [x] Dockerized ingestion pipeline
- [x] PostgreSQL persistence and idempotent upserts
- [x] Next.js web application
- [x] Neon production database
- [x] Vercel deployment
- [x] Daily GitHub Actions execution
- [x] Multiple ATS integrations
- [x] Revolut integration
- [x] Salary and experience extraction
- [x] Buena coincidencia / Stretch matching
- [x] Country normalization
- [x] Country-only web filtering
- [x] Spain selected by default
- [x] Automated connector tests
- [x] Closed-listing detection
- [x] Pagination
- [ ] Email notifications
- [ ] More product, software and FinTech companies

## Security

Database credentials, connection strings and secrets are stored in environment variables or GitHub secrets and are never committed to the repository.

## Production

https://job-radar-snowy.vercel.app

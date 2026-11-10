"""Company job boards the API sweeps read (1-find/ats_sweep.py, 1-find/workday_sweep.py).

Public, unauthenticated APIs. A token that no longer exists just answers 404 and is skipped (the
sweep prints how many boards were unreachable), so these lists can be grown and pruned freely. Find
a company's board: Greenhouse  boards.greenhouse.io/<token>, Ashby  jobs.ashbyhq.com/<org>,
Lever  jobs.lever.co/<company>, SmartRecruiters  jobs.smartrecruiters.com/<Company>.

Weighted to companies that run cloud / platform / SaaS engineering teams in India. Pruned on
2026-11-04 with `python ats_sweep.py --probe`: only boards that existed then are listed. Re-run it
occasionally and delete what it reports.
"""
from __future__ import annotations

GREENHOUSE = """mongodb druva pubmatic zscaler rubrik elastic yugabyte dremio starburst fivetran datadog newrelic
twilio okta stripe coinbase robinhood figma asana dropbox gitlab cloudflare pagerduty samsara groww
sigmoid inmobi glance zetaglobal agoda airbnb pinterest reddit optiver imc jumptrading commvault
thoughtworks netskope sumologic solarwinds databricks cockroachlabs tailscale planetscale
grafanalabs honeycomb launchdarkly circleci singlestore fastly sendbird zuora jfrog veracode lookout
orca purestorage vast kentik airtable squarespace adyen brex ramp netlify vercel""".split()

ASHBY = """atlan ramp linear vercel supabase replit posthog plane sarvam snyk redis clickhouse vultr cloudbees
illumio wiz notion miro clickup zapier plaid snowflake confluent pinecone weaviate render railway
sentry""".split()

LEVER = """cred meesho zeta mindtickle hevodata plivo paytm freshworks sysdig sonarsource sonatype fly""".split()

SMARTRECRUITERS = """Freshworks Visa BoschGroup Siemens Equinix ServiceNow Wipro LTIMindtree Publicis Sapient IKEA
FractalAnalytics QAD Ajuba""".split()

# "<tenant>.<wdN>/<site>": the part of https://<tenant>.<wdN>.myworkdayjobs.com/en-US/<site>.
WORKDAY = """nvidia.wd5/NVIDIAExternalCareerSite mastercard.wd1/CorporateCareers salesforce.wd12/External_Career_Site
intel.wd1/External hp.wd5/ExternalCareerSite dell.wd1/External adobe.wd5/external_experienced
workday.wd5/Workday ptc.wd1/PTC bmc.wd1/BMC_Careers fis.wd5/SearchJobs hsbc.wd3/external
avalara.wd1/AvalaraCareers walmart.wd5/WalmartExternal target.wd5/targetcareers wellsfargo.wd1/WellsFargoJobs
statestreet.wd1/Global northerntrust.wd1/northerntrust db.wd3/DBWebSite ubs.wd3/UBS_Experienced_Professionals
genesys.wd1/Genesys zebra.wd1/Zebra_Careers motorolasolutions.wd5/Careers siemens.wd3/SiemensCareers
barclays.wd3/External_Careers_Site citi.wd5/2 morganstanley.wd5/External sap.wd3/SAPCareers
autodesk.wd1/Ext thomsonreuters.wd5/External_Career_Site nasdaq.wd1/Global_External_Site
blackrock.wd1/BlackRock_Professional vanguard.wd5/vanguard_external marvell.wd1/MarvellCareers
micron.wd1/External paypal.wd1/jobs ebay.wd5/apply dxc.wd1/DXCJobs unisys.wd5/External
hpe.wd5/ACJobSite redhat.wd5/jobs broadcom.wd1/External_Career equifax.wd5/External
transunion.wd5/TransUnion fico.wd1/External philips.wd3/jobs-and-careers wolterskluwer.wd3/External
lseg.wd3/Careers fmr.wd1/FidelityCareers spglobal.wd5/SPGI_Careers broadridge.wd5/Careers
synchrony.wd5/Careers""".split()

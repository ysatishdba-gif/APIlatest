# Ignore Python cache and compiled files
__pycache__/
*.pyc
*.pyo
*.pyd
Here are all the changes from the original API (1.3.0) to 1.5.15, listed as features:

New endpoints

POST /v2/retrieval-signals added.
GET /v2/retrieval-signals/action-types added.
POST /v3/retrieval-signals added.
/v1/retrieval-signals, /v1/nature-breakdown, /v2/nature-breakdown and /v1/extract-intents are unchanged; /v1 output is identical to 1.3.0.

Action Finder (v2, v3)

Action Finder added. It classifies each query into actions (information retrieval, conversational, knowledge/fact search, admin, out of scope) inside the service, with no HTTP call to another endpoint.
Optional gate: send actions (and enable_action_finder), and only queries classified as information retrieval get retrieval signals.
Other queries get a skip block with the identified actions and a reason.
If the Action Finder itself fails, the query is processed anyway, and the failure is logged.
Its token usage is reported under usage_metadata.action_finder.

Record types (v2, v3)

Record-type names are generated exactly as in /v1, by the same prompt.
Record-type codes come from your standalone link-code pipeline, with the tag names as context: cluster selection → activities → Stage 1 → documents → Stage 2 → document classes → Stage 3.
The codes are the Stage 3 node ids with the row suffix removed (C3166217-0 → C3166217).
There are no fallbacks. A record type the pipeline can't code has an empty coding.
The BigQuery queries and service payloads are the script's own.
Record types in one query run in parallel, batches of 25, 30 workers, as in the script.

Temporal prompt (v3)

The /v3 contextual-environment prompt is a new file: prompts/v3/contextual_environment.py.
The temporal vocabulary (about 1,500 entries) is no longer pasted into the prompt.
The model now states each window as a span (last / within / between, value, unit), plus whether it was explicit or inferred and a short reason.
The service turns that span into a vocabulary entry: the exact span if one exists, else the narrowest entry that contains it, else the widest.
Windows are resolved per candidate, so a query with no time wording still gets the window its clinical nature implies.
The prompt carries a short menu of spans the vocabulary can code, and a shortlist of vocabulary names close to the query's wording.
The model's answer format for temporal fields is enforced.

Contextual prompt optimised (v3)

The contextual-environment prompt went from about 82.8K to about 17.5K characters (about 20.7K to 4.4K tokens, −79%), because the vocabulary list was removed.

Temporal output (v2, v3)

All temporal windows found for a query are merged into the one temporal object. The window that contains the others gives the name and formula, and every window's codes are in coding.
formula always has two values, ["REF_POINT", "REF_POINT - X"], including when the vocabulary stores a window as one string (the AIF GCS file) and for client hints.
A range such as an age band (55-59) never names the object when a window starting at REF_POINT exists.

Reliability (v2, v3)

A failing cluster-selection, transaction-selection or BigQuery call is logged. That record type gets no codes, and the response is still a normal 200.
Record-type coding for a query has a 120 s budget; each service call is capped by the time left, so the request stays under the 300 s limit.
A model answer that can't be parsed or isn't valid is asked for once more before the query is returned without signals.
Two new errors, both answered before any model call:
503 cluster_service_unavailable when the pipeline isn't configured.
503 temporal_index_unavailable when the vocabulary can't be indexed at startup (/v3 only).

Configuration

config/.env.<env> files are plain KEY=VALUE lists, with four keys added: the two service URLs, the BigQuery project and the dataset.
app/config.py holds every setting, with your script's values as defaults, so nothing needs to be filled in.
A blank value in an env file keeps the default.
Local runs get identity tokens from gcloud, as the script does; Cloud Run uses its service account.
google-cloud-bigquery added to requirements.txt.

Logging

One log line per resolved temporal window (Temporal inference), plus Temporal windows merged.
One line per record type from the pipeline, with counts per step, the outcome and seconds taken.
Per-request timing (Retrieval signals request timing) and a startup line showing the URLs, project and dataset in use.

Optional tools (off by default)

Shadow mode: /v3 also runs the /v2 matching and logs where the two disagree.
Scripts to replay queries against a running service and compare /v2 against /v3.
Cadence memory: windows the service inferred before can be fed back into the prompt as examples.

Tests and docs

Tests went from the 1.3.0 set to 216, all passing.
README, CHANGELOG (written per endpoint), the Confluence page and the OpenAPI file are updated.
# Ignore virtual environments
.venv/
venv/
env/
ENV/

# Ignore test and coverage outputs
.pytest_cache/
.coverage
htmlcov/
.tox/
.nox/
.cache

# Ignore local environment files
*.env
*.env.*

# Ignore IDE/editor settings
.vscode/
.idea/
*.sublime-project
*.sublime-workspace

# Ignore OS-specific files
.DS_Store
Thumbs.db

# Ignore logs and temp files
*.log
*.tmp
*.bak

# Ignore build and dist folders
build/
dist/
*.egg-info/
.eggs/
wheels/
*.whl

# Ignore node modules (if any)
node_modules/

# Ignore notebooks (if any)
app/notebooks/

# Ignore Docker-related files that shouldn't be in the build context
docker-compose.override.yml

# Ignore test files and results
tests/

---
name: caf-service-provisioning
description: >
  Orchestrates the full sequence for taking a Cloud Run service from nothing
  to a working dev deployment on the Mayo CAF platform: GCP project and
  app-resources Terraform bootstrap (new project) or url_map/ELB Terraform
  wiring (existing project), GitHub Actions CI/CD generation, GitHub
  environments and secrets, the first dev deploy, and optional Apigee
  routing. Use when the user asks to set up a new service end-to-end,
  provision a brand-new GCP project, deploy a new app to Cloud Run from
  scratch, or "get this service running in dev" without any of the CAF
  plumbing in place yet. This skill does not replace the other skills and
  docs in this repo — it decides which ones apply, in what order, and gates
  each phase on the previous one actually succeeding. For a single known
  step (e.g. "just add a url_map entry" or "just fix my Apigee target"), use
  that specific skill directly instead of this one.
version: 0.2.0
tools: Bash, Read, Edit
entrypoints:
  claude_skill: true
  github_skill: false
  github_prompt: true
  codex_skill: true
  codex_skill_json: true
interface:
  display_name: "CAF Service Provisioning"
  short_description: "Orchestrate the end-to-end sequence for provisioning and deploying a new CAF Cloud Run service."
  default_prompt: "$caf-service-provisioning Walk me through provisioning and deploying my service end-to-end."
---

# CAF Service Provisioning — Orchestrator

This skill sequences the other skills and docs in this repo into one
end-to-end run: infra bootstrap → CI/CD wiring → GitHub environments → first
deploy → optional Apigee routing. Each phase below names the skill or doc
that does the actual work; this skill's job is deciding which phases apply
to this request, running them in the right order, and refusing to advance
past a phase that hasn't actually succeeded. Read `README.md` and
`project-context.md` before starting — they define the terms and
placeholders used throughout.

Do not duplicate another doc's instructions here. If a phase's linked doc
and this skill ever disagree, the linked doc is authoritative — come back
and fix this file.

## Companion Skills Required

This skill invokes four other skills that live in this same repo:
`apigee-proxy-request` (Phase 0/5), `gha-frontend-pipeline-single-service`
(Phase 2), `gha-backend-pipeline` (Phase 2), and `apigee-proxy-routing`
(Phase 5). It assumes they're present and does not duplicate their logic. If
`caf-service-provisioning` was installed on its own rather than as part of
the full `caf-agent-instructions` repo, install these four alongside it too.

Before invoking any of them, confirm the referenced `SKILL.md` actually
exists in this installation. If it doesn't, **stop and tell the user which
skill is missing** and that it needs to be installed from
`skills/<name>/SKILL.md` in `caf-agent-instructions` — do not attempt to
hand-roll that phase from memory. Getting Apigee ticket fields, GitHub
Actions wiring, or PSC/ILB routing wrong is exactly the failure mode these
skills exist to prevent. This is separate from the Phase 1/1b skills below,
which live in *other* repos entirely and have their own cross-repo fallback
handling (read the file directly, or hand off to a session opened there).

## Track phases with tasks

This run spans multiple repos and, often, multiple sessions (a Terraform
pipeline or an Apigee PSC request can take a while to land). Use `TaskCreate`
at the start to create one task per phase you determine applies (Phase 0
through whichever of Phase 5/6 apply), and update status as each completes.
Tasks don't persist across sessions — if resuming a provisioning run started
earlier, don't trust a stale task list; re-derive what's actually done from
git/gh/gcloud/Terraform state (open PRs, pipeline run status, existing GitHub
environments, whether the Cloud Run service exists) before continuing.

## Phase 0 — Scope Gate

Answer these in one round before doing anything else:

- New project (new GCP project + app-resources repos) or adding a service to
  an **existing** project?
- How many Cloud Run services will this rollout include, and what does each
  one do (frontend user-facing UI / backend API / both in one service)? Ask
  this as a single open-ended question rather than a fixed single-vs-multiple
  choice — "multiple services" by itself doesn't say how many or what each
  does, and a fixed-choice functionality question (frontend / backend / both)
  can't represent an N-service breakdown either. A prompt like "How many
  Cloud Run services, and what does each one do? e.g. '2 services: nick-fe
  (frontend), nick-be (backend)'" gets the count and the per-service
  breakdown in the same round. If the first answer already names every
  service and its functionality, don't spend a second round-trip confirming
  the count separately.
- **Per service**, does it need a relational database (Cloud SQL / managed
  PostgreSQL)? Ask directly — don't infer it from "backend," plenty of
  backends are stateless or use a different datastore. If yes, flag it now.
  For a **new** project, this can go straight into Phase 1's bootstrap config
  (`create_cloud_sql`/`app_name_db`) instead of a follow-up pass — see Phase 1
  step 2. For an **existing** project, it's still actioned in Phase 1b via the
  `cloud-sql-onboarding` skill, since there's no bootstrap run to declare it
  into.

Don't ask a separate "does it need Apigee / ELB / url_map" question — most
requesters don't know CAF's internal routing terms, and asking it
independently invites an inconsistent answer. Routing is **derived** from
functionality:

- Frontend traffic is public and fronted by **Akamai**; a frontend needs the
  CAF ELB entry (`cloudrun_public_services`) that Akamai sits in front of.
- Backend/API traffic goes through **Apigee**; a backend needs the CR-ILB
  `url_map` entry.
- A service with both frontend and backend functionality needs both entries
  — split by which requests are UI versus API, don't ask the user to pick
  one routing mechanism for the whole service.

If a service has a backend/API component and no Apigee proxy repo exists yet
for it, note that now — but don't submit the request yet if this is a **new**
project: the ServiceNow request needs a real GCP project to reference, so it
has to wait until Phase 1 creates one. For an **existing** project the
project already exists, so invoke `/apigee-proxy-request` right here instead
of waiting for Phase 5 (see "Companion Skills Required" above if it isn't
available in this installation).

Either way, don't defer all the way to Phase 5. The ticket has its own lead
time and is often the longest pole in the whole rollout — get it in as soon
as a project exists so it lands in parallel with Phases 2–4 instead of
stalling Phase 5 behind it. Submitting it doesn't block anything else:
proceed with the remaining phases immediately after, don't wait on the
ticket.

Then collect the values in `project-context.md` (repo paths, app prefix/name,
GitHub org) — do not guess any of them, and never infer a repo path from
another repo's name.

- **New project:** ask for the project **slug** (e.g. `projecttemplate`,
  `dpe-project-template`), not four separate GCP project IDs. Explain that
  dev/test/stage/prod project IDs are derived as `{slug}-d`, `{slug}-t`,
  `{slug}-s`, `{slug}-p` and will be created by the Phase 1 bootstrap — the
  user doesn't need to invent or pre-register them.
  Also determine which CAF folder/program the project belongs to. Don't
  assume "DPE POC" by default or guess from naming — `/caf-project-bootstrap`
  already has a catalog-driven path for this (`caf-folder-catalog.yml` in
  `caf-template-project`, its "Path C"). If that skill is available in the
  current session, let it present the catalog and ask there. If it isn't
  (per the Cross-repo note below), read `caf-folder-catalog.yml` yourself and
  have the user pick a `program_name` from it before moving to Phase 1 — the
  catalog's programs (e.g. `dpe-poc`, `ext-dpe-poc`, `cdh-dps`) carry
  different folder IDs, shared projects, and PAU codes, and only `dpe-poc`
  matches `references/new-project-setup-dpe-poc.md`'s fixed values.
  **`program_name` and `ado.project` are coupled — resolve `ado.project` from
  the catalog, don't guess it.** `program_name` selects the CAF/GCP folder
  (folder IDs, PAU, SAs, state bucket, and the `caf-<program_name>-<slug>-project`
  repo name); `ado.project` only selects the Azure DevOps project that hosts
  that repo and pipeline. They are different fields but must identify the
  **same org**. `caf-folder-catalog.yml` carries the mapping: each program
  entry has an `ado_project` field (e.g. `dpe-poc` → `DPE_POC`, `ext-dpe-poc`
  → `DPE_POC`, `cdh-dps` → `Digital Products and Services`). Read the
  `ado_project` for the chosen `program_name` from the catalog and use it —
  do **not** fall back to the bootstrap config's hardcoded `DPE_POC` default
  when the catalog says otherwise.
  **If no mapping reference applies** — the program has no catalog entry, or
  its entry has no `ado_project` — do **not** guess or substitute a project
  you happen to have access to. Instead, list the ADO projects the user *can*
  access (`az devops project list`), **recommend** the one whose org matches
  `program_name`, and ask the user to confirm which to use — then add its
  `ado_project` to the catalog entry so the next run resolves it automatically.
  **Always surface any inconsistency between `ado.project` and `program_name`
  to the user** — an ADO project belonging to a different org than
  `program_name` means one of the two is wrong, and the user must resolve it,
  not you. Concretely: the ADO project `Digital Products and Services` is the
  **DPS** org, which is `program_name: cdh-dps` — pairing it with
  `program_name: dpe-poc` (the **DPE** org) is inconsistent and will render a
  project into the wrong folder. When they diverge, stop, tell the user exactly
  which org each value points to, and confirm which org the project actually
  belongs to; only then change **both** fields together, never just one.
- **Existing project:** the four project IDs already exist — read them from
  the app-resources repo's `vars/*.tfvars` (`develop`/`test`/`stage`/`main`)
  rather than asking the user blind.

Verify the README's prerequisites checklist (`gcloud`/`gh`/`az`/`python3`
auth, `caf-template-project` and `caf-template-app-resources` cloned on the
`template` branch and pulled to latest, `vpc-con` VPC connector, `gsa5-caf-mgmt`
service account). **Stop and resolve with the user before continuing if any
are missing.** Checking that the branch is checked out is not the same as
checking it's current — a stale local clone renders an out-of-date template
with no warning, so pull both template repos to latest before handing off to
the bootstrap skills in Phase 1.

## Phase 1 — Provision GCP Infrastructure (new project only)

Skip this phase entirely if adding a service to an existing project — go to
Phase 1b instead.

1. From the `template` branch of `caf-template-project`, run
   `/caf-project-bootstrap`.
2. Only after it succeeds, from the `template` branch of
   `caf-template-app-resources`, run `/caf-app-resources-bootstrap`. It
   depends on service accounts the project bootstrap creates — never run it
   first. Phase 0 already collected the per-service functionality breakdown
   for this rollout — pass it into the bootstrap config's `frontend_services`
   (for each frontend/`cloudrun_public_services` service) and
   `backend_services` (for each backend/`url_map`-only service) lists so
   every service already known at this point is wired — routing entry and
   dedicated service account together — as part of the initial commit,
   instead of deferring all of them to Phase 1b. Only a service discovered
   *after* this bootstrap run needs Phase 1b. Leave the config's separate
   `default_service_name` field unset once at least one real service is
   named in `backend_services`/`frontend_services` — don't repeat one of
   those service names there. It's a distinct, older mechanism that only
   wires routing, never a service account, so naming it after a real backend
   does not give that backend an SA, and declaring the same name in both
   places collides. The first `backend_services`/`frontend_services` entry
   wired becomes `default_backend` automatically; `default_service_name`
   should only get an explicit value when this rollout has no other known
   service yet at bootstrap time. If Phase 0 flagged a service as needing
   Cloud SQL, also set `create_cloud_sql: true` and `app_name_db` in this
   same bootstrap config (`cloud_sql_envs` narrows which environments get it,
   default all four) — this wires the toggle into the initial commit instead
   of a separate `cloud-sql-onboarding` pass afterward. The automatic
   secret-access IAM grant still depends on the consuming service being
   declared in `frontend_services`/`backend_services` in this same run (or
   already present from an earlier one) — a database with no service
   declared to read it yet just sits unused until one is.
3. Verify both Terraform pipelines actually applied (check pipeline status,
   don't assume) before moving on.
4. If a backend/API service in this rollout has no Apigee proxy repo yet
   (flagged back in Phase 0), invoke `/apigee-proxy-request` now — the
   project now exists, so nothing blocks submitting the ticket, and doing it
   here lets the ServiceNow lead time run in parallel with Phases 2–4.

If the program picked in Phase 0 is specifically `dpe-poc` (not `ext-dpe-poc`
or another catalog entry — they have different folder IDs and shared
projects), also read `references/new-project-setup-dpe-poc.md` for folder-specific fixed
values and known platform blockers (e.g. the
`iam.serviceAccounts.getAccessToken` binding that requires the platform
team). Other programs don't have a dedicated doc yet; the same class of
platform-team blocker can still show up under a different service-account
name for that program — recognize it by the error, not by matching this
doc's fixed values.

**Cross-repo note:** these two bootstrap skills live in the template repos,
not this one, so `/caf-project-bootstrap` / `/caf-app-resources-bootstrap`
won't appear as an available skill in the current session — the slash-command
registry is fixed to the session's own project root and doesn't change when
you `cd` elsewhere. That does not mean the work has to move to a different
session. Offer the user both options rather than defaulting to the first:

1. **Read and follow it here.** Read
   `<repo-local-path>/skills/caf-project-bootstrap/SKILL.md` (or
   `caf-app-resources-bootstrap`'s equivalent) — it's a plain file, and
   `Bash`/`Read`/`Edit` aren't restricted to this repo. Following its
   canonical command doc verbatim in this conversation is not hand-rolling;
   it's the same skill, just invoked by reading the file instead of the
   slash command. This keeps the user able to watch and weigh in on each
   step instead of it running out of view.
2. **Hand off to a session opened in that repo** (on the `template` branch)
   if the user would rather drive it themselves — return to this
   orchestrator for the next phase once it succeeds.

Either way, do not approximate what the bootstrap skill does from memory —
only proceed after actually reading its current instructions via one of the
two options above.

## Phase 1b — Add a Service to an Existing Project

Run this phase for every service added to an **existing** project, and for
any **new**-project service that wasn't declared in Phase 1's bootstrap
config (`frontend_services`/`backend_services`) — e.g. one discovered or
decided on after that bootstrap run already happened. Don't assume a service
is wired just because Phase 1 ran; verify by reading that service's entry in
`vars/develop.tfvars` before skipping this phase for it.

**Cross-repo note:** a rendered app-resources repo carries its own skills
under `.agents/skills/` / `.claude/commands/` in *that* repo (not this one) —
typically `caf-app-resources-add-cloudrun-service`, `app-resources-url-map`,
and `caf-app-resources-terraform`. As with Phase 1's bootstrap skills, don't
default to sending the user to a separate session — read that skill's
`SKILL.md` (and canonical command doc) directly from the rendered repo's
local path and follow it in this conversation, or hand off to a session
opened there if the user prefers to drive it themselves. Either way, do not
hand-roll what one of these already does from memory — they validate
naming/length constraints, keep all four env tfvars consistent, and know
this repo's actual variable schema (which can drift from what's described
here), so read the current instructions before acting.

Use `/caf-app-resources-add-cloudrun-service` for a frontend's
`cloudrun_public_services` entry (fronted by Akamai/ELB), and
`/app-resources-url-map` for a backend's CR-ILB `url_map` entry (reachable via
Apigee) — per the Phase 0 functionality answer, not a separate routing
question. `/app-resources-url-map` also creates the backend's dedicated
service account (`service_accounts` entry) in the same run — it is not a
separate manual step; see `references/build-and-deploy-pipeline-setup.md`
Workstream 1 for the combined frontend+backend case, where the SA instead
comes from the `cloudrun_public_services` entry. Then run `terraform plan`/`apply`
via `/caf-app-resources-terraform` — prefer letting the ADO pipeline apply
after a PR merges to `develop` over that skill's local-apply option, unless
the user explicitly asks to bypass the pipeline.

**Stop here until this Terraform apply is confirmed green.** Triggering a
deploy before the service account and route exist produces
`PERMISSION_DENIED` or a missing-service-account failure, not a useful error
about what's actually wrong.

If this service was flagged in Phase 0 as needing Cloud SQL, run the
`cloud-sql-onboarding` skill now, in the same app-resources PR/apply as the
routing entry above — it enables the `create_cloud_sql` toggle, applies it,
and confirms the automatic secret-access IAM grant. Its remaining steps
(wiring the connection secret into the deploy workflow) carry into Phase 2,
since that's where the workflow files themselves get written.

The project already existed coming into this phase, so if a backend/API
service here has no Apigee proxy repo yet, `/apigee-proxy-request` should
already have been invoked back in Phase 0 — no need to wait for this phase.

## Phase 2 — CI/CD Wiring

Route on the Phase 0 answers:

| Situation | Do this |
|---|---|
| Frontend, single Cloud Run service, no blue/green | Invoke the `gha-frontend-pipeline-single-service` skill (see "Companion Skills Required" above if it isn't available in this installation). It also creates all six GitHub environments and sets the secret in each — **Phase 3 is already done when this finishes; skip to Phase 4.** |
| Frontend, but blue/green or multi-service | No packaged skill yet. Read `references/deployment-workflow-patterns.md` (Advanced Multi-Service Backend pattern) and `references/build-and-deploy-pipeline-setup-frontend.md`, and build the workflow files by hand. |
| Backend | Read `references/build-and-deploy-pipeline-setup.md` Workstream 1 (service accounts in app-resources) only if Phase 1/1b didn't already create them; then invoke the `gha-backend-pipeline` skill (see "Companion Skills Required" above if it isn't available in this installation) — it covers pattern selection (`single_service` vs `mono_repo`), the Redis/Cloud SQL/AppDynamics decisions, GitHub environments, and running the generator. |
| Single service with both frontend and backend functionality | No packaged skill for this combination. Follow `references/build-and-deploy-pipeline-setup.md` Workstream 1's combined-service note: base the service off `cdp-frontend-javascript-template-helloworld`, use the backend SA pattern (not the frontend minimal SA) since it needs the same Cloud SQL/PubSub/Secret Manager access as a typical backend, and treat it as backend for the CI/CD pattern (one Cloud Run service, one deploy pipeline) — use `gha-backend-pipeline`'s `single_service` pattern. Confirm with the user which specific routes are UI versus API so Phase 1b/5 wire the `cloudrun_public_services` and `url_map` entries against the right paths — both can reference the same service. |
| Unsure which pattern fits | Read `references/deployment-workflow-patterns.md` first, answer its Example Selection Checklist, then re-enter this phase. |

For backend, `gha-backend-pipeline`'s own SKILL.md already covers the
Redis/Cloud SQL/AppDynamics pre-setup decisions and GitHub environments —
don't duplicate that gathering here. If this service needs Cloud SQL, use
the `cloud-sql-onboarding` skill's Step 4 instead of hand-rolling it — it
covers both shipped pipeline patterns (single-service `secrets:` block, and
matrix/promote via the same dedicated `secrets:` input Redis/AppDynamics
already use) and wires in the real `db_connection_string_secret_id`
Terraform output from Phase 1b's apply, not a hand-typed secret name.

## Phase 3 — GitHub Environments & Secrets

Skip if the frontend skill in Phase 2 already created them.

Follow `references/github-environments-setup.md` end to end for every environment this
deploy targets: find `GS2_IMPERSONATION_ACCOUNT_SECRET` in the correct
shared default project, create the GitHub environment, set the secret,
verify it's present.

**Stop and verify the secret is present in each environment before
triggering any deploy.** A missing or wrong-sourced secret is the most
common CI auth failure (see README's Common Failure Modes table and
`references/TROUBLESHOOTING.md`), and it's much cheaper to catch here than after a
failed workflow run.

## Phase 4 — First Deploy

Trigger the dev deploy via `workflow_dispatch` and follow it to completion
(e.g. `gh workflow run` then `gh run watch`). Confirm the Cloud Run service
comes up healthy, not just that the workflow exited green.

If it fails, match the error text against `references/TROUBLESHOOTING.md` and the
README's Common Failure Modes table before changing anything — most first-
deploy failures are one of the entries there (missing secret, wrong
impersonation account, missing `--binary-authorization=default`, uppercase
image path, Node version).

**Stop here until the dev deploy is green** before starting Apigee work —
diagnosing Apigee routing against a service that doesn't deploy cleanly
wastes both phases.

## Phase 5 — Apigee Routing

Skip entirely if no service has a backend/API component — provisioning is
done; go to Phase 6.

The proxy request should already be in flight from Phase 0 (existing
project) or Phase 1 step 4 (new project). If it isn't — e.g. this phase was
reached without an earlier pass through this skill, or the request was
never submitted — use `/apigee-proxy-request` now (see "Companion Skills
Required" above if it isn't available in this installation); the steps
below don't need the rendered proxy repo to exist yet and can proceed
while the ticket is still in flight, but writing the target endpoint does.

Invoke the `apigee-proxy-routing` skill
(`skills/apigee-proxy-routing/SKILL.md`, already in this repo — see
"Companion Skills Required" above if it isn't) to register the service in
the CR-ILB `url_map`, submit/track the PSC ServiceNow ticket, and configure
the proxy target and bundle (base path, OpenAPI spec, OAuth/third-party
tokens as needed). Follow its workflow in full — do not shortcut its steps
from here.

If the user asks to disable or remove Apigee OAuth for the proxy, **stop** —
that requires an approved OIS SBAR exception. Do not remove an OAuth `<Step>`,
delete an OAuth policy file, or set `enabled="false"`. Read
`apigee-oauth-exception.md` to pick the scenario, then surface
`/apigee-oauth-exception-service-auth` or
`/apigee-oauth-exception-unauthenticated` and confirm a human will submit the
request. If the backend validates an Entra/Azure, Google, or Epic token, no
exception is needed — use the third-party token flow instead.

Traffic will not flow end-to-end until `psc_connection_status` reaches
`ACCEPTED` — say so explicitly rather than implying the service is fully
live once the target is written.

## Phase 6 — Done, and What's Next

Once Phase 4 (and Phase 5, if applicable) are green, provisioning for this
environment is complete. Promoting the same service to test/stage/prod is a
**separate** workflow — `references/promoting-to-higher-environments.md` — with its own
Terraform, environment, and Apigee steps per target environment. Don't start
it in the same pass unless the user asks; confirm first.

## Do Not Skip Ahead

Same rule as the README: GitHub environments must exist before a deploy
workflow can authenticate to GCP, and Terraform must apply before the
ELB/CR-ILB route or service account exists. Running a later phase before an
earlier one has actually succeeded produces a confusing downstream error
instead of the real one — always verify a phase's completion criteria before
advancing, not just that a command was run.

# Cloud Readiness — what's left to run this for real

The system runs **locally end-to-end** today. Nothing is provisioned on Google
Cloud yet — there's no project, no deployed service, no IAM, no CI. This is the
checklist to take it from "works on my machine" to "two authenticated Cloud Run
services talking over A2A."

Ordered roughly by dependency.

---

## 0. Foundation — GCP project & APIs

- [ ] A GCP project with billing enabled.
- [ ] Enable the APIs: `run.googleapis.com`, `cloudbuild.googleapis.com`,
      `artifactregistry.googleapis.com`, `aiplatform.googleapis.com` (for Vertex),
      `secretmanager.googleapis.com`, `iamcredentials.googleapis.com`.
- [ ] `gcloud config set project <id>` and pick a region (docs assume
      `us-central1`).

## 1. Models — decide AI Studio vs. Vertex AI

Today both services use the **AI Studio Gemini API** (`GEMINI_API_KEY`). For prod,
the convention (`.claude/skills/deploy-cloud-run`) is **Vertex AI** so calls use
the service's identity instead of a long-lived key.

- [ ] **Orchestrator** — already Vertex-capable via env only: set
      `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`.
      The ADK/`google-genai` layer reads these; no code change. Grant the runtime
      SA `roles/aiplatform.user` (done by `scripts/setup-gcp.sh`).
- [x] **Engine** — ✅ now backend-selectable by env. `cmd/server/main.go` uses
      Vertex (ADC, no key) when `GOOGLE_GENAI_USE_VERTEXAI=true` +
      `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`, else falls back to
      `GEMINI_API_KEY`. Misconfiguration fails fast with a clear message.

## 2. Containerization / build

- [x] **Engine** — ✅ `Dockerfile` added (multi-stage; static `CGO_ENABLED=0`
      build → `distroless/static:nonroot`; copies `agent-card.json`). Static build
      verified. `--source .` will use it. (A `.dockerignore` keeps `.env`/`.git`
      out of the build context.)
- [ ] **Orchestrator** — uses the Python buildpack via `--source .`; the
      `Procfile` (`uvicorn server:app`) is in place. Verify `uv`-based deps resolve
      in the buildpack (it reads `pyproject.toml` / `uv.lock`).

## 3. Identities & IAM

- [~] Create two runtime SAs + grant `roles/aiplatform.user` — scripted in
      [`scripts/setup-gcp.sh`](../scripts/setup-gcp.sh) (you run it once you have a
      project). Creates `travel-fare-engine-sa` and `travel-prequal-sa`.
- [ ] `roles/run.invoker` for the orchestrator SA **on the engine service** — must
      run *after* the engine is deployed (the script prints the exact command).
- [ ] Deploy each service `--service-account <sa>`.

## 4. Deploy & wire the two services

- [ ] Deploy the **engine** first, `--no-allow-unauthenticated`. Capture its URL.
- [ ] Set the engine's `HOST_URL` to that URL (or its agent card advertises
      `localhost` and discovery breaks).
- [ ] Deploy the **orchestrator** with `FARE_ENGINE_URL=<engine-url>`. It's the
      public entry point, so `--allow-unauthenticated` (or front it with IAP).
- [ ] Smoke test: the orchestrator returns a non-null `fare_quote`. Use
      `.claude/skills/gcp-smoke-test/smoke-test.sh`.

## 5. Secrets

- [ ] If any `GEMINI_API_KEY` remains (e.g. engine before the Vertex change), store
      it in **Secret Manager** and mount via `--set-secrets`, not `--set-env-vars`.
- [ ] Confirm `.env` is never deployed (it's git-ignored; buildpacks shouldn't copy
      it, but verify).

## 6. CI/CD (currently none)

Keyless GitHub Actions deploys via **Workload Identity Federation** (no SA keys).

- [x] WIF pool + GitHub OIDC provider + deploy SA + bindings — scripted in
      [`scripts/setup-wif.sh`](../scripts/setup-wif.sh) (you run it once with your
      GitHub owner). Restricts impersonation to repos under your GitHub owner.
- [x] Engine workflow (`travel-fare-engine/.github/workflows/deploy.yml`):
      `go vet` + `go test ./...` (unit + tripwire + eval) → deploy → set HOST_URL →
      authenticated smoke test of the agent card.
- [x] Orchestrator workflow (`.github/workflows/deploy.yml`): `pytest` → ADK
      evals (Vertex AI via WIF) → resolve engine URL → deploy → authenticated
      `/list-apps` smoke test. Deploy is gated on tests **and** evals.
- [ ] **You:** push both repos to GitHub, run `setup-wif.sh` (re-run it if you
      set it up before the evals job existed — the deployer SA now also needs
      `roles/aiplatform.user`), and set the four repo variables it prints
      (`GCP_PROJECT`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`) in
      each repo.

## 7. Observability & resilience

- [ ] Health/readiness checks — the smoke-test skill references `/health` and
      `/agents`; confirm ADK's FastAPI app actually exposes those (or adjust).
- [ ] Cloud Trace / structured logging — the engine already pulls OpenTelemetry
      deps transitively; wire an exporter if you want traces.
- [ ] Decide ingress posture for the engine (`--ingress all` + auth today; see
      DECISIONS.md §11 for the `internal` trade-off).
- [ ] Automated rollback on smoke-test failure (DECISIONS.md §12 notes it's manual
      today).

---

## TL;DR

| Layer            | State today                                      |
| ---------------- | ----------------------------------------------- |
| App logic (local)| ✅ implemented & verified                        |
| Contract + evals | ✅ tripwires both sides; eval scaffolding        |
| Models in prod   | ✅ both Vertex-capable by env (engine code updated) |
| Containers       | ✅ engine Dockerfile added; orchestrator via buildpack |
| IAM / SAs        | ⚙️ scripted (`scripts/setup-gcp.sh`), not yet run |
| Deployed services| ❌ none (needs a GCP project)                     |
| Secrets          | ✅ none needed (Vertex via ADC; no API keys in prod) |
| CI/CD + WIF      | ⚙️ workflows + WIF script written; you run setup-wif.sh + set repo vars |
| Observability    | ❌ not configured                                 |

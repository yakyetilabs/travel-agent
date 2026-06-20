# Lessons Learned — building & shipping a two-repo A2A system

A field guide to the walls we hit building this system and how we got past them.
If you're learning **A2A**, **Google ADK**, or **deploying agents to Cloud Run +
Vertex AI**, you will likely hit several of these — so each entry is written as
*symptom → cause → fix → the concept underneath*.

The system: a Python/ADK orchestrator (`travel-agent`) that calls a Go pricing
microservice (`travel-fare-engine`) over A2A. Read [ARCHITECTURE.md](ARCHITECTURE.md)
first for the shape; this doc is the "why was that so hard" companion.

---

## A2A protocol

### 1. `METHOD_NOT_FOUND (-32601)` even though the A2A call connected

**Symptom.** The orchestrator resolved the engine's agent card and sent a request,
but got back JSON-RPC `-32601 METHOD_NOT_FOUND`.

**Cause.** A2A has been implemented across SDK generations with **different
JSON-RPC method names**. The Go server (`a2a-go/v2`) `a2asrv.NewJSONRPCHandler`
dispatches on gRPC-style names (`SendMessage`, `SendStreamingMessage`, `GetTask`).
The Python client (`a2a-sdk`, protocol 0.3.0) sends the **standard** names
(`message/send`, `message/stream`, `tasks/get`). They never matched.

**Fix.** Serve the standard names with the compatibility handler:
`a2acompat/a2av0.NewJSONRPCHandler(requestHandler)` instead of
`a2asrv.NewJSONRPCHandler(...)`. Same `RequestHandler`, standard wire names.

**Concept.** "Speaks A2A" is not enough — the **transport encoding and method
names must match** between client and server SDKs. When two SDKs disagree, look
for a compat/legacy layer before rewriting anything.

### 2. Agent card rejected: `skills[].id`, `skills[].tags`, `url` required

**Symptom.** `Failed to validate agent card structure … skills[0].id Field
required … url Field required`.

**Cause.** Our hand-written `agent-card.json` didn't match the A2A **`AgentCard`**
schema the client validates against. The real schema needs a **top-level `url`**
(+ optional `preferredTransport`, `additionalInterfaces`) and skills with
`id` + `name` + `description` + `tags`. Our card used a made-up
`supportedInterfaces` field (silently ignored) and skills without `id`/`tags`.

**Fix.** Match the real schema: top-level `url`, `preferredTransport: "JSONRPC"`,
and `skills: [{ id, name, description, tags, ... }]`. We read the SDK's pydantic
`AgentCard` model directly to get the exact required fields rather than guessing.

**Concept.** The agent card is a **typed contract**, not free-form JSON. When in
doubt, read the consumer's schema (the pydantic/proto model) — the error messages
point at exact field paths.

### 3. Card advertises `http://localhost:8081` in production → discovery breaks

**Symptom.** Locally fine; deployed, the orchestrator couldn't reach the engine.

**Cause.** The static card's `url` is baked at build time. Cloud Run does **not**
inject the service URL into a standard env var (only `K_SERVICE`, `K_REVISION`,
`K_CONFIGURATION`), so a static `localhost` URL ships to production.

**Fix.** Rewrite the card's `url` at startup from a `HOST_URL` env var (falling
back to `http://localhost:$PORT` locally). Set `HOST_URL` on the deployed service.

**Concept.** Anything self-referential in a service (its own public URL) must be
**injected at deploy time**, never hard-coded.

---

## Google ADK

### 4. `output_schema` silently disables tools and sub-agent delegation

**Symptom.** An agent with both `tools=[...]` and `output_schema=...` never calls
its tools (or an orchestrator stops after the first sub-agent).

**Cause.** In ADK, setting `output_schema` puts the `LlmAgent` in
structured-output mode, which **disables tool calling and `transfer_to_agent`**.

**Fix.** Split responsibilities:
- Tool-using agents emit JSON-as-text and write to state via `output_key` (no
  `output_schema`). This is what `pricing`, `policy`, and `fare_prep` do.
- A final agent with `output_schema` (no tools) validates the final shape.

**Concept.** This is *why* the engine has a **two-agent pipeline** (pricing →
formatter) and the orchestrator ends in a **finalizer**. The architecture is
shaped by this constraint, not by preference.

### 5. Circular import when an ADK multi-agent app loads

**Symptom.** `cannot import name 'root_agent' from partially initialized module …
(most likely due to a circular import)` when the app loads (not at startup — at
first session/run).

**Cause.** The orchestrator package imported the finalizer agent, and the
finalizer imported a schema from the **orchestrator** package — a cycle. ADK loads
the app as a top-level module (`orchestrator`) while the code also imports it as
`agents.orchestrator`, so the package initialized **twice under two names**,
turning the cycle fatal.

**Fix.** Point the dependency one way only: define the shared schema in the
**producer** (`finalizer/schemas.py`) and have the orchestrator package re-export
it. A sub-agent must never import from its parent package.

**Concept.** In a multi-agent package, dependencies should flow **parent →
children**, never back. We verified the fix with an AST-based import-cycle check
(stdlib only) — a cheap way to catch this class of bug without running the stack.

### 6. ADK apps are loaded by directory — mind the `__init__.py`

**Symptom.** Works with `adk web`, breaks with `adk eval` (or vice versa) with
import errors.

**Cause.** `adk eval` loads an agent's `__init__.py` via
`spec_from_file_location`, which does **not** put the repo root on `sys.path`, so
absolute imports (`from tools.x import …`) fail.

**Fix.** Each agent's `__init__.py` inserts the repo root on `sys.path` and does
`from . import agent`. (See `.claude/skills/adk-agent-pattern/SKILL.md`.)

**Concept.** ADK's directory-based loader has different import contexts per
command. Match the canonical `__init__.py` pattern so every entry point works.

---

## Deterministic core & pipeline design

### 7. A deterministic check that needs a value computed later in the pipeline

**Symptom.** The budget policy "ran" but never actually enforced anything — it was
a stub that always passed.

**Cause.** Policy ran **before** the fare engine, so the real `total_fare` didn't
exist yet. You can't budget-check a number you don't have.

**Fix.** Reorder the `SequentialAgent`: `intake → fare_prep → fare_engine →
policy → finalizer`. Now policy reads the real quoted fare.

**Concept.** In a deterministic pipeline, **sequence is part of the design**.
Order stages so each has the inputs its tools require; don't paper over a missing
input with a stub.

### 8. The human vocabulary ≠ the engine vocabulary

**Symptom.** The engine needs `base_distance_miles`, `advance_purchase_days`,
`route_type`, `season_code`, `booking_class`; intake only collects airports,
dates, and cabin.

**Cause.** A pricing engine shouldn't know about airports or calendars; a traveler
shouldn't know about booking classes. Something must translate.

**Fix.** A deterministic `fare_prep` tool (`tools/fare_request.py`) derives the
engine's inputs from human inputs (haversine distance, country→route, month→season,
a booking-class ladder that respects the engine's advance-purchase minimums).

**Concept.** The translation layer between a conversational front-end and a typed
back-end is **its own component**, and it should be deterministic — not something
you hope the LLM gets right.

---

## Cloud Run deployment

### 9. Buildpack fails: `MANIFEST_UNKNOWN: Failed to fetch "3.12.11"`

**Symptom.** `gcloud run deploy --source .` fails in the build, before installing
deps.

**Cause.** `.python-version` pinned an exact patch (`3.12.11`) that Cloud Run's
buildpack runtime registry didn't have. Also, the Python buildpack didn't natively
install `uv`.

**Fix.** Use a **Dockerfile** instead of buildpacks: `FROM python:3.12.11-slim`
(Docker Hub *does* have it) + install `uv` + `uv sync --frozen`. When a Dockerfile
is present, `--source .` uses it automatically.

**Concept.** Buildpacks are convenient until they aren't (exact runtime versions,
non-standard package managers). A Dockerfile trades convenience for **control and
reproducibility** — usually the right call for a portfolio/production service.

### 10. Private Cloud Run service: getting an ID token to call it

**Symptom.** Calling a `--no-allow-unauthenticated` service returns 401/403.

**Cause.** Cloud Run requires a Google-signed **ID token** whose **audience** is
the service URL, from a caller with `roles/run.invoker`.

**Fix.** Service-to-service (orchestrator→engine): mint an ID token for the
engine's URL as audience (`google.oauth2.id_token`), attach it as a Bearer header,
and grant the caller SA `run.invoker`. For humans/testing, `gcloud run services
proxy` handles auth for you.

**Concept.** Private Cloud Run auth = **ID token (aud = service URL) + IAM
`run.invoker`**. Two independent things; both required.

### 11. Local dev breaks because the client always tries to mint a token

**Symptom.** Running the orchestrator locally against a local engine fails trying
to fetch a GCP ID token.

**Cause.** The A2A client wrapped *every* call in ID-token auth, even for
`http://localhost`, where there's no GCP identity and no auth needed.

**Fix.** Skip token auth when `FARE_ENGINE_URL` is localhost.

**Concept.** Auth that's mandatory in production is often *impossible* locally.
Branch on environment so local dev stays frictionless.

---

## CI/CD on GCP

### 12. CI deploy: `caller does not have permission to act as service account …`

**Symptom.** Manual `gcloud run deploy` works from your laptop; the *identical*
command fails in GitHub Actions with `act as service account`.

**Cause.** `--source` deploys build via **Cloud Build**, which runs as the project's
**Compute Engine default SA**. Your CI identity could deploy but couldn't *act as*
the build SA. (It worked manually because you're an owner.)

**Fix.** Grant the CI deployer `roles/iam.serviceAccountUser` on
`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`. (Now in `setup-wif.sh`.)

**Concept.** A build that works for an owner can fail for a least-privilege CI SA.
Cloud Run source builds need **two** act-as grants: the **runtime** SA (`--service-account`)
*and* the **build** SA (Cloud Build). Enumerate every identity a command assumes.

### 13. Keyless CI auth with Workload Identity Federation (no JSON keys)

**Symptom.** You want CI to deploy without committing a service-account key.

**Cause/solution.** WIF lets GitHub Actions exchange its short-lived OIDC token for
GCP credentials by impersonating a deployer SA — **no long-lived keys anywhere**.
Setup (`scripts/setup-wif.sh`): a Workload Identity **Pool** + **OIDC Provider**
(issuer `token.actions.githubusercontent.com`), an attribute-condition restricting
to your GitHub org, and a `workloadIdentityUser` binding on the deployer SA. In the
workflow: `permissions: id-token: write` + `google-github-actions/auth`.

**Concept.** Service-account key files are the thing to avoid. **WIF / OIDC
federation** is the modern, keyless pattern for CI → cloud.

---

## Tooling / dev-loop

### 14. ADK dev UI returns 403 on session create behind the Cloud Run proxy

**Symptom.** `gcloud run services proxy` works; the browser dev UI shows "Failed
to create session" (`POST …/sessions` → 403), but the **same call via `curl`
returns 200**.

**Cause.** Identical IAM identity for both (the proxy), so it's **not** a
permissions problem — it's an app-level rejection of the browser request
(origin/headers) when the dev UI runs behind the proxy on a deployed service.

**Fix (pragmatic).** Drive the pipeline via the **API** (`POST /run`) — that's the
real end-to-end test anyway. The dev UI is a convenience, not the product.

**Concept.** When the same request succeeds via `curl` but fails from a browser,
suspect **origin/CSRF/app-layer** handling, not IAM. Don't reach for IAM changes
for an app-layer 403.

---

## Meta-lessons

- **Read the dependency's source, don't guess.** Several fixes (the A2A method
  names, the AgentCard schema, the ADK FastAPI routes) came from reading the
  installed library, not documentation or memory.
- **Verify before you redeploy.** We reproduced the import cycle with a stdlib
  script and confirmed builder→engine compatibility offline before paying for a
  cloud build. Cheap local checks beat slow cloud round-trips.
- **Peel the onion.** The A2A call failed for three *different* reasons in
  sequence (card schema → method names → … ). Fix one, re-test, read the next
  error. Each error was real progress.

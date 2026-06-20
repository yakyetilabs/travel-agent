# Deploy your own — from clone to running in the cloud

An ordered, copy-pasteable runbook for standing up **your own** copy of this
two-repo A2A system on Google Cloud. Everything is parameterized — set the values
in [§3](#3-configure-your-values) once and the rest is paste-friendly.

If a step fails, check [LESSONS.md](LESSONS.md) — most of the walls you can hit are
documented there with fixes.

> **Cost:** essentially $0 idle. Cloud Run scales to zero (you pay only while
> serving a request); Vertex AI is per-token (cents per run); the only standing
> cost is a few cents/month of container image storage. Set a budget alert anyway
> (§Cost), and run [teardown](#teardown) when you're done.

---

## 0. Prerequisites

- A **Google Cloud project** with billing enabled, and `gcloud` installed +
  authenticated:
  ```bash
  gcloud auth login
  gcloud auth application-default login
  ```
- **Go 1.26+** (to test the engine) and **uv** (to test the orchestrator) — only
  needed if you want to run tests / run locally.
- You do **not** need Docker locally — Cloud Build builds the images.
- For local model calls, a **Gemini API key** (https://aistudio.google.com). In
  the cloud we use **Vertex AI** (no key).

## 1. Clone both repos

```bash
git clone https://github.com/yakyetilabs/travel-agent.git
git clone https://github.com/yakyetilabs/travel-fare-engine.git
```

(Replace with your forks if you've forked them.)

## 2. (Optional) Run locally first

Prove the pipeline works on your machine before touching the cloud.

```bash
# Terminal 1 — the engine
cd travel-fare-engine
export GEMINI_API_KEY=your-key
go run ./cmd/server          # serves A2A on :8081

# Terminal 2 — the orchestrator
cd travel-agent
cp .env.example .env         # set GEMINI_API_KEY and FARE_ENGINE_URL=http://localhost:8081
uv sync
adk web                      # open http://localhost:8000, pick "orchestrator"
```

The engine's pure math also runs with zero setup: `cd travel-fare-engine && go test ./...`.

## 3. Configure your values

Set these in your shell; every command below uses them.

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
export GITHUB_OWNER=your-github-username     # only needed for CI (§9)
```

## 4. Provision GCP (one-time)

Creates the runtime service accounts, grants Vertex access, enables APIs:

```bash
cd travel-agent
PROJECT_ID=$PROJECT_ID REGION=$REGION ./scripts/setup-gcp.sh
```

## 5. Deploy the engine (private)

```bash
cd ../travel-fare-engine
gcloud run deploy travel-fare-engine \
  --source . --project "$PROJECT_ID" --region "$REGION" \
  --service-account "travel-fare-engine-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --no-allow-unauthenticated \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --timeout=120 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}"
```
First deploy will offer to create an Artifact Registry repo — say **Y**.

## 6. Point the engine at itself + allow the orchestrator to call it

```bash
export ENGINE_URL=$(gcloud run services describe travel-fare-engine \
  --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')

# Advertise the real URL in the agent card (merge, don't wipe the Vertex vars):
gcloud run services update travel-fare-engine \
  --project "$PROJECT_ID" --region "$REGION" --update-env-vars "HOST_URL=${ENGINE_URL}/"

# Let the orchestrator's SA invoke the (private) engine:
gcloud run services add-iam-policy-binding travel-fare-engine \
  --project "$PROJECT_ID" --region "$REGION" \
  --member "serviceAccount:travel-prequal-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/run.invoker
```

## 7. Deploy the orchestrator (private)

```bash
cd ../travel-agent
gcloud run deploy travel-prequal \
  --source . --project "$PROJECT_ID" --region "$REGION" \
  --service-account "travel-prequal-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --no-allow-unauthenticated \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=1Gi --timeout=300 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},FARE_ENGINE_URL=${ENGINE_URL}"
```

## 8. Smoke test the whole pipeline

Both services are private, so use the authenticated proxy:

```bash
# Terminal A — proxy (leave running)
gcloud run services proxy travel-prequal --project "$PROJECT_ID" --region "$REGION" --port 8080
```

```bash
# Terminal B — run a trip end to end
SID=$(curl -s -X POST http://127.0.0.1:8080/apps/orchestrator/users/u1/sessions \
  -H "Content-Type: application/json" -d '{}' | jq -r .id)

cat > /tmp/run.json <<JSON
{
  "appName": "orchestrator", "userId": "u1", "sessionId": "$SID",
  "newMessage": {"role": "user", "parts": [{"text": "Test Traveler (test@example.com, employee 00001, Engineering) wants to fly JFK to LAX departing 2026-09-15 returning 2026-09-20, economy, for a client meeting. One adult."}]}
}
JSON

curl -s -X POST http://127.0.0.1:8080/run -H "Content-Type: application/json" -d @/tmp/run.json \
  | jq -r '.[-1].content.parts[].text // empty'
```

Success = a `TravelQualificationOutput` with a populated `fare_quote` (base fare,
taxes, total) and a `final_decision`.

## 9. (Optional) Keyless CI/CD with GitHub Actions

Push to `main` → test → build → deploy → smoke test, with **no service-account
keys** (Workload Identity Federation).

```bash
cd travel-agent
PROJECT_ID=$PROJECT_ID GITHUB_OWNER=$GITHUB_OWNER ./scripts/setup-wif.sh
```

Then in **both** repos (Settings → Secrets and variables → Actions → **Variables**),
add the four values the script prints: `GCP_PROJECT`, `GCP_REGION`,
`GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`. Set the variables **before** pushing to `main`
(the deploy job needs them). After that, `git push origin main` deploys
automatically. See the workflows in each repo's `.github/workflows/deploy.yml`.

---

## Cost

- **Idle:** ~$0 (scale-to-zero; only image storage, cents/month).
- **Per demo run:** a handful of Gemini Flash calls = sub-cent.
- **Guardrails baked in:** `--min-instances=0` + `--max-instances=2`.
- **Set a budget alert:** Console → Billing → Budgets & alerts → e.g. $10/month
  with email at 50/90/100%.

## Teardown

When you're done, delete everything so nothing can bill:

```bash
cd travel-agent
PROJECT_ID=$PROJECT_ID REGION=$REGION ./scripts/teardown.sh
```

It removes both Cloud Run services, the three service accounts, and the Workload
Identity pool/provider. It does **not** delete your project or disable billing —
do that yourself if the project was only for this.

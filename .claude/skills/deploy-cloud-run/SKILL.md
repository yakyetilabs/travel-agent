---
name: deploy-cloud-run
description: Use when deploying any agent to Cloud Run, or when troubleshooting a failed Cloud Run deploy. Knows the source-deploy buildpack flow and the env var contract this project uses.
---

# Cloud Run deploy

## Required env vars on Cloud Run

- GOOGLE_CLOUD_PROJECT
- GOOGLE_CLOUD_LOCATION (us-central1)
- GOOGLE_GENAI_USE_VERTEXAI=TRUE (NOT the AI Studio key in prod)
- FARE_ENGINE_URL

## Deploy command

```bash
gcloud run deploy travel-prequal \
  --source . \
  --region us-central1 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project),GOOGLE_CLOUD_LOCATION=us-central1,FARE_ENGINE_URL=<your-fare-engine-url> \
  --allow-unauthenticated
```

## Smoke test

After deploy, hit <service-url>/health and <service-url>/agents to confirm
both return 200.

## Common failures

"Permission denied on aiplatform" → Cloud Run service account needs
roles/aiplatform.user.

## Cold start over 30s → bump --cpu 2 --memory 2Gi.

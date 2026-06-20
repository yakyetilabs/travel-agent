#!/usr/bin/env bash
#
# One-time GCP provisioning for the Travel Pre-Qualification system.
# Idempotent: safe to re-run. It does NOT deploy services — it prepares the
# project, identities, and IAM so the two `gcloud run deploy` commands work.
#
# Prereqs (you, once):
#   gcloud auth login
#   gcloud auth application-default login
#
# Usage:
#   PROJECT_ID=my-proj REGION=us-central1 ./scripts/setup-gcp.sh
#
# What it does:
#   1. Enables required APIs.
#   2. Creates two runtime service accounts (engine, orchestrator).
#   3. Grants the engine + orchestrator SAs roles/aiplatform.user (Vertex AI).
#
# NOT done here (because they need the deployed services to exist first):
#   - roles/run.invoker for the orchestrator SA on the engine service
#     (printed as a follow-up command at the end).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
ENGINE_SA_NAME="travel-fare-engine-sa"
ORCH_SA_NAME="travel-prequal-sa"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: set PROJECT_ID (env) or 'gcloud config set project <id>'." >&2
  exit 1
fi

ENGINE_SA="${ENGINE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ORCH_SA="${ORCH_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

cat <<EOF
About to provision:
  Project : ${PROJECT_ID}
  Region  : ${REGION}
  Engine SA      : ${ENGINE_SA}
  Orchestrator SA: ${ORCH_SA}
EOF
read -r -p "Proceed? [y/N] " ans
[[ "${ans}" == "y" || "${ans}" == "Y" ]] || { echo "Aborted."; exit 0; }

echo "==> Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  --project "${PROJECT_ID}"

create_sa() {
  local name="$1" display="$2" email="$3"
  if gcloud iam service-accounts describe "${email}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "    SA ${email} already exists; skipping."
  else
    gcloud iam service-accounts create "${name}" \
      --display-name "${display}" --project "${PROJECT_ID}"
  fi
}

echo "==> Creating service accounts..."
create_sa "${ENGINE_SA_NAME}" "Travel Fare Engine runtime" "${ENGINE_SA}"
create_sa "${ORCH_SA_NAME}"   "Travel Pre-Qual orchestrator runtime" "${ORCH_SA}"

echo "==> Granting Vertex AI access (roles/aiplatform.user)..."
for sa in "${ENGINE_SA}" "${ORCH_SA}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${sa}" \
    --role roles/aiplatform.user \
    --condition=None >/dev/null
done

cat <<EOF

Done. Next steps (in order). Cost guardrails baked in: scale-to-zero
(--min-instances=0) + a low --max-instances cap. Set a billing budget alert too.

1. Deploy the ENGINE (from the travel-fare-engine repo), private:
   gcloud run deploy travel-fare-engine \\
     --source . --region ${REGION} \\
     --service-account ${ENGINE_SA} \\
     --no-allow-unauthenticated \\
     --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --timeout=120 \\
     --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}

2. Point the engine's agent card at its own URL (no rebuild; --update-env-vars
   MERGES so it won't wipe the Vertex vars from step 1):
   ENGINE_URL=\$(gcloud run services describe travel-fare-engine --region ${REGION} --format='value(status.url)')
   gcloud run services update travel-fare-engine --region ${REGION} \\
     --update-env-vars HOST_URL="\${ENGINE_URL}/"

3. Allow the orchestrator to invoke the engine:
   gcloud run services add-iam-policy-binding travel-fare-engine \\
     --region ${REGION} \\
     --member serviceAccount:${ORCH_SA} \\
     --role roles/run.invoker

4. Deploy the ORCHESTRATOR (from this repo), PRIVATE (demo via
   'gcloud run services proxy travel-prequal --region ${REGION}'):
   gcloud run deploy travel-prequal \\
     --source . --region ${REGION} \\
     --service-account ${ORCH_SA} \\
     --no-allow-unauthenticated \\
     --min-instances=0 --max-instances=2 --cpu=1 --memory=1Gi --timeout=300 \\
     --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},FARE_ENGINE_URL="\${ENGINE_URL}"
EOF

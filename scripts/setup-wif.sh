#!/usr/bin/env bash
#
# One-time Workload Identity Federation (WIF) setup for keyless GitHub Actions
# deploys. Run AFTER scripts/setup-gcp.sh (which created the runtime SAs).
#
# WIF lets GitHub Actions impersonate a GCP service account using a short-lived
# OIDC token from GitHub — no service-account JSON keys anywhere. This is the
# "no service account keys" principle the docs call for.
#
# Prereq: gcloud auth login (as a project owner/editor).
#
# Usage:
#   PROJECT_ID=travel-booking-agent GITHUB_OWNER=<your-github-username-or-org> \
#     ./scripts/setup-wif.sh
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
GITHUB_OWNER="${GITHUB_OWNER:?set GITHUB_OWNER to your GitHub username or org}"
POOL="github-pool"
PROVIDER="github-provider"
DEPLOYER_NAME="github-deployer"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: set PROJECT_ID or 'gcloud config set project <id>'." >&2; exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DEPLOYER_SA="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SAS=("travel-fare-engine-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
             "travel-prequal-sa@${PROJECT_ID}.iam.gserviceaccount.com")

cat <<EOF
About to set up keyless CI deploys:
  Project        : ${PROJECT_ID} (number ${PROJECT_NUMBER})
  GitHub owner   : ${GITHUB_OWNER}  (any repo under this owner may deploy)
  Deployer SA    : ${DEPLOYER_SA}
  WIF pool/prov  : ${POOL} / ${PROVIDER}
EOF
read -r -p "Proceed? [y/N] " ans
[[ "${ans}" == "y" || "${ans}" == "Y" ]] || { echo "Aborted."; exit 0; }

echo "==> Enabling IAM credentials + STS APIs..."
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com --project "${PROJECT_ID}"

echo "==> Creating deployer service account..."
gcloud iam service-accounts describe "${DEPLOYER_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "${DEPLOYER_NAME}" \
       --display-name "GitHub Actions deployer" --project "${PROJECT_ID}"

echo "==> Granting deploy permissions to ${DEPLOYER_SA}..."
# What 'gcloud run deploy --source' needs: deploy services, run Cloud Build,
# upload source to GCS, push images to Artifact Registry.
for role in roles/run.admin \
            roles/cloudbuild.builds.editor \
            roles/storage.admin \
            roles/artifactregistry.admin \
            roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${DEPLOYER_SA}" --role "${role}" --condition=None >/dev/null
done

echo "==> Allowing the deployer to act as the runtime service accounts..."
# Required so 'gcloud run deploy --service-account <runtime-sa>' is permitted.
for sa in "${RUNTIME_SAS[@]}"; do
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --member "serviceAccount:${DEPLOYER_SA}" \
    --role roles/iam.serviceAccountUser --project "${PROJECT_ID}" >/dev/null
done

echo "==> Allowing the deployer to act as the Cloud Build service account..."
# 'gcloud run deploy --source' builds the image via Cloud Build, which runs as the
# Compute Engine default SA. The deployer must be able to act as it, or the build
# fails with: "caller does not have permission to act as service account ...".
gcloud iam service-accounts add-iam-policy-binding \
  "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --member "serviceAccount:${DEPLOYER_SA}" \
  --role roles/iam.serviceAccountUser --project "${PROJECT_ID}" >/dev/null

echo "==> Creating Workload Identity Pool + GitHub OIDC provider..."
gcloud iam workload-identity-pools describe "${POOL}" --location=global --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "${POOL}" \
       --location=global --display-name="GitHub Actions Pool" --project "${PROJECT_ID}"

gcloud iam workload-identity-pools providers describe "${PROVIDER}" \
  --location=global --workload-identity-pool="${POOL}" --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
       --location=global --workload-identity-pool="${POOL}" \
       --display-name="GitHub provider" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
       --attribute-condition="assertion.repository_owner=='${GITHUB_OWNER}'" \
       --project "${PROJECT_ID}"

echo "==> Binding the GitHub principalSet to impersonate the deployer SA..."
# Only tokens from repos owned by ${GITHUB_OWNER} (enforced by the provider's
# attribute-condition above) can impersonate the deployer.
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository_owner/${GITHUB_OWNER}" \
  --project "${PROJECT_ID}" >/dev/null

PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

cat <<EOF

Done. Set these as GitHub Actions *repository variables* (Settings → Secrets and
variables → Actions → Variables) in BOTH repos (travel-fare-engine, travel-agent):

  GCP_PROJECT       = ${PROJECT_ID}
  GCP_REGION        = us-central1
  GCP_WIF_PROVIDER  = ${PROVIDER_RESOURCE}
  GCP_DEPLOY_SA     = ${DEPLOYER_SA}

These are NOT secrets (no keys), so repository *variables* are fine. Push to main
and the workflow in each repo will test, deploy, and smoke-test automatically.
EOF

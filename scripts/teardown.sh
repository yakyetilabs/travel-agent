#!/usr/bin/env bash
#
# Tear down everything this project created on Google Cloud, so nothing can bill.
# Reverses setup-gcp.sh + setup-wif.sh + the two deploys.
#
# Removes:
#   - Cloud Run services: travel-prequal, travel-fare-engine
#   - Service accounts:    travel-prequal-sa, travel-fare-engine-sa, github-deployer
#                          (project-level IAM bindings vanish with the SAs)
#   - Workload Identity:   provider github-provider, pool github-pool
#   - (optional) Artifact Registry repo: cloud-run-source-deploy  [--with-images]
#
# Does NOT delete the project or disable billing — do that yourself if the project
# existed only for this.
#
# Usage:
#   PROJECT_ID=my-proj REGION=us-central1 ./scripts/teardown.sh [--with-images]
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${REGION:-us-central1}"
WITH_IMAGES="${1:-}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: set PROJECT_ID or 'gcloud config set project <id>'." >&2; exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' 2>/dev/null || true)"
RUN_SERVICES=(travel-prequal travel-fare-engine)
SERVICE_ACCOUNTS=(
  "travel-prequal-sa@${PROJECT_ID}.iam.gserviceaccount.com"
  "travel-fare-engine-sa@${PROJECT_ID}.iam.gserviceaccount.com"
  "github-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
)

cat <<EOF
!!! DESTRUCTIVE !!!  This will DELETE from project '${PROJECT_ID}':
  - Cloud Run services : ${RUN_SERVICES[*]}
  - Service accounts   : travel-prequal-sa, travel-fare-engine-sa, github-deployer
  - Workload Identity  : provider github-provider, pool github-pool
$( [[ "${WITH_IMAGES}" == "--with-images" ]] && echo "  - Artifact Registry  : cloud-run-source-deploy (and its images)" )

Type the project ID to confirm:
EOF
read -r confirm
[[ "${confirm}" == "${PROJECT_ID}" ]] || { echo "Mismatch. Aborted."; exit 1; }

# Helper: run a delete, ignore "not found" so the script is idempotent.
del() { echo "  - $*"; "$@" --quiet 2>/dev/null || echo "    (already gone or not found)"; }

echo "==> Deleting Cloud Run services..."
for svc in "${RUN_SERVICES[@]}"; do
  del gcloud run services delete "${svc}" --project "${PROJECT_ID}" --region "${REGION}"
done

echo "==> Deleting the Workload Identity provider + pool..."
del gcloud iam workload-identity-pools providers delete github-provider \
  --project "${PROJECT_ID}" --location global --workload-identity-pool github-pool
del gcloud iam workload-identity-pools delete github-pool \
  --project "${PROJECT_ID}" --location global

echo "==> Deleting service accounts (their IAM bindings go with them)..."
for sa in "${SERVICE_ACCOUNTS[@]}"; do
  del gcloud iam service-accounts delete "${sa}" --project "${PROJECT_ID}"
done

if [[ "${WITH_IMAGES}" == "--with-images" ]]; then
  echo "==> Deleting the Artifact Registry repo + images..."
  del gcloud artifacts repositories delete cloud-run-source-deploy \
    --project "${PROJECT_ID}" --location "${REGION}"
fi

cat <<EOF

Teardown complete.

Left intact on purpose:
  - The project '${PROJECT_ID}' itself and its billing.
  - Enabled APIs (free to leave on).
  - The compute default SA (${PROJECT_NUMBER:-<num>}-compute@developer.gserviceaccount.com) — a GCP built-in.
$( [[ "${WITH_IMAGES}" != "--with-images" ]] && echo "  - The Artifact Registry repo (re-run with --with-images to remove it)." )

To remove everything, delete the project:  gcloud projects delete ${PROJECT_ID}
EOF

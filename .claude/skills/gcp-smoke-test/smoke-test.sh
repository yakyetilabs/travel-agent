#!/usr/bin/env bash
# GCP smoke test for the travel-prequal orchestrator.
# Verifies infrastructure wiring BEFORE debugging agent logic:
#   1. gcloud is pointed at a project
#   2. the fare engine's agent card is reachable WITH an ID token
#   3. (interactive) the orchestrator can be launched via `adk web`
#
# Usage:
#   ./.claude/skills/gcp-smoke-test/smoke-test.sh            # full
#   SKIP_AGENT_CHECK=1 ./.claude/skills/gcp-smoke-test/smoke-test.sh   # CI / non-interactive
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "OK: $*"; }

# Load .env if present (FARE_ENGINE_URL etc.)
if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi

# --- 1. gcloud project ------------------------------------------------------
command -v gcloud >/dev/null 2>&1 || fail "gcloud not installed"
PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
[[ -n "${PROJECT}" && "${PROJECT}" != "(unset)" ]] || fail "no gcloud project set (gcloud config set project ...)"
ok "gcloud project: ${PROJECT}"

# --- 2. fare engine reachability -------------------------------------------
: "${FARE_ENGINE_URL:?FARE_ENGINE_URL not set (see .env.example)}"
CARD_URL="${FARE_ENGINE_URL%/}/.well-known/agent-card.json"

# Mint an ID token for the engine's audience. Works with a service-account key
# (GOOGLE_APPLICATION_CREDENTIALS) or any identity that can mint ID tokens.
TOKEN="$(gcloud auth print-identity-token 2>/dev/null || true)"
if [[ -z "${TOKEN}" ]]; then
  echo "  note: could not mint an ID token via gcloud; trying unauthenticated (local dev)"
  AUTH_ARGS=()
else
  AUTH_ARGS=(-H "Authorization: Bearer ${TOKEN}")
fi

HTTP_CODE="$(curl -s -o /tmp/agent-card.json -w '%{http_code}' "${AUTH_ARGS[@]}" "${CARD_URL}" || true)"
[[ "${HTTP_CODE}" == "200" ]] || fail "agent card not reachable at ${CARD_URL} (HTTP ${HTTP_CODE})"

# The card's advertised interface URL must NOT be a localhost address when the
# engine is deployed — a common misconfiguration (engine must set HOST_URL).
if command -v jq >/dev/null 2>&1; then
  CARD_NAME="$(jq -r '.name' /tmp/agent-card.json)"
  IFACE_URL="$(jq -r '.url // empty' /tmp/agent-card.json)"
  ok "fare engine reachable: ${CARD_NAME} (interface: ${IFACE_URL:-n/a})"
  if [[ "${FARE_ENGINE_URL}" != http://localhost* && "${IFACE_URL}" == http://localhost* ]]; then
    fail "engine card advertises a localhost URL (${IFACE_URL}) but is deployed remotely — set HOST_URL on the engine"
  fi
else
  ok "fare engine reachable (install jq for card detail)"
fi

# --- 3. agent connectivity (interactive) -----------------------------------
if [[ "${SKIP_AGENT_CHECK:-0}" == "1" ]]; then
  echo "SKIP: agent check (SKIP_AGENT_CHECK=1)"
  exit 0
fi
echo
echo "Next (interactive): run 'adk web', open http://localhost:8000, pick 'orchestrator',"
echo "and send a sample trip. Confirm the response includes a non-null fare_quote."

name: gcp-smoke-test
description: Use to verify GCP infrastructure is correctly wired before debugging agent issues. Checks fare engine reachability and agent connectivity.

---

# GCP smoke test

## Run it

```bash
./.claude/skills/gcp-smoke-test/smoke-test.sh
```

## What each check means

1. gcloud project — confirms you're pointed at the right project.

2. Fare engine reachability — hits $FARE_ENGINE_URL/.well-known/agent-card.json
   with an ID token.

3. Agent connectivity — interactive check via adk web.

## When to skip

Skip the agent check (step 3) in CI or non-interactive contexts.

The `smoke-test.sh` would be a bash script checking environment variables, curl with token, etc.

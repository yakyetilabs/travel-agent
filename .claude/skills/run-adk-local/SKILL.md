name: run-adk-local
description: Use whenever the user asks to run, test, or debug an agent locally. Covers `adk run`, `adk web`, multi-process A2A setup, and the eval workflow.

---

# Running ADK locally

## Single agent (CLI)

```bash
adk run agents/<name>
```

## Web dev UI (preferred for demos)

```bash
adk web
```

Open http://localhost:8000 and pick the agent from the dropdown.

A2A: fare_engine + orchestrator together
Two terminals:

```bash
# Terminal 1 – start the Go fare engine locally on port 8081 (see its README)

# Terminal 2
adk web
```

The orchestrator references the fare_engine via RemoteA2AAgent at
`http://localhost:8081`.

## Evals

Eval sets live in eval/<agent>.evalset.json. Run:

```bash
adk eval agents/<name> eval/<agent>.evalset.json
```

Always create at least 3 eval cases per agent before considering it "done".

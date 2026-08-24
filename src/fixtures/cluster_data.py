"""
fixtures/cluster_data.py — Fake "cluster" data so we can build and test the
graph's logic without needing a real Kubernetes cluster yet (that's Phase 2).

This simulates three data sources a real investigation would query:
  - pod_events: what Kubernetes itself reported about the pod's lifecycle
  - recent_deploys: what changed recently (a common root cause)
  - logs: raw application log lines from the crashing pod

The scenario baked in here: a bad deploy introduced a config that makes the
app crash on startup. The evidence needed to find this is SPREAD across
sources — a single lookup won't be enough, which is intentional. It's what
will force the graph to loop.
"""

POD_EVENTS = """
2026-08-19T10:02:11Z  Normal   Scheduled   Pod payments-api-7f8b9 assigned to node-3
2026-08-19T10:02:14Z  Normal   Pulled      Container image "payments-api:v2.4.1" pulled
2026-08-19T10:02:15Z  Normal   Created     Container created
2026-08-19T10:02:16Z  Normal   Started     Container started
2026-08-19T10:02:19Z  Warning  BackOff     Back-off restarting failed container
2026-08-19T10:02:19Z  Warning  Unhealthy   Readiness probe failed: connection refused
2026-08-19T10:03:45Z  Warning  BackOff     Back-off restarting failed container (5 restarts)
"""

RECENT_DEPLOYS = """
2026-08-19T09:58:02Z  Deploy   payments-api  v2.4.0 -> v2.4.1  by: ci-bot
    Changed files: config/database.yaml, src/db/connection.py
2026-08-18T14:20:00Z  Deploy   auth-service  v1.9.2 -> v1.9.3  by: jsmith
2026-08-17T11:00:00Z  Deploy   payments-api  v2.3.9 -> v2.4.0  by: ci-bot
"""

APP_LOGS = """
2026-08-19T10:02:16.501Z INFO  Starting payments-api v2.4.1
2026-08-19T10:02:16.612Z INFO  Loading config from config/database.yaml
2026-08-19T10:02:16.890Z ERROR Failed to connect to database: host "db-primary-v2" not found
2026-08-19T10:02:16.891Z ERROR Connection string references "db-primary-v2" but DNS resolution failed
2026-08-19T10:02:17.002Z FATAL Unable to establish DB connection after 1 attempt, exiting
"""

# Simple lookup so the node can "query" by source name.
SOURCES = {
    "pod_events": POD_EVENTS,
    "recent_deploys": RECENT_DEPLOYS,
    "app_logs": APP_LOGS,
}

# Other replicas of the same deployment. In a real cluster, you wouldn't
# know this list (or its length) until you actually queried the API —
# it might be 2 replicas today, 5 next week after a scale-up. This is
# what makes it a good fit for Send: the fan-out COUNT is a runtime fact.
OTHER_PODS = {
    "payments-api-a1b2": "Readiness probe failing, connection refused. Same symptom as payments-api-7f8b9.",
    "payments-api-c3d4": "Readiness probe failing, connection refused. Same symptom as payments-api-7f8b9.",
    "payments-api-e5f6": "Running normally. Passed last 3 readiness checks.",
}
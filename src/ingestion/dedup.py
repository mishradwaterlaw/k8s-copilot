"""
src/ingestion/dedup.py — In-memory alert-storm deduplicator & circuit breaker.

CONCEPT: ALERT-STORM DEDUPLICATION & IDEMPOTENCY
════════════════════════════════════════════════
In Kubernetes environments, a crash-looping pod or node failure often causes an "alert storm":
  - Prometheus sends an alert notification every scrape cycle or evaluation interval.
  - Alertmanager groups and retries notifications aggressively if network lags.
  - 50 identical `KubePodCrashLooping` alerts can arrive within 3 minutes for the same pod.

Without deduplication:
  1. The server spawns 50 parallel LangGraph multi-agent investigations.
  2. Each investigation calls Gemini 5-10 times -> 300+ LLM API calls in 3 minutes!
  3. Burns API quotas, spikes costs, and generates 50 duplicate Slack/log notifications.

SOLUTION:
  `AlertDeduplicator` maintains a TTL-cached registry of active/recent alert fingerprints.
  - First alert -> Records fingerprint -> Returns `is_duplicate=False` -> Spawns investigation.
  - Subsequent duplicate alert within TTL window (e.g. 300s) -> Returns `is_duplicate=True` + existing `thread_id`.

INTERVIEW TALKING POINT:
  "I implemented an in-memory alert deduplicator keyed by alert fingerprint with a configurable TTL.
  This acts as an alert-storm circuit breaker, preventing duplicate LLM investigations and API budget
  exhaustion when Alertmanager retries aggressively during high-frequency pod crash events."
"""

import time
import threading
from typing import Dict, Optional, Tuple


class AlertDeduplicator:
    """
    Thread-safe in-memory cache for alert fingerprint deduplication.
    
    TTL (Time-to-Live): How many seconds an alert is considered 'active' before
    a new notification for the same fingerprint triggers a fresh investigation.
    Default: 300 seconds (5 minutes).
    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        # Mapping: fingerprint -> (timestamp_added, thread_id)
        self._cache: Dict[str, Tuple[float, str]] = {}
        self._lock = threading.Lock()

    def check_and_register(self, fingerprint: str, thread_id: str) -> Tuple[bool, str]:
        """
        Atomically checks if a fingerprint is currently active.
        
        If active and within TTL:
            Returns (True, existing_thread_id)  -> Duplicate! Do NOT run new graph.
        If new or expired:
            Registers the new fingerprint and thread_id.
            Returns (False, thread_id)          -> New alert! Safe to run investigation.
        """
        now = time.time()
        with self._lock:
            # Clean up expired entries periodically
            self._purge_expired(now)

            if fingerprint in self._cache:
                timestamp, existing_thread_id = self._cache[fingerprint]
                if now - timestamp < self.ttl_seconds:
                    # Still within active cooldown window -> Duplicate!
                    return True, existing_thread_id

            # Register new entry
            self._cache[fingerprint] = (now, thread_id)
            return False, thread_id

    def _purge_expired(self, now: float) -> None:
        """Removes entries older than TTL to keep memory footprint minimal."""
        expired_keys = [
            fp for fp, (ts, _) in self._cache.items() if now - ts >= self.ttl_seconds
        ]
        for key in expired_keys:
            del self._cache[key]

    def clear(self) -> None:
        """Clears all cached entries (useful in unit tests)."""
        with self._lock:
            self._cache.clear()


# Global singleton instance for the FastAPI application
deduplicator = AlertDeduplicator(ttl_seconds=300)

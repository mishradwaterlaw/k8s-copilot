"""
providers/__init__.py — Package init + provider factory function.

CONCEPT: THE FACTORY PATTERN
═════════════════════════════
A "factory" is a function that creates and returns an object
based on some configuration, hiding the construction details.

Instead of making every caller figure out:
  "Which provider class do I use? How do I import it? What args does it take?"

You call one function:
  provider = get_provider()

And the factory reads config and returns the correct provider.

WHY THIS FILE IS __init__.py:
  When Python imports a package (a directory with __init__.py),
  it runs this file. Putting the factory here means:
    from providers import get_provider
  Just works — no need to know the internal structure.

This file is the ONLY place in the codebase that knows about
both FixtureProvider and KubeAPIProvider. Everything else just
knows about KubeDataProvider (the interface) and get_provider() (the factory).
"""

import config
from providers.base import KubeDataProvider
from providers.fixture import FixtureProvider


def get_provider() -> KubeDataProvider:
    """
    Factory function: reads DATA_PROVIDER from config and returns
    the appropriate provider instance.

    Returns:
        KubeDataProvider — the correct concrete provider.
        The return type annotation says "KubeDataProvider" not
        "FixtureProvider" or "KubeAPIProvider" on purpose:
        callers should only depend on the interface, not the implementation.

    To add a new provider:
      1. Create providers/my_new_provider.py with a class that extends KubeDataProvider
      2. Add an elif branch here
      3. No other files change.
    """
    provider_type = config.DATA_PROVIDER.lower().strip()

    if provider_type == "fixture":
        return FixtureProvider()

    elif provider_type == "kube_api":
        # Only import KubeAPIProvider when actually needed.
        # This is a LAZY IMPORT — if the kubernetes SDK isn't installed
        # (which is fine for fixture-only setups), this code never runs
        # and you won't get an ImportError.
        from providers.kube_api import KubeAPIProvider
        return KubeAPIProvider(kubeconfig_path=config.KUBECONFIG_PATH)

    else:
        raise ValueError(
            f"Unknown DATA_PROVIDER: '{provider_type}'. "
            f"Valid options: 'fixture', 'kube_api'."
            # Clear error message so the user knows exactly what to fix.
        )

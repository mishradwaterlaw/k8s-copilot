"""
llm.py — Pluggable LLM Provider Factory for k8s-copilot.

CONCEPT: 12-FACTOR PLUGGABLE LLM PROVIDER PATTERN
═════════════════════════════════════════════════
Rather than hardcoding a single AI vendor (e.g. Google Gemini), this module
provides a provider-agnostic factory:
  - `LLM_PROVIDER=gemini`: Uses Google Gemini (`gemini-2.5-flash` or `gemini-1.5-flash`)
  - `LLM_PROVIDER=groq`: Uses Groq Ultra-Fast Inference (`llama-3.3-70b-versatile` or `llama-3.1-8b-instant`)

This allows zero-code switching via environment variables when rate limits or
cloud policies change.
"""

import os
from langchain_core.language_models.chat_models import BaseChatModel
import config


def get_llm() -> BaseChatModel:
    """
    Instantiate and return the configured ChatModel.
    Lazy initialization ensures API keys are checked at invocation time.
    """
    provider = config.LLM_PROVIDER.lower().strip()

    if provider == "groq":
        from langchain_groq import ChatGroq

        model_name = config.LLM_MODEL
        # Fallback to recommended Groq models if default was a Gemini string
        if not model_name or "gemini" in model_name.lower():
            model_name = "llama-3.3-70b-versatile"

        return ChatGroq(
            model=model_name,
            temperature=config.LLM_TEMPERATURE,
            groq_api_key=os.getenv("GROQ_API_KEY"),
        )

    # Default: Google Gemini
    from langchain_google_genai import ChatGoogleGenerativeAI

    model_name = config.LLM_MODEL or "gemini-2.5-flash"
    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=config.LLM_TEMPERATURE,
    )

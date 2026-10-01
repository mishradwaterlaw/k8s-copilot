
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

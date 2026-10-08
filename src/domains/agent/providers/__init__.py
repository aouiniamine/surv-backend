from core.config import Settings
from domains.agent.providers.base import ModelProvider
from domains.agent.providers.gemini import GeminiProvider
from domains.agent.providers.ollama import OllamaProvider


def create_model_provider(settings: Settings) -> ModelProvider:
    if settings.agent_provider == "gemini":
        key = settings.gemini_api_key
        if key is None:
            raise ValueError("GEMINI_API_KEY is required when AGENT_PROVIDER=gemini")
        return GeminiProvider(key.get_secret_value(), settings.gemini_model)
    return OllamaProvider(settings.ollama_base_url, settings.ollama_model)

import unittest
from pathlib import Path

from pydantic import ValidationError

from core.config import Settings
from domains.agent.providers import create_model_provider


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.required = {
            "database_url": "postgresql://user:password@localhost/surv",
            "smtp_host": "localhost",
            "smtp_port": 25,
            "smtp_username": "user",
            "smtp_password": "password",
            "smtp_from_email": "sender@example.com",
            "jwt_secret_key": "test-secret-key-with-at-least-32-characters",
        }

    def test_jwt_secret_must_be_at_least_32_characters(self) -> None:
        Settings(_env_file=None, **self.required)
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, **(self.required | {"jwt_secret_key": "short"}))

    def test_production_requires_apps_domain(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, **(self.required | {"project_environment": "production"}))
        settings = Settings(_env_file=None, **(self.required | {
            "project_environment": "production", "apps_domain": "*.example.com",
        }))
        self.assertEqual(settings.apps_domain, "example.com")

    def test_agent_provider_selection_and_gemini_key(self) -> None:
        ollama = Settings(_env_file=None, **self.required)
        self.assertEqual(create_model_provider(ollama).provider_id, "ollama")
        with self.assertRaisesRegex(ValidationError, "GEMINI_API_KEY"):
            Settings(_env_file=None, **(self.required | {"agent_provider": "gemini"}))
        gemini = Settings(_env_file=None, **(self.required | {
            "agent_provider": "gemini", "gemini_api_key": "test-key",
        }))
        provider = create_model_provider(gemini)
        self.assertEqual(provider.provider_id, "gemini")
        self.assertEqual(provider.model_id, gemini.gemini_model)
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, **(self.required | {"agent_provider": "unknown"}))

    def test_env_file_is_resolved_from_backend_directory(self) -> None:
        expected = Path(__file__).resolve().parents[1] / ".env"
        self.assertEqual(Settings.model_config["env_file"], expected)

import unittest

from pydantic import ValidationError

from core.config import Settings


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

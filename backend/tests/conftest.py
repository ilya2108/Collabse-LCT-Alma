"""Тестовое окружение: env-переменные должны быть заданы ДО импорта app.core.config."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

os.environ["APP_ENV"] = "test"
os.environ["PII_FERNET_KEYS"] = Fernet.generate_key().decode()
os.environ["PII_HMAC_KEY"] = "test-hmac-key-0123456789abcdef-0123456789abcdef"
os.environ["KEYCLOAK_ISSUER"] = "http://id.crm.local/realms/crm"
os.environ["KEYCLOAK_INTERNAL_URL"] = "http://keycloak.test:8080"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://crm:crm@localhost:5432/crm_test"
os.environ["KEYDB_URL"] = "redis://localhost:6399/0"  # заведомо недоступен: тест деградации

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
# тесты с БД (tests/db) пересоздают схему этой базы; параллельные прогоны — каждый со своей:
# ALMA_TEST_DB=crm_test_f<N> (имя crm_test* или *_test — см. tests/db/conftest.py)
os.environ["DATABASE_URL"] = os.environ.get(
    "ALMA_TEST_DATABASE_URL",
    f"postgresql+asyncpg://crm:crm@localhost:5432/{os.environ.get('ALMA_TEST_DB', 'crm_test')}",
)
os.environ["KEYDB_URL"] = "redis://localhost:6399/0"  # заведомо недоступен: тест деградации

-- Выполняется только при первом старте пустого тома PostgreSQL.
-- Отдельная БД для Keycloak в том же инстансе: один контейнер/StatefulSet,
-- две изолированные схемы данных (crm — приложение, keycloak — IAM).
CREATE DATABASE keycloak;

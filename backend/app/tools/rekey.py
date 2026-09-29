"""Ротация ключей шифрования ПДн (data-model.md §8.4, Р-20).

Использование: добавить новый Fernet-ключ ПЕРВЫМ в PII_FERNET_KEYS, перекатить Secret
и запустить `python -m app.tools.rekey` — батчами перешифровывает `*_enc` активным
ключом (SELECT ... FOR UPDATE SKIP LOCKED, по 500 строк). Старый ключ удаляется из
списка после полного прогона. При смене PII_HMAC_KEY хэши пересчитываются заново
(расшифровка → нормализация → новый HMAC).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.db import dispose_engine, get_session_factory
from app.core.pii import get_pii_crypto, normalize_email, normalize_full_name
from app.models.crm import Counterparty
from app.models.talent import Student

BATCH_SIZE = 500


async def _rekey_table(model: type, columns: tuple[str, ...]) -> int:
    """Перешифровывает bytea-колонки модели активным ключом; возвращает число строк."""
    crypto = get_pii_crypto()
    factory = get_session_factory()
    total = 0
    last_id = None
    while True:
        async with factory() as session:
            stmt = (
                select(model)
                .order_by(model.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            if last_id is not None:
                stmt = stmt.where(model.id > last_id)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return total
            for row in rows:
                for attr in columns:
                    value: str | None = getattr(row, attr)
                    if value is None:
                        continue
                    # присваивание того же значения не пометит атрибут «грязным» —
                    # переустанавливаем явно, чтобы TypeDecorator зашифровал заново
                    setattr(row, attr, None)
                    setattr(row, attr, value)
                # пересчёт blind-index (актуально при смене PII_HMAC_KEY)
                if getattr(row, "email", None):
                    row.email_hmac = crypto.hmac_hex(normalize_email(row.email))
                if getattr(row, "full_name", None):
                    row.full_name_hmac = crypto.hmac_hex(normalize_full_name(row.full_name))
                total += 1
            last_id = rows[-1].id
            await session.commit()


async def main() -> int:
    students = await _rekey_table(Student, ("full_name", "email", "phone"))
    counterparties = await _rekey_table(Counterparty, ("full_name", "email", "phone"))
    await dispose_engine()
    print(f"rekey: перешифровано студентов={students}, контрагентов={counterparties}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

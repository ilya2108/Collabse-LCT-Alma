"""ПДн-утилиты: нормализация, blind index, Fernet-ротация, псевдонимизация, маски."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.pii import (
    PiiConfigurationError,
    PiiCrypto,
    display_name_from_full_name,
    mask_email,
    mask_full_name,
    normalize_email,
    normalize_full_name,
)

HMAC_KEY = "unit-test-hmac-key-0123456789abcdef-0123456789abcdef"


@pytest.fixture()
def crypto() -> PiiCrypto:
    return PiiCrypto([Fernet.generate_key().decode()], HMAC_KEY)


class TestNormalization:
    def test_email_lower_and_trim(self) -> None:
        assert normalize_email("  Ivanov@Example.COM ") == "ivanov@example.com"

    def test_full_name_spaces_case_and_yo(self) -> None:
        assert normalize_full_name("  Ёлкина   Алёна  Петровна ") == "елкина алена петровна"

    def test_full_name_nfc(self) -> None:
        # й в декомпозированной форме (и + U+0306) должна схлопнуться в NFC
        decomposed = "Зайцев"
        assert normalize_full_name(decomposed) == "зайцев"


class TestBlindIndex:
    def test_hmac_is_hex64_and_deterministic(self, crypto: PiiCrypto) -> None:
        first = crypto.email_hmac("Ivanov@Example.com")
        second = crypto.email_hmac("  ivanov@example.com ")
        assert first == second  # нормализация до HMAC
        assert len(first) == 64
        int(first, 16)  # валидный hex

    def test_different_keys_produce_different_hmac(self, crypto: PiiCrypto) -> None:
        other = PiiCrypto([Fernet.generate_key().decode()], HMAC_KEY + "-другой")
        assert crypto.email_hmac("a@b.ru") != other.email_hmac("a@b.ru")

    def test_full_name_hmac_ignores_yo(self, crypto: PiiCrypto) -> None:
        assert crypto.full_name_hmac("Ёлкин Пётр") == crypto.full_name_hmac("Елкин Петр")


class TestFernet:
    def test_roundtrip(self, crypto: PiiCrypto) -> None:
        value = "Иванов Иван Иванович"
        token = crypto.encrypt(value)
        assert token != value.encode()
        assert crypto.decrypt(token) == value

    def test_rotation_old_key_still_readable(self) -> None:
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        old_crypto = PiiCrypto([old_key], HMAC_KEY)
        token = old_crypto.encrypt("secret@example.com")
        # новый ключ добавлен ПЕРВЫМ, старый остаётся для чтения (MultiFernet)
        rotated = PiiCrypto([new_key, old_key], HMAC_KEY)
        assert rotated.decrypt(token) == "secret@example.com"
        # rotate перешифровывает активным ключом: читается и без старого ключа
        fresh_token = rotated.rotate(token)
        only_new = PiiCrypto([new_key], HMAC_KEY)
        assert only_new.decrypt(fresh_token) == "secret@example.com"

    def test_missing_keys_raise_configuration_error(self) -> None:
        with pytest.raises(PiiConfigurationError):
            PiiCrypto([], HMAC_KEY)
        with pytest.raises(PiiConfigurationError):
            PiiCrypto([Fernet.generate_key().decode()], "короткий")


class TestPseudonymization:
    @pytest.mark.parametrize(
        ("full_name", "expected"),
        [
            ("Иванов Иван Иванович", "Иванов И.И."),
            ("Иванов Иван", "Иванов И."),
            ("Иванов", "Иванов"),
            ("  Петрова   Анна   Сергеевна  ", "Петрова А.С."),
        ],
    )
    def test_display_name(self, full_name: str, expected: str) -> None:
        assert display_name_from_full_name(full_name) == expected


class TestMasking:
    def test_mask_full_name(self) -> None:
        assert mask_full_name("Иванов Иван Иванович") == "И***в И.И."

    def test_mask_short_surname(self) -> None:
        assert mask_full_name("Ли Ян") == "Л*** Я."

    def test_mask_email(self) -> None:
        assert mask_email("ivanov@example.com") == "i***v@example.com"

    def test_mask_email_short_local(self) -> None:
        assert mask_email("ab@example.com") == "a***@example.com"

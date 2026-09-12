"""
Tests for crypto_utils.py

Covers:
- encrypt_bytes, decrypt_bytes
- encrypt_str, decrypt_str
- is_encrypted
- get_fernet
- Key generation and persistence
"""
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock


def test_encrypt_decrypt_bytes():
    """Test encryption and decryption of bytes."""
    from crypto_utils import encrypt_bytes, decrypt_bytes

    plaintext = b'Hello, World!'
    ciphertext = encrypt_bytes(plaintext)
    decrypted = decrypt_bytes(ciphertext)

    assert ciphertext != plaintext
    assert decrypted == plaintext


def test_encrypt_decrypt_str():
    """Test encryption and decryption of strings."""
    from crypto_utils import encrypt_str, decrypt_str

    plaintext = 'Hello, World!'
    ciphertext = encrypt_str(plaintext)
    decrypted = decrypt_str(ciphertext)

    assert ciphertext != plaintext
    assert decrypted == plaintext


def test_is_encrypted_true():
    """Test that is_encrypted returns True for encrypted data."""
    from crypto_utils import encrypt_bytes, is_encrypted

    plaintext = b'Hello, World!'
    ciphertext = encrypt_bytes(plaintext)

    assert is_encrypted(ciphertext) == True


def test_is_encrypted_false():
    """Test that is_encrypted returns False for plain data."""
    from crypto_utils import is_encrypted

    plaintext = b'\xff\xd8\xff\xe0\x00\x10JFIF'  # JPEG header
    assert is_encrypted(plaintext) == False


def test_is_encrypted_empty():
    """Test that is_encrypted returns False for empty data."""
    from crypto_utils import is_encrypted

    assert is_encrypted(b'') == False


def test_get_fernet_singleton():
    """Test that get_fernet returns the same instance."""
    from crypto_utils import get_fernet

    fernet1 = get_fernet()
    fernet2 = get_fernet()

    assert fernet1 is fernet2


def test_get_fernet_generates_key_when_missing(monkeypatch, tmp_path):
    """Test that get_fernet generates a key when not in environment."""
    from crypto_utils import get_fernet, _fernet
    import crypto_utils

    # Reset global state
    crypto_utils._fernet = None

    # Remove environment variable
    monkeypatch.delenv('FACE_DATA_ENCRYPTION_KEY', raising=False)

    # Mock BASE_DIR to temp directory
    with patch('crypto_utils.BASE_DIR', str(tmp_path)):
        fernet = get_fernet()

        assert fernet is not None
        assert crypto_utils._fernet is not None


def test_get_fernet_uses_existing_key(monkeypatch):
    """Test that get_fernet uses existing key from environment."""
    from crypto_utils import get_fernet, _fernet
    import crypto_utils
    from cryptography.fernet import Fernet

    # Reset global state
    crypto_utils._fernet = None

    # Set a known key
    key = Fernet.generate_key().decode('utf-8')
    monkeypatch.setenv('FACE_DATA_ENCRYPTION_KEY', key)

    fernet = get_fernet()

    assert fernet is not None
    assert crypto_utils._fernet is not None


def test_persist_generated_key(tmp_path, monkeypatch):
    """Test that generated key is persisted to .env file."""
    from crypto_utils import _persist_generated_key
    import crypto_utils

    # Mock BASE_DIR to temp directory
    with patch('crypto_utils.BASE_DIR', str(tmp_path)):
        test_key = 'test-key-12345'
        _persist_generated_key(test_key)

        env_file = tmp_path / '.env'
        assert env_file.exists()

        content = env_file.read_text()
        assert 'FACE_DATA_ENCRYPTION_KEY' in content
        assert test_key in content


def test_persist_generated_key_handles_write_error(tmp_path, monkeypatch):
    """Test that _persist_generated_key handles write errors gracefully."""
    from crypto_utils import _persist_generated_key

    # Point to a non-existent directory that can't be created
    bogus_path = tmp_path / 'nonexistent' / 'subdir'

    with patch('crypto_utils.BASE_DIR', str(bogus_path)):
        # Should not raise, just log error
        _persist_generated_key('test-key')


def test_env_file_path():
    """Test that _env_file_path returns correct path."""
    from crypto_utils import _env_file_path
    from config import BASE_DIR

    expected = os.path.join(BASE_DIR, '.env')
    assert _env_file_path() == expected


def test_decrypt_bytes_invalid_token():
    """Test that decrypt_bytes raises InvalidToken for bad data."""
    from crypto_utils import decrypt_bytes
    from cryptography.fernet import InvalidToken

    bad_data = b'not-valid-encrypted-data'

    with pytest.raises(InvalidToken):
        decrypt_bytes(bad_data)


def test_decrypt_str_invalid_token():
    """Test that decrypt_str raises InvalidToken for bad data."""
    from crypto_utils import decrypt_str
    from cryptography.fernet import InvalidToken

    bad_data = 'not-valid-encrypted-data'

    with pytest.raises(InvalidToken):
        decrypt_str(bad_data)


def test_encrypt_decrypt_large_data():
    """Test encryption/decryption of larger data."""
    from crypto_utils import encrypt_bytes, decrypt_bytes

    large_data = b'x' * 10000  # 10KB
    ciphertext = encrypt_bytes(large_data)
    decrypted = decrypt_bytes(ciphertext)

    assert decrypted == large_data


def test_encrypt_decrypt_unicode_str():
    """Test encryption/decryption of unicode strings."""
    from crypto_utils import encrypt_str, decrypt_str

    unicode_str = 'Hello 世界 🌍'
    ciphertext = encrypt_str(unicode_str)
    decrypted = decrypt_str(ciphertext)

    assert decrypted == unicode_str

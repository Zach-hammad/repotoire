"""Tests for encryption utility."""

import os
import pytest


def test_encrypt_decrypt_roundtrip():
    """Test that encryption and decryption work correctly."""
    # Set a test encryption key
    os.environ["ENCRYPTION_KEY"] = "test-key-for-unit-tests"
    
    # Clear any cached Fernet instance
    from repotoire.utils import encryption
    encryption._get_fernet.cache_clear()
    
    from repotoire.utils.encryption import encrypt_api_key, decrypt_api_key
    
    original = "sk-ant-api03-test-key-12345"
    encrypted = encrypt_api_key(original)
    
    # Encrypted should be different from original
    assert encrypted != original
    
    # Decrypted should match original
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == original


def test_mask_api_key():
    """Test API key masking."""
    from repotoire.utils.encryption import mask_api_key
    
    # Normal key
    key = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx"
    masked = mask_api_key(key)
    assert masked.startswith("sk-ant-a")
    assert masked.endswith("xxxx")
    assert "..." in masked
    
    # Short key (edge case)
    short = "sk-123"
    masked_short = mask_api_key(short)
    assert masked_short == "******"


def test_encrypt_different_keys_produce_different_output():
    """Test that different inputs produce different encrypted outputs."""
    os.environ["ENCRYPTION_KEY"] = "test-key-for-unit-tests"
    
    from repotoire.utils import encryption
    encryption._get_fernet.cache_clear()
    
    from repotoire.utils.encryption import encrypt_api_key
    
    key1 = "sk-ant-key-one"
    key2 = "sk-ant-key-two"
    
    encrypted1 = encrypt_api_key(key1)
    encrypted2 = encrypt_api_key(key2)
    
    assert encrypted1 != encrypted2

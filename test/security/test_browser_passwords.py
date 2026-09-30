import pytest


def test_scrypt_salts_unicode_and_wrong_password():
    from cli_agent_orchestrator.security.browser_passwords import hash_password, verify_password

    password = "contraseña suficientemente larga "
    first = hash_password(password)
    second = hash_password(password)
    assert first != second
    assert verify_password(password, first)
    assert not verify_password(password.rstrip(), first)
    assert not verify_password("incorrect password here", first)
    assert not verify_password(password, {"scheme": "broken"})


@pytest.mark.parametrize("password", ["short", "123456789", "x" * 129])
def test_password_length_is_rejected(password):
    from cli_agent_orchestrator.security.browser_passwords import hash_password

    with pytest.raises(ValueError):
        hash_password(password)

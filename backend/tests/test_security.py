from app.security import hash_password, hash_token, new_token, verify_password


def test_argon2_password_round_trip() -> None:
    password_hash = hash_password("a sufficiently long password")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "a sufficiently long password")
    assert not verify_password(password_hash, "wrong")


def test_tokens_are_random_and_stored_as_keyed_hashes() -> None:
    first, second = new_token(), new_token()
    assert first != second
    assert hash_token(first, "secret") != first
    assert hash_token(first, "secret") != hash_token(second, "secret")

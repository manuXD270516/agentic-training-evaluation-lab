import hashlib

import pytest

from evallab.canonical import canonical_digest, canonical_json


def test_rfc8785_number_serialization_vector() -> None:
    # RFC 8785 §3.2.2 / apéndice B: serialización ECMAScript de números.
    value = {"numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001]}
    assert canonical_json(value) == b'{"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27]}'


def test_rfc8785_sorts_keys_by_utf16_code_units() -> None:
    # RFC 8785 §3.2.3: orden por unidades UTF-16. Por code point, U+FB33 iría antes que U+1F600.
    value = {"\u20ac": "Euro", "\r": "CR", "\ufb33": "Hebrew", "1": "One", "\U0001f600": "Emoji"}
    assert canonical_json(value).decode() == (
        '{"\\r":"CR","1":"One","\u20ac":"Euro","\U0001f600":"Emoji","\ufb33":"Hebrew"}'
    )


def test_digest_is_independent_of_key_order_and_whitespace() -> None:
    assert canonical_digest({"b": [1, 2], "a": {"y": 1, "x": 2}}) == canonical_digest(
        {"a": {"x": 2, "y": 1}, "b": [1, 2]}
    )
    assert canonical_digest({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()


def test_array_order_is_preserved() -> None:
    assert canonical_digest({"seeds": [11, 23]}) != canonical_digest({"seeds": [23, 11]})


def test_non_finite_numbers_are_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": float("nan")})

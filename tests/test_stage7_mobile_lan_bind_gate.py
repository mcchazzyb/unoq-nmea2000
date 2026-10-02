"""Stage 7.9/7.10: `web/main.py` bind-host guard (Requirement 14.3, 30.4-30.5;
steering 13; Property 41).

Regression test for extending the guard from its original hardcoded
`10.`/`192.168.` prefix check to the full RFC 1918 range via `ipaddress`,
while proving the wildcard/non-private refusal still holds exactly as
before the change -- not just that the new private-address path works.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from web.main import is_permitted_bind_host


def test_loopback_forms_are_permitted():
    assert is_permitted_bind_host("127.0.0.1")
    assert is_permitted_bind_host("localhost")
    assert is_permitted_bind_host("::1")


def test_192_168_address_is_permitted():
    """Requirement 30.5: 192.168.0.0/16 -- the common home/marina-router
    range, and what the Arduino UNO Q's own networking commonly advertises
    on -- must be explicitly accepted, not just the 10.x range."""
    assert is_permitted_bind_host("192.168.1.42")
    assert is_permitted_bind_host("192.168.0.1")


def test_10_x_address_is_permitted():
    assert is_permitted_bind_host("10.0.0.5")


def test_172_16_31_address_is_permitted():
    """The 172.16.0.0/12 range was NOT covered by the original hardcoded
    prefix check (only 10./192.168. were) -- this is new coverage, not a
    pre-existing pass."""
    assert is_permitted_bind_host("172.16.0.1")
    assert is_permitted_bind_host("172.31.255.254")


def test_wildcard_addresses_are_always_refused():
    """Requirement 30.5: no configuration path may ever permit a wildcard
    bind -- this must hold identically before and after the RFC 1918
    extension."""
    assert not is_permitted_bind_host("0.0.0.0")
    assert not is_permitted_bind_host("::")


def test_public_address_is_refused():
    assert not is_permitted_bind_host("8.8.8.8")
    assert not is_permitted_bind_host("1.1.1.1")


def test_malformed_host_string_is_refused():
    assert not is_permitted_bind_host("not-an-address")
    assert not is_permitted_bind_host("")


@given(
    st.one_of(
        st.ip_addresses(v=4).map(str),
        st.ip_addresses(v=6).map(str),
        st.text(max_size=20),  # malformed/arbitrary strings too
    )
)
@settings(max_examples=100)
def test_bind_guard_accepts_iff_loopback_or_private_non_unspecified(host):
    """Feature: nmea2000-diagnostic-platform, Property 41: the Web_API bind
    guard never permits a wildcard or non-private address -- for any
    candidate host string, acceptance holds if and only if the host is
    loopback or a valid private, non-unspecified address."""
    import ipaddress as ip_mod

    result = is_permitted_bind_host(host)

    if host in ("127.0.0.1", "localhost", "::1"):
        expected = True
    else:
        try:
            addr = ip_mod.ip_address(host)
        except ValueError:
            expected = False
        else:
            expected = (not addr.is_unspecified) and (addr.is_private or addr.is_loopback)

    assert result == expected

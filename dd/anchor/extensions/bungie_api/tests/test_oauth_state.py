# Copyright © 2019-present gsfernandes81

# This file is part of "dd" henceforth referred to as "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

import datetime as dt

import pytest

from dd.anchor.extensions.bungie_api.oauth import OAuthStateManager


@pytest.fixture(autouse=True)
def _reset_oauth_state():
    """OAuthStateManager keeps class-level state; reset around each test."""
    OAuthStateManager._oauth_state_codes.clear()
    OAuthStateManager.clear_access_token()
    yield
    OAuthStateManager._oauth_state_codes.clear()
    OAuthStateManager.clear_access_token()


def test_state_code_lifecycle():
    code = OAuthStateManager.generate_oauth_state_code()
    assert OAuthStateManager.check_state_code_exists(code)
    # Consuming a live code does not raise and removes it.
    OAuthStateManager.consume_oauth_state_code(code)
    assert not OAuthStateManager.check_state_code_exists(code)


def test_unknown_state_code_does_not_exist():
    assert not OAuthStateManager.check_state_code_exists("never-generated")


def test_unknown_state_code_consume_raises_keyerror():
    # The OAuth callback relies on this: an unknown code raises KeyError (-> "Invalid
    # callback URL"), distinct from an expired code's ValueError (-> "expired").
    with pytest.raises(KeyError):
        OAuthStateManager.consume_oauth_state_code("never-generated")


def test_expired_state_code_consume_raises():
    past = dt.datetime.now() - dt.timedelta(minutes=1)
    OAuthStateManager._oauth_state_codes["expired"] = past
    with pytest.raises(ValueError):
        OAuthStateManager.consume_oauth_state_code("expired")


def test_expired_state_code_reports_absent():
    past = dt.datetime.now() - dt.timedelta(minutes=1)
    OAuthStateManager._oauth_state_codes["expired"] = past
    assert not OAuthStateManager.check_state_code_exists("expired")
    # check_state_code_exists evicts the expired entry.
    assert "expired" not in OAuthStateManager._oauth_state_codes


def test_generate_sweeps_expired_codes():
    """generate_oauth_state_code proactively drops abandoned/expired codes (N2)."""
    OAuthStateManager._oauth_state_codes["stale"] = dt.datetime.now() - dt.timedelta(
        minutes=1
    )
    OAuthStateManager.generate_oauth_state_code()
    assert "stale" not in OAuthStateManager._oauth_state_codes


def test_access_token_set_get_clear():
    OAuthStateManager.set_access_token("tok", 100)
    assert OAuthStateManager.get_access_token() == "tok"
    OAuthStateManager.clear_access_token()
    assert OAuthStateManager.get_access_token() is None


def test_expired_access_token_returns_none():
    # A non-positive expires_in puts the expiry in the past after the safety factor.
    OAuthStateManager.set_access_token("tok", -100)
    assert OAuthStateManager.get_access_token() is None

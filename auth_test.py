#############################################################################
# auth_test.py
#
# Tests identity selection without a real login (st is mocked).
#############################################################################
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import auth


def fake_st(secrets, user):
    st = MagicMock()
    st.secrets = secrets
    st.user = user
    return st


class TestCurrentUser(unittest.TestCase):

    def test_demo_mode_when_auth_not_configured(self):
        with patch.object(auth, "st", fake_st({}, SimpleNamespace(is_logged_in=False))):
            u = auth.current_user()
        self.assertTrue(u["is_guest"])
        self.assertFalse(u["auth_enabled"])
        self.assertEqual(u["user_id"], auth.GUEST_ID)

    def test_guest_when_configured_but_logged_out(self):
        with patch.object(auth, "st", fake_st({"auth": {}}, SimpleNamespace(is_logged_in=False))):
            u = auth.current_user()
        self.assertTrue(u["is_guest"])
        self.assertTrue(u["auth_enabled"])

    def test_signed_in_user_is_identified_by_lowercase_email(self):
        user = SimpleNamespace(is_logged_in=True, email="Ann@Fisk.edu", name="Ann")
        with patch.object(auth, "st", fake_st({"auth": {}}, user)):
            u = auth.current_user()
        self.assertFalse(u["is_guest"])
        self.assertEqual(u["user_id"], "ann@fisk.edu")
        self.assertEqual(u["name"], "Ann")

    def test_logged_in_without_email_is_treated_as_guest(self):
        user = SimpleNamespace(is_logged_in=True, email=None, name="Ann")
        with patch.object(auth, "st", fake_st({"auth": {}}, user)):
            self.assertTrue(auth.current_user()["is_guest"])

    def test_secrets_lookup_failure_means_demo_mode(self):
        class Boom:
            def __contains__(self, key):
                raise FileNotFoundError("no secrets")
        with patch.object(auth, "st", fake_st(Boom(), SimpleNamespace(is_logged_in=False))):
            self.assertFalse(auth.auth_configured())


if __name__ == "__main__":
    unittest.main()

#############################################################################
# auth.py
#
# Per-user identity for Hat Plug using Streamlit's built-in OpenID Connect
# login (st.login / st.user). See AUTH_SETUP.md for the one-time setup.
#
# If no [auth] secrets are configured (local dev, tests, first deploy) the app
# runs in demo mode as the shared guest account, exactly as before.
#############################################################################
import streamlit as st

GUEST_ID = "user1"   # shared demo account that already has data in BigQuery
GUEST_NAME = "Guest"


def auth_configured() -> bool:
    """True when an [auth] section exists in Streamlit secrets."""
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def current_user() -> dict:
    """
    Returns {"user_id", "name", "is_guest", "auth_enabled"}.
    A signed-in user is identified by their lower-cased email address.
    """
    enabled = auth_configured()
    if enabled:
        user = st.user
        email = (getattr(user, "email", None) or "").strip().lower()
        if getattr(user, "is_logged_in", False) and email:
            return {
                "user_id": email,
                "name": getattr(user, "name", None) or email.split("@")[0],
                "is_guest": False,
                "auth_enabled": True,
            }
    return {
        "user_id": GUEST_ID,
        "name": GUEST_NAME,
        "is_guest": True,
        "auth_enabled": enabled,
    }


def render_account_bar(user: dict) -> None:
    """Small sign-in / sign-out control shown at the top of every page."""
    if not user["auth_enabled"]:
        st.caption("Demo mode: you're using the shared guest account.")
        return
    if user["is_guest"]:
        left, right = st.columns([5, 2])
        with left:
            st.caption("Browsing as a guest. Sign in to use your cart, orders and Style Coach.")
        with right:
            if st.button("Sign in with Google", key="login_btn", use_container_width=True):
                st.login()
    else:
        left, right = st.columns([5, 2])
        with left:
            st.caption(f"Signed in as {user['name']} ({user['user_id']})")
        with right:
            if st.button("Sign out", key="logout_btn", use_container_width=True):
                st.logout()


def require_login(user: dict, what: str) -> bool:
    """
    Returns True if the page may proceed. When auth is enabled and the visitor
    is a guest, shows a sign-in prompt for `what` and returns False.
    """
    if user["auth_enabled"] and user["is_guest"]:
        st.info(f"Please sign in to see {what}.")
        if st.button("Sign in with Google", key=f"login_gate_{what}"):
            st.login()
        return False
    return True

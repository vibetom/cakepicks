"""A password gate for an app that has to be publicly reachable.

Streamlit's free tier allows only one private app, so a second one is public
whether or not that is wanted. The URL is obscure but discoverable, and the app
holds two things worth protecting: an odds API key with a monthly credit budget
and a GitHub token that can write to a repository.

The gate is deliberately narrow about what it claims. It stops a stranger who
finds the URL from using the app -- fetching odds, spending credits, writing to
the data branch. It is not authentication: there are no accounts, one shared
password, and a determined attacker with the URL is only inconvenienced. The
secrets themselves never reach the browser either way, since everything runs
server-side.

No password configured means no gate, so an app that is already private is
unaffected.
"""

from __future__ import annotations

import hmac

import streamlit as st

SECRET_NAME = "APP_PASSWORD"


def _matches(supplied: str, expected: str) -> bool:
    """Constant-time comparison, so the check leaks no timing information."""
    return hmac.compare_digest(str(supplied).encode(), str(expected).encode())


def require_password(expected: str | None, *, state_key: str = "unlocked",
                     title: str = "This app is password protected") -> bool:
    """Gate the app. Returns True when it should render.

    Renders a password form and returns False while locked; the caller stops.
    An unset or blank password disables the gate entirely.
    """
    if not expected:
        return True
    if st.session_state.get(state_key):
        return True

    st.title("🔒 " + title)
    st.caption(
        "Ask whoever runs this app for the password. It exists because the app "
        "spends a metered API budget, not because anything here is sensitive."
    )

    with st.form("password-gate"):
        supplied = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock", type="primary")

    if submitted:
        if _matches(supplied, expected):
            st.session_state[state_key] = True
            st.rerun()
        else:
            attempts = st.session_state.get(f"{state_key}_attempts", 0) + 1
            st.session_state[f"{state_key}_attempts"] = attempts
            st.error("That password isn't right.")

    return False

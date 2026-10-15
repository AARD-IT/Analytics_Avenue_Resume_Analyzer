"""
Analytics Avenue · Resume Analyzer — AI chatbot for HR.
Open link: Chat for everyone. Admin tab opens only for ADMIN_EMAILS + ADMIN_PASSWORD.
Run:  streamlit run app.py
"""
import hmac
import logging

import streamlit as st

from core.config import Config, get_secret

st.set_page_config(page_title="Resume Analyzer · Analytics Avenue", page_icon=str(Config.ASSETS / "logo_icon.png"),
                   layout="wide", initial_sidebar_state="collapsed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from core import store  # noqa: E402
from ui import theme  # noqa: E402
from ui.admin_page import admin_page  # noqa: E402
from ui.chat_page import chat_page  # noqa: E402

theme.inject()
store.db()  # connect + load the 99 bundled candidates on first run

ss = st.session_state
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "")
ADMIN_EMAILS = [e.strip().lower() for e in str(get_secret("ADMIN_EMAILS", "")).split(",") if e.strip()]
ss.setdefault("user_role", "hr")
ss.setdefault("user_email", "hr")


def admin_gate():
    # Open link: the app can't know who is visiting, so admins sign in here with their email + password.
    if ss.user_role != "admin":
        theme.brand_bar("Admin console", pill="Admin")
        _, mid, _ = st.columns([1, 1.2, 1])
        with mid:
            with st.form("admin_unlock"):
                em = st.text_input("Admin email")
                pw = st.text_input("Password", type="password")
                ok = st.form_submit_button("Open Admin", type="primary", width="stretch")
            if ok:
                em = (em or "").strip().lower()
                if not ADMIN_PASSWORD or not ADMIN_EMAILS:
                    st.error("Set ADMIN_EMAILS and ADMIN_PASSWORD in Streamlit secrets first.")
                elif em not in ADMIN_EMAILS:
                    st.error("This email is not an admin.")
                elif not hmac.compare_digest(pw or "", ADMIN_PASSWORD):
                    st.error("Wrong password.")
                else:
                    ss.user_role, ss.user_email = "admin", em
                    st.rerun()
        return

    def lock():
        ss.user_role, ss.user_email = "hr", "hr"
        st.rerun()

    admin_page(on_lock=lock)


pages = [
    st.Page(chat_page, title="Chat", default=True, url_path="chat"),
    st.Page(admin_gate, title="Admin", url_path="admin"),
]
st.navigation(pages, position="top").run()
"""
Analytics Avenue · Resume Analyzer — AI chatbot for HR.
No login: the chat opens directly. The Admin tab is protected by ADMIN_PASSWORD (Streamlit secrets).
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
ss.setdefault("user_email", "hr")
ss.setdefault("user_role", "hr")
ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "")


def admin_gate():
    if ss.user_role != "admin":
        theme.brand_bar("Admin console", pill="Admin")
        _, mid, _ = st.columns([1, 1.2, 1])
        with mid:
            with st.form("admin_unlock"):
                pw = st.text_input("Admin password", type="password")
                ok = st.form_submit_button("Unlock Admin", type="primary", width="stretch")
            if ok:
                if not ADMIN_PASSWORD:
                    st.error("Set ADMIN_PASSWORD in Streamlit secrets first.")
                elif hmac.compare_digest(pw or "", ADMIN_PASSWORD):
                    ss.user_role, ss.user_email = "admin", "admin"
                    st.rerun()
                else:
                    st.error("Wrong password.")
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

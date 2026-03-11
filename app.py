import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import logging
from streamlit.logger import get_logger

from components.auth import check_authentication, apply_auth_to_db_client
from components.transcript_review import render_transcript_review

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = get_logger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
N8N_WF5_WEBHOOK = os.getenv("N8N_WF5_WEBHOOK_URL")
REVIEWER_ACCESS_TABLE = os.getenv("REVIEWER_ACCESS_TABLE")

logger.info(f"[APP START] N8N_WF5_WEBHOOK loaded: {N8N_WF5_WEBHOOK}")

st.set_page_config(page_title="Transcript Review", layout="wide", initial_sidebar_state="collapsed")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase configuration. Add SUPABASE_URL and SUPABASE_KEY to your .env file.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

if not check_authentication(supabase, REVIEWER_ACCESS_TABLE):
    st.stop()

# MAIN APP
st.title("Transcript Review")
st.caption("Review and approve cleaned transcripts before AI analysis")
apply_auth_to_db_client(supabase)

query_params = st.query_params
transcript_token = query_params.get("transcript_id") if query_params else None

pending = supabase.table("transcripts") \
    .select("*, projects(project_name, company_name), zoom_meetings(meeting_topic, meeting_date)") \
    .eq("status", "pending_review") \
    .order("created_at", desc=True) \
    .execute()

if transcript_token:
    pending.data = [t for t in (pending.data or []) if t['id'] == transcript_token] if pending.data else []
    if pending.data:
        st.caption(f"🔗 Filtered to transcript: {pending.data[0]['zoom_meetings']['meeting_topic']}")

if not pending.data:
    visible = supabase.table("transcripts").select("id,status").order("created_at", desc=True).limit(10).execute()
    visible_rows = visible.data or []

    if visible_rows:
        statuses = sorted({row.get("status") for row in visible_rows if row.get("status") is not None})
        st.info("No rows with pending transcripts were found.")
        st.caption(f"Visible statuses in recent rows: {', '.join(statuses) if statuses else 'none'}")
    else:
        st.warning("No transcripts are visible to this logged-in user. This is usually an RLS policy issue.")
    st.stop()

transcript = pending.data[0] if pending.data else None

if transcript:
    render_transcript_review(supabase, transcript, N8N_WF5_WEBHOOK)

# Sidebar(Logout)
st.sidebar.title("Settings")
if st.sidebar.button("Logout"):
    for key in ["auth_user_id", "auth_user_email", "auth_access_token", "auth_refresh_token"]:
        st.session_state.pop(key, None)
    st.rerun()

st.sidebar.markdown("---")
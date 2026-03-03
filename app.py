import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
from datetime import datetime
import requests
import logging
from streamlit.logger import get_logger

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
N8N_WF5_WEBHOOK = os.getenv("N8N_WF5_WEBHOOK_URL")
REVIEWER_ACCESS_TABLE = os.getenv("REVIEWER_ACCESS_TABLE")

st.set_page_config(page_title="Transcript Review", layout="wide", initial_sidebar_state="collapsed")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase configuration. Add SUPABASE_URL and SUPABASE_KEY to your .env file.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = get_logger(__name__)


def apply_auth_to_db_client() -> bool:
    token = st.session_state.get("auth_access_token")
    if not token:
        return False

    try:
        supabase.postgrest.auth(token)
        return True
    except Exception as exc:
        logger.warning(f"Failed to apply auth token to db client: {exc}")
        return False

# AUTHENTICATION
def user_has_reviewer_access(user_id: str, email: str) -> tuple[bool, str]:
    try:
        normalized_email = (email or "").strip().lower()
        by_user_response = (
            supabase.table(REVIEWER_ACCESS_TABLE)
            .select("id")
            .eq("is_active", True)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        # logger.info(f"Access check for user_id={user_id}, email={email}: {by_user_response.data}")
        has_access = bool(by_user_response.data)

        if not has_access and normalized_email:
            by_email_response = (
                supabase.table(REVIEWER_ACCESS_TABLE)
                .select("id")
                .eq("is_active", True)
                .ilike("email", normalized_email)
                .limit(1)
                .execute()
            )
            has_access = bool(by_email_response.data)

        if not has_access:
            return False, "Your account is authenticated but not authorized for transcript review."
        return True, "Access granted"
    except Exception as exc:
        return False, f"Access check failed. Verify `{REVIEWER_ACCESS_TABLE}` exists and is readable: {exc}"


def check_authentication() -> bool:
    session_user_id = st.session_state.get("auth_user_id")
    session_email = st.session_state.get("auth_user_email", "")
    apply_auth_to_db_client()
    
    # logger.error(f"Checking authentication for user_id={st.session_state.get('debug_last_user_id')}, email={st.session_state.get('debug_last_email')}")

    if session_user_id:
        access_ok, access_message = user_has_reviewer_access(session_user_id, session_email)
        if access_ok:
            return True

        st.error(access_message)
        for key in ["auth_user_id", "auth_user_email", "auth_access_token"]:
            st.session_state.pop(key, None)
        return False

    st.title("Transcript Review")
    st.caption("Sign in with an email/password from this project's Supabase Auth users.")

    sign_in_tab, create_account_tab = st.tabs(["Sign in", "Create account"])

    with sign_in_tab:
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

        if submitted:
            normalized_email = email.strip().lower()
            if not normalized_email or not password:
                st.error("Email and password are required.")
                return False

            try:
                auth_response = supabase.auth.sign_in_with_password({
                    "email": normalized_email,
                    "password": password
                })

                if not auth_response.user or not auth_response.session:
                    st.error("Login failed. Check your email and password.")
                    return False

                access_ok, access_message = user_has_reviewer_access(
                    auth_response.user.id,
                    (auth_response.user.email or normalized_email).lower()
                )
                if not access_ok:
                    st.error(access_message)
                    return False

                st.session_state["auth_user_id"] = auth_response.user.id
                st.session_state["auth_user_email"] = (auth_response.user.email or normalized_email).lower()
                st.session_state["auth_access_token"] = auth_response.session.access_token
                st.session_state["auth_refresh_token"] = auth_response.session.refresh_token
                apply_auth_to_db_client()
                st.rerun()
            except Exception as exc:
                error_text = str(exc)
                if "Invalid login credentials" in error_text:
                    st.error(
                        "Invalid login credentials. This usually means the user is not in Supabase Auth users for this project, "
                        "or the password is wrong. Add the user under Supabase Authentication > Users (email/password)."
                    )
                else:
                    st.error(f"Login failed: {error_text}")

    with create_account_tab:
        with st.form("signup_form", clear_on_submit=False):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm Password", type="password", key="signup_password_confirm")
            signup_submitted = st.form_submit_button("Create account", use_container_width=True)

        if signup_submitted:
            normalized_new_email = new_email.strip().lower()
            if not normalized_new_email or not new_password:
                st.error("Email and password are required.")
                return False
            if new_password != confirm_password:
                st.error("Passwords do not match.")
                return False
            if len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
                return False

            try:
                supabase.auth.sign_up({"email": normalized_new_email, "password": new_password})
                st.success(
                    "Account created in Supabase Auth. If your project requires email confirmation, verify your email first, "
                    "then return to the Sign in tab."
                )
            except Exception as exc:
                st.error(f"Account creation failed: {exc}")

    return False


def trigger_workflow_5(payload: dict) -> tuple[bool, str]:
    if not N8N_WF5_WEBHOOK:
        return False, "Workflow 5 webhook URL is missing (N8N_WF5_WEBHOOK_URL)."

    try:
        response = requests.post(N8N_WF5_WEBHOOK, json=payload, timeout=15)
        if response.status_code >= 400:
            response_body = response.text[:300] if response.text else "no response body"
            return False, f"Workflow 5 returned {response.status_code}: {response_body}"
        return True, "Workflow 5 triggered successfully."
    except Exception as exc:
        return False, f"Workflow 5 trigger failed: {exc}"


def update_transcript_and_verify(transcript_id: str, update_data: dict) -> tuple[bool, str]:
    try:
        supabase.table("transcripts") \
            .update(update_data) \
            .eq("id", transcript_id) \
            .execute()
        # logger.info(f"Updated transcript {transcript_id} with data: {update_data}")

        verify = supabase.table("transcripts") \
            .select("id, cleaned_text, review_notes, reviewed_at, status") \
            .eq("id", transcript_id) \
            .limit(1) \
            .execute()
        # logger.info(f"Verification query result for transcript {transcript_id}: {verify.data}")

        if not verify.data:
            return False, "Update may have failed or row is not visible (likely RLS policy issue)."

        return True, "Update saved to database."
    except Exception as exc:
        return False, f"Database update failed: {exc}"

if not check_authentication():
    st.stop()

# MAIN APP
st.title("Transcript Review")
st.caption("Review and approve cleaned transcripts before AI analysis")
apply_auth_to_db_client()

pending = supabase.table("transcripts") \
    .select("*, projects(project_name, company_name), zoom_meetings(meeting_topic, meeting_date)") \
    .eq("status", "pending_review") \
    .order("created_at", desc=True) \
    .execute()

# logger.info(f"Fetched pending transcripts count: {len(pending.data or [])}")

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

st.info(f"**{len(pending.data)} transcript(s)** pending review")

transcript_options = {
    f"{t['zoom_meetings']['meeting_topic']} - {t['projects']['company_name']}": t['id']
    for t in pending.data
}

selected_label = st.selectbox(
    "Select a transcript to review:",
    options=list(transcript_options.keys()),
    key="transcript_selector"
)

selected_transcript_id = transcript_options[selected_label]
transcript = next((t for t in pending.data if t['id'] == selected_transcript_id), None)

if transcript:
    # TRANSCRIPT REVIEW INTERFACE
    st.divider()
    col_header, col_meta = st.columns([3, 1])
    
    with col_header:
        st.subheader(f"{transcript['zoom_meetings']['meeting_topic']}")
        st.write(f"**Project:** {transcript['projects']['project_name']} - {transcript['projects']['company_name']}")
    
    with col_meta:
        meeting_date = datetime.fromisoformat(transcript['zoom_meetings']['meeting_date'].replace('Z', '+00:00'))
        st.metric("Date", meeting_date.strftime("%b %d, %Y"))
    st.write("📁 **Files:**")
    col1, col2 = st.columns(2)
    with col1:
        if transcript.get('original_transcript_url'):
            st.markdown(f"[Original Transcript]({transcript['original_transcript_url']})")
    with col2:
        if transcript.get('cleaned_transcript_url'):
            st.markdown(f"[Cleaned Transcript]({transcript['cleaned_transcript_url']})")
    
    st.markdown("---")
    
    st.subheader("Compare & Edit")
    
    col_original, col_cleaned = st.columns(2)
    
    with col_original:
        st.markdown("### Original Transcript")
        st.caption("Raw transcript from Zoom")
        
        original_text = st.text_area(
            "Original",
            value=transcript.get('original_text', 'Loading...'),
            height=400,
            disabled=True,
            key=f"original_{transcript['id']}",
            label_visibility="collapsed"
        )
    
    with col_cleaned:
        st.markdown("### Cleaned Transcript")
        st.caption("Edited by AI - you can make changes below")
        
        edited_text = st.text_area(
            "Cleaned (editable)",
            value=transcript.get('cleaned_text', 'Loading...'),
            height=400,
            key=f"cleaned_{transcript['id']}",
            label_visibility="collapsed"
        )
    
    if transcript.get('original_text') and transcript.get('cleaned_text'):
        orig_len = len(transcript['original_text'])
        clean_len = len(transcript['cleaned_text'])
        change_pct = abs(orig_len - clean_len) / orig_len * 100
        
        stat_col1, stat_col2, stat_col3 = st.columns(3)
        with stat_col1:
            st.metric("Original Length", f"{orig_len:,} chars")
        with stat_col2:
            st.metric("Cleaned Length", f"{clean_len:,} chars")
        with stat_col3:
            st.metric("Change", f"{change_pct:.1f}%")
    
    st.markdown("---")
    review_notes = st.text_area(
        "Review Notes (optional)",
        placeholder="Add any notes about changes you made or issues found...",
        key=f"notes_{transcript['id']}",
        height=100
    )
    
    st.markdown("---")
    
    action_col1, action_col2 = st.columns(2)
    
    with action_col1:
        if st.button(
            "Approve",
            key=f"approve_{transcript['id']}",
            type="primary",
            use_container_width=True
        ):
            user_made_edits = edited_text != transcript.get('cleaned_text')
            final_text = edited_text if edited_text is not None else transcript.get('cleaned_text')
            reviewer_email = st.session_state.get("auth_user_email", "")
            timestamp = datetime.now().isoformat()

            update_data = {
                "status": "approved",
                "cleaned_text": final_text,
                "final_text": final_text,
                "reviewed_at": timestamp,
                "approved_at": timestamp,
                "review_notes": review_notes if review_notes else None,
                "user_edited": user_made_edits
            }

            final_file_name = "Unknown(final).txt"
            meeting_id = transcript.get('zoom_meeting_id')
            if meeting_id:
                try:
                    file_name_response = (
                        supabase.table("zoom_files")
                        .select("renamed_file_name, drive_folder_url")
                        .eq("meeting_id", meeting_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    file_rows = file_name_response.data or []
                    source_name = (file_rows[0].get("renamed_file_name") if file_rows else "") or ""
                    folder_url = file_rows[0].get("drive_folder_url") if file_rows else None
                    if source_name:
                        base_name = os.path.splitext(os.path.basename(source_name.strip()))[0]
                        final_file_name = f"{base_name}(final).txt"
                        logger.info(f"[APPROVE] Built final_file_name: {final_file_name}")
                except Exception as exc:
                    logger.error(f"[APPROVE] Exception in file lookup: {exc}", exc_info=True)
            try:
                save_ok, save_message = update_transcript_and_verify(transcript['id'], update_data)
                if not save_ok:
                    st.error(save_message)
                    st.stop()

                payload = {
                    "transcript_id": transcript['id'],
                    "project_id": transcript.get('project_id'),
                    "meeting_id": transcript.get('zoom_meeting_id'),
                    "final_transcript_text": final_text,
                    "final_text": final_text,
                    "cleaned_text": final_text,
                    "status": "approved",
                    "user_edited": user_made_edits,
                    "approved_at": timestamp,
                    "file_name": final_file_name,
                    "folder_url": folder_url,
                }

                workflow_ok, workflow_message = trigger_workflow_5(payload)
                if workflow_ok:
                    st.success("Transcript approved and Workflow 5 triggered.")
                else:
                    st.warning(f"Transcript approved, but {workflow_message}")

                st.balloons()
                st.rerun()
            except Exception as exc:
                st.error(f"Approve failed: {exc}")
    
    with action_col2:
        if st.button(
            "Save Draft",
            key=f"draft_{transcript['id']}",
            use_container_width=True
        ):
            draft_data = {
                "cleaned_text": edited_text,
                "review_notes": review_notes,
                "reviewed_at": datetime.now().isoformat()
            }

            draft_ok, draft_message = update_transcript_and_verify(transcript['id'], draft_data)
            if draft_ok:
                st.success("Changes saved as draft.")
            else:
                st.error(draft_message)
    
    if review_notes:
        with st.expander("Review History"):
            st.write(f"**Last Review:** {transcript.get('reviewed_at', 'N/A')[:19]}")
            st.write(f"**Notes:** {review_notes}")

# Sidebar(Logout)
st.sidebar.title("Settings")
if st.sidebar.button("Logout"):
    for key in ["auth_user_id", "auth_user_email", "auth_access_token", "auth_refresh_token"]:
        st.session_state.pop(key, None)
    st.rerun()

st.sidebar.markdown("---")
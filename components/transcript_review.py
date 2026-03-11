import streamlit as st
from supabase import Client
from datetime import datetime
import os
import logging
from components.database import update_transcript_and_verify

logger = logging.getLogger(__name__)


def render_action_message(container, level: str, message: str):
    if not message:
        return

    if level == "success":
        container.success(message)
    elif level == "warning":
        container.warning(message)
    else:
        container.error(message)


def render_transcript_review(supabase: Client, transcript: dict, workflow_webhook: str):
    st.divider()

    meeting_date = datetime.fromisoformat(transcript['zoom_meetings']['meeting_date'].replace('Z', '+00:00'))
    
    detail_col1, detail_col2, detail_col3, detail_col4 = st.columns(4)
    with detail_col1:
        st.markdown(f"**Date:** {meeting_date.strftime('%b %d, %Y')}")
    with detail_col2:
        st.markdown(f"**Topic:** {transcript['zoom_meetings']['meeting_topic']}")
    with detail_col3:
        st.markdown(f"**Project:** {transcript['projects']['project_name']}")
    with detail_col4:
        st.markdown(f"**Company:** {transcript['projects']['company_name']}")
    
    st.markdown("")
    
    file_col1, file_col2 = st.columns(2)
    with file_col1:
        if transcript.get('original_transcript_url'):
            st.markdown(f"🔗 [Original Transcript]({transcript['original_transcript_url']})")
    with file_col2:
        if transcript.get('cleaned_transcript_url'):
            st.markdown(f"🔗 [Cleaned Transcript]({transcript['cleaned_transcript_url']})")
    
    st.divider()

    col_original, col_cleaned = st.columns(2, gap="medium")
    
    with col_original:
        st.markdown("### Original Transcript")
        original_text = st.text_area(
            "Original",
            value=transcript.get('original_text', 'Loading...'),
            height=550,
            disabled=True,
            key=f"original_{transcript['id']}",
            label_visibility="collapsed"
        )
    
    with col_cleaned:
        st.markdown("### Cleaned Transcript (Editable)")
        edited_text = st.text_area(
            "Cleaned (editable)",
            value=transcript.get('cleaned_text', 'Loading...'),
            height=550,
            key=f"cleaned_{transcript['id']}",
            label_visibility="collapsed"
        )
    
    st.divider()
    
    action_col1, action_col2, message_col = st.columns([1, 1, 2])
    
    approve_clicked = False
    draft_clicked = False
    
    with action_col1:
        approve_clicked = st.button(
            "Approve",
            key=f"approve_{transcript['id']}",
            type="primary",
            use_container_width=True
        )
    
    with action_col2:
        draft_clicked = st.button(
            "Save Draft",
            key=f"draft_{transcript['id']}",
            use_container_width=True
        )

    feedback_key = f"review_feedback_{transcript['id']}"
    feedback = st.session_state.pop(feedback_key, None)

    with message_col:
        message_container = st.empty()
        if feedback:
            render_action_message(
                message_container,
                feedback.get("level", "error"),
                feedback.get("message", "")
            )

    st.markdown("---")
    review_notes = st.text_area(
        "Review Notes (optional)",
        placeholder="Add any notes about changes you made or issues found...",
        key=f"notes_{transcript['id']}",
        height=80
    )

    if approve_clicked:
        handle_approve(supabase, transcript, edited_text, review_notes, workflow_webhook, feedback_key)
    
    if draft_clicked:
        handle_save_draft(supabase, transcript, edited_text, review_notes, message_container)


def handle_approve(
    supabase: Client,
    transcript: dict,
    edited_text: str,
    review_notes: str,
    webhook_url: str,
    feedback_key: str,
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
    folder_url = None
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
        save_ok, save_message = update_transcript_and_verify(supabase, transcript['id'], update_data)
        if not save_ok:
            st.session_state[feedback_key] = {"level": "error", "message": save_message}
            st.rerun()

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

        workflow_ok, workflow_message = trigger_workflow_5(webhook_url, payload)
        if workflow_ok:
            st.session_state[feedback_key] = {
                "level": "success",
                "message": "Transcript approved and Workflow 5 triggered."
            }
        else:
            st.session_state[feedback_key] = {
                "level": "warning",
                "message": f"Transcript approved, but Workflow 5 trigger failed"
            }

        st.rerun()
    except Exception as exc:
        st.session_state[feedback_key] = {"level": "error", "message": f"Approve failed: {exc}"}
        st.rerun()


def handle_save_draft(
    supabase: Client,
    transcript: dict,
    edited_text: str,
    review_notes: str,
    message_container,
):
    draft_data = {
        "cleaned_text": edited_text,
        "review_notes": review_notes,
        "reviewed_at": datetime.now().isoformat()
    }

    draft_ok, draft_message = update_transcript_and_verify(supabase, transcript['id'], draft_data)
    if draft_ok:
        render_action_message(message_container, "success", "Changes saved as draft.")
    else:
        render_action_message(message_container, "error", draft_message)


def trigger_workflow_5(webhook_url: str, payload: dict) -> tuple[bool, str]:
    import requests
    
    if not webhook_url:
        return False, "Workflow 5 webhook URL is missing (N8N_WF5_WEBHOOK_URL)."

    try:
        response = requests.post(webhook_url, json=payload, timeout=15)
        if response.status_code >= 400:
            response_body = response.text[:300] if response.text else "no response body"
            return False, f"Workflow 5 returned {response.status_code}: {response_body}"
        return True, "Workflow 5 triggered successfully."
    except Exception as exc:
        return False, f"Workflow 5 trigger failed: {exc}"

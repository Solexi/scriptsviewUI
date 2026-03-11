import streamlit as st
from supabase import Client
import logging

logger = logging.getLogger(__name__)


def user_has_reviewer_access(supabase: Client, user_id: str, email: str, reviewer_table: str) -> tuple[bool, str]:
    try:
        normalized_email = (email or "").strip().lower()
        by_user_response = (
            supabase.table(reviewer_table)
            .select("id")
            .eq("is_active", True)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        has_access = bool(by_user_response.data)

        if not has_access and normalized_email:
            by_email_response = (
                supabase.table(reviewer_table)
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
        return False, f"Access check failed. Verify `{reviewer_table}` exists and is readable: {exc}"


def apply_auth_to_db_client(supabase: Client) -> bool:
    """Apply auth token to database client"""
    token = st.session_state.get("auth_access_token")
    refresh_token = st.session_state.get("auth_refresh_token")
    if not token:
        return False

    try:
        if refresh_token:
            supabase.auth.set_session(token, refresh_token)
        supabase.postgrest.auth(token)
        return True
    except Exception as exc:
        logger.warning(f"Failed to apply auth token to db client: {exc}")
        return False


def check_authentication(supabase: Client, reviewer_table: str) -> bool:
    session_user_id = st.session_state.get("auth_user_id")
    session_email = st.session_state.get("auth_user_email", "")
    apply_auth_to_db_client(supabase)

    if session_user_id:
        access_ok, access_message = user_has_reviewer_access(supabase, session_user_id, session_email, reviewer_table)
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
                    supabase, 
                    auth_response.user.id,
                    (auth_response.user.email or normalized_email).lower(),
                    reviewer_table
                )
                if not access_ok:
                    st.error(access_message)
                    return False

                st.session_state["auth_user_id"] = auth_response.user.id
                st.session_state["auth_user_email"] = (auth_response.user.email or normalized_email).lower()
                st.session_state["auth_access_token"] = auth_response.session.access_token
                st.session_state["auth_refresh_token"] = auth_response.session.refresh_token
                apply_auth_to_db_client(supabase)
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

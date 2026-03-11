from supabase import Client
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def update_transcript_and_verify(supabase: Client, transcript_id: str, update_data: dict) -> tuple[bool, str]:
    try:
        supabase.table("transcripts") \
            .update(update_data) \
            .eq("id", transcript_id) \
            .execute()

        verify = supabase.table("transcripts") \
            .select("id, cleaned_text, review_notes, reviewed_at, status") \
            .eq("id", transcript_id) \
            .limit(1) \
            .execute()

        if not verify.data:
            return False, "Update may have failed or row is not visible (likely RLS policy issue)."

        return True, "Update saved to database."
    except Exception as exc:
        return False, f"Database update failed: {exc}"




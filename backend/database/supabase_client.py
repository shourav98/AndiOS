"""
Supabase client — uses service role key so backend bypasses RLS.
Never expose this key to the frontend.
"""
from supabase import create_client, Client
from config import settings

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
        )
    return _supabase

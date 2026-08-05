"""
Supabase client — uses service role key so backend bypasses RLS.
Never expose this key to the frontend.
"""
from supabase import create_client, Client
from config import settings

def get_supabase() -> Client:
    """
    Creates and returns a new Supabase client instance per request.
    This prevents auth session leakage across concurrent requests.
    """
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
    )

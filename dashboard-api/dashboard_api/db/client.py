import os
from supabase import create_client, create_async_client, Client, AsyncClient

def get_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)

async def get_supabase_async() -> AsyncClient:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return await create_async_client(url, key)

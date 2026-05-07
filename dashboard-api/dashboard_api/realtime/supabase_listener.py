import asyncio
import logging
from ..db.client import get_supabase_async
from ..services.ws_hub import hub

logger = logging.getLogger(__name__)

async def start_supabase_listener():
    sb = await get_supabase_async()
    
    def on_change(payload):
        logger.info(f"Received realtime event: {payload}")
        # Payload format depends on the supabase-py version, but usually has 'new' data
        data = payload.get('new', {})
        if not data:
            return

        # In a real app, we'd find which agent is assigned to this call
        # For now, let's look up the call to get the agent_id
        call_id = data.get('call_id')
        if call_id:
            try:
                call_res = sb.table("calls").select("agent_id").eq("id", call_id).single().execute()
                agent_id = call_res.data.get('agent_id')
                if agent_id:
                    asyncio.create_task(hub.broadcast_to_agent(str(agent_id), {
                        "type": "new_turn",
                        "data": data
                    }))
            except Exception as e:
                logger.error(f"Error fetching agent_id for call {call_id}: {e}")

    # Subscribe to all changes on the 'turns' table using the async client API.
    try:
        channel = sb.channel("db-changes")
        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="turns",
            callback=on_change
        )
        await channel.subscribe()
        
        logger.info("Subscribed to Supabase Realtime changes on 'turns' table")
    except Exception as e:
        logger.error(f"Failed to subscribe to Supabase Realtime: {e}")

import asyncio
import logging
from typing import Dict, Any, Set
from datetime import datetime

logger = logging.getLogger(__name__)

class EventBroadcaster:
    """
    Service for broadcasting real-time events to connected SSE clients.
    Supports multiple event channels (e.g., post_status, stats_update).
    """
    
    def __init__(self):
        self.clients: Dict[str, Set[asyncio.Queue]] = {
            'post_status': set(),
            'stats': set(),
            'all': set()
        }
        
    def subscribe(self, channel: str = 'all') -> asyncio.Queue:
        """
        Subscribe to a specific event channel.
        Returns a queue that will receive events.
        """
        queue = asyncio.Queue(maxsize=100)
        
        if channel not in self.clients:
            self.clients[channel] = set()
            
        self.clients[channel].add(queue)
        logger.info(f"[EventBroadcaster] New client subscribed to '{channel}' (total: {len(self.clients[channel])})")
        
        return queue
    
    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        """Unsubscribe from a channel."""
        if channel in self.clients and queue in self.clients[channel]:
            self.clients[channel].remove(queue)
            logger.info(f"[EventBroadcaster] Client unsubscribed from '{channel}' (remaining: {len(self.clients[channel])})")
    
    async def broadcast(self, channel: str, event_type: str, data: Dict[str, Any]):
        """
        Broadcast an event to all subscribers of a channel.
        
        Args:
            channel: Target channel (e.g., 'post_status', 'stats')
            event_type: Type of event (e.g., 'post_updated', 'stats_refreshed')
            data: Event payload
        """
        event = {
            'type': event_type,
            'timestamp': datetime.utcnow().isoformat(),
            'data': data
        }
        
        # Get subscribers for this channel + 'all' channel
        subscribers = list(self.clients.get(channel, set())) + list(self.clients.get('all', set()))
        
        if not subscribers:
            return
            
        logger.debug(f"[EventBroadcaster] Broadcasting {event_type} to {len(subscribers)} clients on '{channel}'")
        
        # Send to all subscribers
        dead_queues = []
        for queue in subscribers:
            try:
                # Non-blocking put with timeout
                await asyncio.wait_for(queue.put(event), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning(f"[EventBroadcaster] Client queue full, skipping...")
                dead_queues.append(queue)
            except Exception as e:
                logger.error(f"[EventBroadcaster] Error broadcasting to client: {e}")
                dead_queues.append(queue)
        
        # Clean up dead queues
        for queue in dead_queues:
            for ch in self.clients:
                if queue in self.clients[ch]:
                    self.clients[ch].remove(queue)
    
    async def broadcast_post_status(self, post_id: int, status: str, meta: Dict[str, Any] = None):
        """Convenience method for broadcasting post status updates."""
        await self.broadcast('post_status', 'post_updated', {
            'post_id': post_id,
            'status': status,
            'meta': meta or {}
        })
    
    async def broadcast_stats_update(self):
        """Convenience method for notifying about stats refresh."""
        await self.broadcast('stats', 'stats_refreshed', {
            'message': 'Statistics have been updated'
        })

# Global singleton instance
event_broadcaster = EventBroadcaster()

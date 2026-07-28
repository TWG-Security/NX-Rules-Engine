"""JSON-RPC over WebSocket ingestion (Phase 2 — flagged EXPERIMENTAL).

NX 6.1 exposes ``GET /jsonrpc`` as a WebSocket that supports subscriptions to REST
resources, and the docs steer integrators to it over the legacy transaction bus. It is
a genuine push feed — but the exact *subscribe* message payload is not in the uploaded
API spec (it points to an external "API Information page"), so this must be verified
against a live server before we depend on it.

This module is a deliberate stub: it documents the intended shape and refuses to run
until the payload is confirmed, so nothing silently half-works.
"""

from __future__ import annotations

from ..bus import EventBus

JSONRPC_WS_PATH = "/jsonrpc"


class JsonRpcSubscriber:
    """Placeholder for the Phase 2 JSON-RPC/WebSocket subscriber.

    Intended flow (to confirm live):
      1. open WebSocket to ``wss://<host>:<port>/jsonrpc``
      2. authenticate with a session bearer token
      3. send a JSON-RPC ``*.subscribe`` request for the event log / resource
      4. translate each notification into an :class:`Event` and publish to the bus
    """

    def __init__(self, base_url: str, token: str, bus: EventBus):
        self.base_url = base_url
        self.token = token
        self.bus = bus

    async def run(self) -> None:  # pragma: no cover - not implemented in Phase 1
        raise NotImplementedError(
            "JSON-RPC/WebSocket subscription is a Phase 2 feature. The subscribe payload "
            "must be verified against a live NX server's API Information page first. "
            "Use the webhook ingestion path for now."
        )

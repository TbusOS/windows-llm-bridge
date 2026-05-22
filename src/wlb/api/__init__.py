"""HTTP API + Web UI layer.

The ``wlb-api`` (also reachable via ``wlb web``) entry point starts a
FastAPI server that exposes wlb capabilities over JSON HTTP + WebSocket
streaming. The bundled single-page UI consumes those endpoints.

Default bind is ``127.0.0.1`` (localhost only). Anyone who can reach the
port has the same powers as the user who started ``wlb-api`` — do not
expose to other networks without adding an authentication layer.
"""

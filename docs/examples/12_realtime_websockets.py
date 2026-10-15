"""
12_realtime_websockets.py - Real-Time WebSockets & C-Level Pub/Sub
===================================================================

WebSockets enable full-duplex, low-latency bidirectional communication between
clients and the server. In standard Python stacks, RFC 6455 frame parsing and
masking are CPU-heavy and slow.

Buckthorn pushes WebSocket processing down into C11:
1. **RFC 6455 64-Bit SIMD XOR Unmasking:** Client-to-server payload frames are
   unmasked in compiled C (`bt_realtime.c`).
2. **Instant C Handshake:** The `Sec-WebSocket-Accept` SHA1 handshake calculation
   is executed in C OpenSSL during the HTTP upgrade step.
3. **In-Memory C Pub/Sub Broker:** `app.ws_publish(topic, message)` distributes
   messages across thousands of connected sockets with zero Python loop overhead.

This example demonstrates:
- Declaring WebSocket routes with `@app.websocket("/chat/{room}")`.
- Receiving text frames (`await ws.receive_text()`).
- Sending text frames (`await ws.send_text(...)`).
- Subscribing to rooms (`ws.subscribe(room)`) and broadcasting with `app.ws_publish()`.

To run this live server in development mode:
    buckthorn dev docs/examples/12_realtime_websockets.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/12_realtime_websockets.py
"""

from buckthorn import Buckthorn, WebSocket

app = Buckthorn(
    title="Real-Time WebSocket & PubSub Service",
    version="1.0.0",
)


@app.websocket("/chat/{room}")
async def chat_endpoint(ws: WebSocket, room: str):
    """
    Accepts WebSocket connection for a given room.
    Messages sent by the client are echoed back and broadcasted
    to all active subscribers of that room via C Pub/Sub.
    """
    # 1. Accept the WebSocket connection (completes RFC 6455 handshake)
    await ws.accept()

    # 2. Subscribe this socket to the room's C Pub/Sub channel
    ws.subscribe(room)

    # 3. Read an incoming message from the client
    incoming_text = await ws.receive_text()

    # 4. Echo direct reply to the sender
    await ws.send_text(f"[{room}] Direct Echo: {incoming_text}")

    # 5. Broadcast to all other subscribers in the room via C Pub/Sub
    app.ws_publish(room, f"[{room}] Broadcast: {incoming_text}")

    # 6. Close the connection
    await ws.close()


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 12_realtime_websockets.py in-memory verification...")
    client = RawTestClient(app)

    # Connect to the WebSocket room using the in-memory test harness
    with client.websocket_connect("/chat/engineering") as session:
        # Verify RFC 6455 handshake status (101 Switching Protocols)
        assert session.handshake_status == 101
        assert len(session.accept_key) > 0
        print(f"  ✓ Handshake Succeeded -> 101 Switching Protocols (Accept: {session.accept_key[:12]}...)")

        # Send client-masked frame
        session.send_text("Deploying v2.0")

        # Receive direct echo
        echo_reply = session.receive_text()
        assert echo_reply == "[engineering] Direct Echo: Deploying v2.0"
        print(f"  ✓ Direct Echo Received -> '{echo_reply}'")

        # Receive C Pub/Sub broadcast
        broadcast_reply = session.receive_text()
        assert broadcast_reply == "[engineering] Broadcast: Deploying v2.0"
        print(f"  ✓ Pub/Sub Broadcast Received -> '{broadcast_reply}'")

    print("\n✅ All 12_realtime_websockets.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/12_realtime_websockets.py:app")

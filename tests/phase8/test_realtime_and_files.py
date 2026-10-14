# tests/phase8/test_realtime_and_files.py
import time
import pytest
from buckthorn import (
    Buckthorn,
    WebSocket,
    UploadFile,
    File,
    EventSourceResponse,
    FileResponse,
)
from buckthorn.testing import RawTestClient


def test_c_websocket_handshake_xor_unmasking_and_pubsub():
    app = Buckthorn()

    @app.websocket("/ws/{room}")
    async def ws_chat(ws: WebSocket, room: str):
        await ws.accept()
        ws.subscribe(room)
        msg = await ws.receive_text()
        await ws.send_text(f"[{room}] echo:{msg}")
        # Broadcast via C Pub/Sub engine to all subscribers of `room`
        app.ws_publish(room, f"broadcast:{msg}")
        await ws.close()

    client = RawTestClient(app)
    with client.websocket_connect("/ws/general") as session:
        # Verify RFC 6455 101 Switching Protocols & Sec-WebSocket-Accept header
        assert session.handshake_status == 101
        assert len(session.accept_key) > 16

        # Send client-masked RFC 6455 frame -> unmasked in C via 64-bit XOR
        session.send_text("hello_buckthorn")
        reply1 = session.receive_text()
        reply2 = session.receive_text()
        assert reply1 == "[general] echo:hello_buckthorn"
        assert reply2 == "broadcast:hello_buckthorn"


def test_sse_event_source_streaming_response():
    app = Buckthorn()

    @app.get("/ai/stream")
    async def stream_tokens(prompt: str = "hi"):
        async def token_generator():
            for chunk in ["token_1", "token_2", f"end:{prompt}"]:
                yield f"data: {chunk}\n\n"

        return EventSourceResponse(token_generator())

    client = RawTestClient(app)
    res = client.get("/ai/stream?prompt=cython")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["Content-Type"]
    assert res.body == b"data: token_1\n\ndata: token_2\n\ndata: end:cython\n\n"


def test_zero_copy_c_multipart_upload_file_and_file_response(tmp_path):
    app = Buckthorn()

    @app.post("/upload")
    async def upload_document(
        doc: UploadFile = File(),
        tag: str = "general",
    ):
        raw_content = await doc.read()
        return {
            "filename": doc.filename,
            "content_type": doc.content_type,
            "size": len(raw_content),
            "preview": raw_content.decode("utf-8"),
            "tag": tag,
        }

    sample_file = tmp_path / "report.txt"
    sample_file.write_bytes(b"Buckthorn zero-copy static file payload")

    @app.get("/download/report")
    def download_report():
        return FileResponse(str(sample_file), media_type="text/plain", filename="report.txt")

    client = RawTestClient(app)

    # 1. Test zero-copy C multipart/form-data parser
    res_up = client.post(
        "/upload?tag=finance",
        files={"doc": ("invoice_2026.csv", b"id,amount\n1,500.00\n", "text/csv")},
    )
    assert res_up.status_code == 200
    assert res_up.json() == {
        "filename": "invoice_2026.csv",
        "content_type": "text/csv",
        "size": 19,
        "preview": "id,amount\n1,500.00\n",
        "tag": "finance",
    }

    # 2. Test static FileResponse delivery
    res_dl = client.get("/download/report")
    assert res_dl.status_code == 200
    assert res_dl.headers["Content-Type"] == "text/plain"
    assert 'filename="report.txt"' in res_dl.headers["Content-Disposition"]
    assert res_dl.body == b"Buckthorn zero-copy static file payload"


def test_phase8_c_websocket_frame_codec_speed_gate():
    app = Buckthorn()

    @app.websocket("/ws/bench")
    async def ws_bench(ws: WebSocket):
        await ws.accept()
        data = await ws.receive_text()
        await ws.send_text(data)

    client = RawTestClient(app)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        with client.websocket_connect("/ws/bench") as ws:
            ws.send_text("ping_payload_1234567890")
            out = ws.receive_text()
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert out == "ping_payload_1234567890"
    assert rps > 60_000, f"Expected >60,000 WebSocket full session cycles/sec, got {rps:,.0f}/sec"

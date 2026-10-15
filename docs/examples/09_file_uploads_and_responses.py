"""
09_file_uploads_and_responses.py - Multipart Uploads & Custom File/HTML Responses
==================================================================================

Standard Python file uploads usually suffer from severe memory copying:
incoming multipart streams are read chunk-by-chunk in pure Python, creating
dozens of temporary string and byte buffers.

Buckthorn solves this with:
1. **Zero-Copy C Multipart Parser (`bt_multipart_extract_file`):**
   The C11 multipart scanner locates boundaries and file boundaries in-place
   within the connection arena without intermediate Python allocations.
2. **Freelist `UploadFile`:**
   Wraps the raw C slice (`_data_ptr`, `_data_len`) via `@cython.freelist(1024)`.
   Data is copied to Python `bytes` lazily only when `await file.read()` is called!
3. **Optimized Custom Responses:**
   - `FileResponse`: Serves static files from disk with correct MIME types and
     `Content-Disposition: attachment` headers.
   - `HTMLResponse`: Renders pre-encoded HTML pages with `Content-Type: text/html`.

This example demonstrates:
- Receiving uploaded files using `UploadFile = File()`.
- Accessing `doc.filename`, `doc.content_type`, and `await doc.read()`.
- Serving static files with `FileResponse`.
- Serving HTML with `HTMLResponse`.

To run this live server in development mode:
    buckthorn dev docs/examples/09_file_uploads_and_responses.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/09_file_uploads_and_responses.py
"""

import os
import tempfile
from buckthorn import Buckthorn, UploadFile, File, FileResponse, HTMLResponse

app = Buckthorn(
    title="File I/O and Custom Responses API",
    version="1.0.0",
)


# ==============================================================================
# 1. Zero-Copy Multipart File Upload Endpoint
# ==============================================================================
@app.post("/upload")
async def upload_file(
    doc: UploadFile = File(),
    category: str = "general",
):
    """
    Parses multipart/form-data directly in compiled C.
    `doc` holds zero-copy slice pointers until `read()` is called.
    """
    file_bytes = await doc.read()
    return {
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size_bytes": len(file_bytes),
        "category": category,
        "preview": file_bytes[:100].decode("utf-8", errors="replace"),
    }


# ==============================================================================
# 2. Static File Response Endpoint
# ==============================================================================
# Create a temporary file to serve as a demo download
_demo_file = os.path.join(tempfile.gettempdir(), "buckthorn_readme.txt")
with open(_demo_file, "w", encoding="utf-8") as f:
    f.write("Buckthorn: Compiled C11/Cython Web Framework for Python 3.14+")


@app.get("/download")
def download_file():
    """Serves a static file from disk as a downloadable attachment."""
    return FileResponse(
        path=_demo_file,
        media_type="text/plain",
        filename="buckthorn_readme.txt",
    )


# ==============================================================================
# 3. HTML Response Endpoint
# ==============================================================================
@app.get("/welcome")
def welcome_page():
    """Serves an HTML landing page."""
    html_content = """
    <!DOCTYPE html>
    <html>
      <head><title>Buckthorn</title></head>
      <body style="font-family: sans-serif; padding: 2rem;">
        <h1>Welcome to Buckthorn!</h1>
        <p>Serving web apps at bare-metal C speed with Python ergonomics.</p>
      </body>
    </html>
    """
    return HTMLResponse(html_content)


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 09_file_uploads_and_responses.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test multipart file upload
    test_content = b"sensor_id,timestamp,value\n101,1700000000,24.5\n"
    res_up = client.post(
        "/upload?category=telemetry",
        files={"doc": ("sensors.csv", test_content, "text/csv")},
    )
    assert res_up.status_code == 200
    up_data = res_up.json()
    assert up_data["filename"] == "sensors.csv"
    assert up_data["content_type"] == "text/csv"
    assert up_data["size_bytes"] == len(test_content)
    assert up_data["category"] == "telemetry"
    print(f"  ✓ Multipart Upload -> 200 {up_data}")

    # 2. Test FileResponse download
    res_dl = client.get("/download")
    assert res_dl.status_code == 200
    assert "text/plain" in res_dl.headers["Content-Type"]
    assert 'filename="buckthorn_readme.txt"' in res_dl.headers["Content-Disposition"]
    assert b"Buckthorn: Compiled C11/Cython" in res_dl.body
    print(f"  ✓ File Download -> 200 ({len(res_dl.body)} bytes delivered)")

    # 3. Test HTMLResponse
    res_html = client.get("/welcome")
    assert res_html.status_code == 200
    assert "text/html" in res_html.headers["Content-Type"]
    assert b"<h1>Welcome to Buckthorn!</h1>" in res_html.body
    print(f"  ✓ HTML Page -> 200 (Rendered HTML successfully)")

    print("\n✅ All 09_file_uploads_and_responses.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/09_file_uploads_and_responses.py:app")

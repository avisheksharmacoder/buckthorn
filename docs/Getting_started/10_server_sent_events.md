# 10. Server-Sent Events (SSE) & LLM Streaming

## Overview
Server-Sent Events (SSE) provide a standardized, lightweight protocol for continuous one-way data streaming from the server to the browser over standard HTTP. With the explosive rise of Large Language Models (LLMs) and real-time AI agents, streaming token-by-token completions has become a core requirement for modern APIs. Buckthorn provides built-in native support for SSE via `EventSourceResponse` without needing third-party libraries like `sse-starlette`.

---

## 1. Layman's Explanation
When you use ChatGPT or Claude, the AI doesn't wait 10 seconds to generate an entire 5-paragraph answer and send it all at once; you see words appear one by one on your screen in real time.

That magic is powered by Server-Sent Events (SSE). Instead of closing the connection after the first answer, the server keeps the pipe open and drips small droplets of text (tokens) down the wire whenever they are ready. 

In most Python setups, setting up SSE requires installing extra adapter libraries and dealing with complex async stream converters. In Buckthorn, you just write a simple Python generator (`yield`), wrap it in `EventSourceResponse`, and Buckthorn streams the tokens straight out of the native C socket buffer.

---

## 2. Technical Explanation
1. **The `EventSourceResponse` Class:**
   - Defined in [buckthorn/responses.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/responses.py#L27-L43).
   - Marked with `__bt_custom_response__ = True`.
   - The compiled Cython bridge recognizes this attribute in [_execute_route_inner](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx#L1330-L1341) and delegates rendering directly to `_render_c_response()`.
2. **Synchronous & Asynchronous Generator Support:**
   - `_consume_async_or_sync_gen()` accepts both standard Python generators (`yield`) and asynchronous generators (`async def ... yield`).
   - For async generators, coroutine iteration is driven directly via `gen.__anext__()` without incurring the overhead of the Python `asyncio` event loop task machinery.
3. **HTTP/1.1 Event-Stream Framing:**
   - Automatically injects the required SSE headers:
     - `Content-Type: text/event-stream; charset=utf-8`
     - `Cache-Control: no-cache`
   - Buffers chunks directly into the C output arena for optimal socket transmission.

---

## 3. Code Example

```python
# sse_llm_streaming_demo.py
import asyncio
from buckthorn import Buckthorn, EventSourceResponse

app = Buckthorn(title="SSE LLM Streaming Demo")

# Simulated LLM Token Generator (Async Generator)
async def generate_llm_stream(prompt: str):
    tokens = [
        "Buckthorn ", "is ", "an ", "ultra-fast ", "compiled ",
        "C/Cython ", "web ", "framework ", "designed ", "for ",
        "extreme ", "throughput ", "and ", "microsecond ", "latency."
    ]
    for token in tokens:
        # Simulate neural network inference time per token
        await asyncio.sleep(0.05)
        # Format as standard SSE data payload
        yield f"data: {token}\n\n"
    
    # Send standard SSE termination marker
    yield "data: [DONE]\n\n"

@app.get("/api/chat/stream")
async def chat_completions(prompt: str = "Explain Buckthorn"):
    return EventSourceResponse(generate_llm_stream(prompt))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `from buckthorn import EventSourceResponse`:
   - Imports the native SSE response handler.
2. `async def generate_llm_stream(prompt: str)`:
   - An asynchronous generator function. It yields each token formatted as standard SSE lines: `data: <content>\n\n`.
   - Any LLM client library (OpenAI Python SDK, Anthropic SDK, Ollama, HuggingFace, LangChain) that yields async chunks can be piped directly into this generator.
3. `yield "data: [DONE]\n\n"`:
   - The industry standard end-of-stream signal recognized by frontend libraries (EventSource, fetch-event-source, and Vercel AI SDK).
4. `return EventSourceResponse(generate_llm_stream(prompt))`:
   - Wraps the generator.
   - Buckthorn immediately formats HTTP 200 headers with `Content-Type: text/event-stream; charset=utf-8` and `Cache-Control: no-cache`.
   - The socket transmits chunks as they are generated without closing the connection.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | FastAPI / Starlette | Flask / Django | Buckthorn |
|---|---|---|---|
| **External Dependencies** | Requires installing `sse-starlette` package | Requires custom WSGI/ASGI hacks or Celery | **Zero external dependencies** (built into core) |
| **Generator Handling** | Wraps generators in heavy `StreamingResponse` tasks | Difficult and blocking in WSGI | **Direct C/Cython generator stepping** |
| **Response Headers** | Manually set headers and handle media types | Manual header setting | **Automatic standard SSE headers pre-baked** |
| **Memory Footprint** | Multiple Python generator wrappers per stream | High per-connection memory | **Minimal C arena footprint** per streaming client |
| **Concurrency** | Limited by Python event loop task overhead | Poor | **Massive concurrent streams** backed by `libuv` |

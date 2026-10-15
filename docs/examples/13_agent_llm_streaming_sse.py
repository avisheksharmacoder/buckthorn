"""
13_agent_llm_streaming_sse.py - Server-Sent Events (SSE) for LLM Token Streaming
=================================================================================

When building AI agents or LLM applications, waiting for the entire language model
completion to finish before returning a response creates terrible user experiences.
Instead, modern AI apps stream tokens in real-time as they are generated using
**Server-Sent Events (SSE)**.

Buckthorn provides `EventSourceResponse`:
1. **Low-Latency Wire Rendering:** Binds to async or sync generator iterables,
   streaming chunks directly with `Content-Type: text/event-stream`.
2. **Minimal Buffering:** Automatically appends HTTP headers (`Cache-Control: no-cache`,
   `Connection: keep-alive`) to ensure client browsers/clients render tokens without delay.
3. **Structured Event Data:** Easily streams JSON deltas (such as OpenAI/Anthropic format).

This example demonstrates:
- Creating an SSE streaming endpoint with `EventSourceResponse`.
- Simulating or connecting an LLM token generator.
- Streaming reasoning thoughts and final tokens.

To run this live server in development mode:
    buckthorn dev docs/examples/13_agent_llm_streaming_sse.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/13_agent_llm_streaming_sse.py
"""

import json
from buckthorn import Buckthorn, EventSourceResponse, Query

app = Buckthorn(
    title="AI Agent Token Streaming Service",
    version="1.0.0",
)


# ==============================================================================
# Simulated LLM Agent Token Generator
# ==============================================================================
async def agent_llm_stream(prompt: str):
    """
    Simulates an AI Agent generating step-by-step thoughts and tokens.
    In a real app, this would be an async iteration from an LLM client:
        async for chunk in openai_client.chat.completions.create(..., stream=True):
            yield f"data: {chunk.choices[0].delta.content}\\n\\n"
    """
    # 1. Stream simulated thinking step
    thought_event = json.dumps({"type": "thought", "content": f"Analyzing question: '{prompt}'"})
    yield f"data: {thought_event}\n\n"

    # 2. Stream simulated tokens
    simulated_tokens = ["Buckthorn ", "is ", "built ", "with ", "C11 ", "and ", "Cython ", "for ", "extreme ", "speed."]
    for token in simulated_tokens:
        token_event = json.dumps({"type": "token", "content": token})
        yield f"data: {token_event}\n\n"

    # 3. Stream terminal finish event
    done_event = json.dumps({"type": "done", "prompt": prompt})
    yield f"data: {done_event}\n\n"


# ==============================================================================
# SSE Route Handler
# ==============================================================================
@app.get("/agent/stream")
async def stream_agent_response(
    prompt: str = Query(default="Tell me about Buckthorn", min_length=2),
):
    """
    Streams AI Agent tokens and reasoning events to the client using Server-Sent Events.
    """
    return EventSourceResponse(agent_llm_stream(prompt))


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 13_agent_llm_streaming_sse.py in-memory verification...")
    client = RawTestClient(app)

    # Use standard URL encoding (%20) for query strings with spaces
    res = client.get("/agent/stream?prompt=What%20is%20Buckthorn")
    assert res.status_code == 200

    # Verify SSE Content-Type
    assert "text/event-stream" in res.headers["Content-Type"]
    assert "no-cache" in res.headers["Cache-Control"]

    # Parse and verify the streaming event frames
    wire_content = res.body.decode("utf-8")
    lines = [line for line in wire_content.split("\n") if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines]
    print(f"  ✓ Stream Headers -> Content-Type: {res.headers['Content-Type']}")
    print(f"  ✓ Received {len(events)} streamed SSE events:")
    for ev in events:
        if ev["type"] == "thought":
            print(f"    [Thought] {ev['content']}")
        elif ev["type"] == "token":
            print(f"    [Token]   '{ev['content']}'")
        elif ev["type"] == "done":
            print(f"    [Done]    Prompt finished: '{ev['prompt']}'")

    assert events[0]["type"] == "thought"
    assert events[-1]["type"] == "done"

    print("\n✅ All 13_agent_llm_streaming_sse.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/13_agent_llm_streaming_sse.py:app")

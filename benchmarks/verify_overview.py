import time
import statistics
from buckthorn import Buckthorn, Path, Query, Schema, Field, Depends
from buckthorn.security import HTTPBearer, create_jwt_token
from buckthorn.testing import RawTestClient

# 1. Plaintext / Static Route
app1 = Buckthorn()
@app1.get("/fast")
def fast():
    return b"Hello, World!"
c1 = RawTestClient(app1)
req1 = b"GET /fast HTTP/1.1\r\nHost: localhost\r\n\r\n"

# 2. Typed Parameters (Path/Query)
app2 = Buckthorn()
@app2.get("/items/{item_id}")
def params(item_id: int = Path(...), q: str = Query("all")):
    return {"id": item_id, "q": q}
c2 = RawTestClient(app2)
req2 = b"GET /items/42?q=search HTTP/1.1\r\nHost: localhost\r\n\r\n"

# 3. Validated JSON Schema (POST)
class Item(Schema):
    name: str = Field(min_length=2)
    price: float = Field(gt=0)
app3 = Buckthorn()
@app3.post("/items")
def schema_route(item: Item):
    return item
c3 = RawTestClient(app3)
body3 = b'{"name":"Widget","price":9.99}'
req3 = (
    b"POST /items HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: " + str(len(body3)).encode() + b"\r\n\r\n" + body3
)

# 4. Dependency Injection (Depends)
app4 = Buckthorn()
def get_db():
    conn = {"db": 1}
    try:
        yield conn
    finally:
        conn["db"] = 0

@app4.get("/di")
def di_route(db: dict = Depends(get_db)):
    return {"db": db["db"]}
c4 = RawTestClient(app4)
req4 = b"GET /di HTTP/1.1\r\nHost: localhost\r\n\r\n"

# 5. JWT Verified Route
app5 = Buckthorn()
auth = HTTPBearer(jwt_secret="secret")
token = create_jwt_token({"sub": "user1", "exp": 2000000000}, secret="secret")
@app5.get("/auth")
def auth_route(claims: dict = Depends(auth)):
    return {"user": claims["sub"]}
c5 = RawTestClient(app5)
req5 = (
    b"GET /auth HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Authorization: Bearer " + token.encode() + b"\r\n\r\n"
)

# 6. Pure C Fast 401 Rejection
app6 = Buckthorn()
auth6 = HTTPBearer(jwt_secret="secret")
@app6.get("/auth")
def auth_reject(claims: dict = Depends(auth6)):
    return {"user": "ok"}
c6 = RawTestClient(app6)
req6 = b"GET /auth HTTP/1.1\r\nHost: localhost\r\n\r\n"  # Missing token -> 401

benchmarks = [
    ("Plaintext / Static Route", c1, req1),
    ("Typed Parameters (Path/Query)", c2, req2),
    ("Validated JSON Schema (POST)", c3, req3),
    ("Dependency Injection (Depends)", c4, req4),
    ("JWT Verified Route (OpenSSL HMAC)", c5, req5),
    ("Pure C Fast 401/403 Rejection", c6, req6),
]

def main():
    print("=" * 90)
    print("🌲 Buckthorn Single-Core Request Workflow Performance Verification")
    print("⚡ Warmup: 1,000 iterations | Measurement: 50,000 iterations per workflow")
    print("=" * 90)
    print(f"| {'Workflow':<36} | {'Throughput':>15} | {'Avg Latency':>13} | {'p50':>9} | {'p99':>9} |")
    print("|" + "-" * 38 + "|" + "-" * 17 + "|" + "-" * 15 + "|" + "-" * 11 + "|" + "-" * 11 + "|")

    for name, client, req in benchmarks:
        # Warmup
        for _ in range(1000):
            client.send_raw(req)

        iters = 50_000
        times_ns = []
        t0 = time.perf_counter()
        for _ in range(iters):
            st = time.perf_counter_ns()
            res = client.send_raw(req)
            times_ns.append(time.perf_counter_ns() - st)
        total_time = time.perf_counter() - t0

        rps = iters / total_time
        times_ns.sort()
        avg_us = sum(times_ns) / len(times_ns) / 1000.0
        p50_us = times_ns[int(iters * 0.50)] / 1000.0
        p99_us = times_ns[int(iters * 0.99)] / 1000.0
        print(f"| {name:<36} | {rps:>11,.0f} RPS | {avg_us:>10.2f} µs | {p50_us:>7.2f} µs | {p99_us:>7.2f} µs |")

    print("=" * 90)

if __name__ == "__main__":
    main()

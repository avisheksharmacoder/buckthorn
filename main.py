from __future__ import annotations

from buckthorn import (
    BackgroundTasks,
    Buckthorn,
    Depends,
    Field,
    Header,
    Schema,
    Security,
    WebSocket,
)
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from buckthorn.security import HTTPBearer

app = Buckthorn(
    title="Order Execution Engine",
    version="1.0.0",
    access_log=True,
    max_body_size=1024 * 1024,
)

# Tier-1 Middlewares execute entirely in C before acquiring the Python GIL
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "api.example.com", "127.0.0.1"])
app.add_middleware(CORSMiddleware, allow_origins=["*"])
app.add_middleware(RateLimitMiddleware, max_requests=5000, window_seconds=60)
app.add_middleware(SecurityHeadersMiddleware)

JWT_SECRET = "prod-hs256-signing-secret-32-bytes-minimum!"
bearer = HTTPBearer(jwt_secret=JWT_SECRET)


class ShippingAddress(Schema):
    city: str = Field(..., min_length=2, max_length=64)
    pincode: int = Field(..., ge=100000, le=999999)


class OrderItem(Schema):
    sku: str = Field(..., min_length=3, max_length=32)
    qty: int = Field(..., ge=1, le=100)
    price: float = Field(..., gt=0.0, le=1_000_000.0)


class CreateOrderRequest(Schema):
    account_id: int = Field(..., ge=1)
    address: ShippingAddress
    items: list[OrderItem]


def get_audit_tag(x_request_id: str = Header("req-default", max_length=64)) -> str:
    return f"audit:{x_request_id}"


@app.get("/")
def root():
    return {"status": "ok", "framework": "Buckthorn", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/orders", response_model=CreateOrderRequest, status_code=201)
async def create_order(
    order: CreateOrderRequest,
    bg: BackgroundTasks,
    audit_tag: str = Depends(get_audit_tag),
    claims: dict = Security(bearer, scopes=["orders:write"]),
):
    bg.add_task(
        app.logger.info,
        "order_accepted",
        account_id=order.account_id,
        actor=claims.get("sub", "unknown"),
        audit=audit_tag,
    )
    return order


@app.websocket("/ws/quotes/{symbol}")
async def quote_stream(ws: WebSocket, symbol: str):
    await ws.accept()
    ws.subscribe(f"ticker:{symbol}")
    msg = await ws.receive_text()
    await ws.send_text(f"[{symbol}] ack:{msg}")
    await ws.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

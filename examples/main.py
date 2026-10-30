from __future__ import annotations

import asyncio
from typing import Optional
from buckthorn import (
    Buckthorn,
    Depends,
    Field,
    HTTPException,
    Path,
    Query,
    Request,
    Schema,
    Security,
    status,
)
from buckthorn.security import HTTPBearer, create_jwt_token

app = Buckthorn(title="Buckthorn Example Service", version="1.0.0")
auth_guard = HTTPBearer(jwt_secret="super-secure-production-secret")


class ItemCreate(Schema):
    name: str = Field(min_length=2, max_length=50)
    price: float = Field(gt=0.0, le=10_000.0)
    quantity: int = Field(default=1, ge=1, le=500)
    in_stock: bool = True


class ItemResponse(Schema):
    name: str
    price: float
    quantity: int


def get_db_session():
    db = {"connected": True}
    try:
        yield db
    finally:
        db["connected"] = False


@app.get("/")
def root():
    return {"status": "ok", "framework": "Buckthorn", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post(
    "/items/{category}",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    category: str = Path(min_length=3),
    discount: float = Query(default=0.0, ge=0.0, le=1.0),
    payload: ItemCreate = ...,
    db: dict = Depends(get_db_session),
):
    payload.price = round(payload.price * (1.0 - discount), 2)
    return payload


@app.websocket("/ws/{channel}")
async def ws_echo(ws, channel: str):
    await ws.accept()
    msg = await ws.receive_text()
    await ws.send_text(f"[{channel}] Echo: {msg}")
    await ws.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

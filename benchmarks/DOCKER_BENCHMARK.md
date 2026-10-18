# 🐳 Running Buckthorn Benchmarks with Docker

This guide details how to build the containerized Buckthorn benchmark application and test it using `wrk`.

---

## 1. Build the Docker Image

Run the build command from the **repository root directory**:

```bash
docker build -f benchmarks/Dockerfile -t buckthorn-bench:latest .
```

* Multi-stage build compiles C11 and Cython extensions with `-O3 -march=native -flto`.
* The final runtime container is lightweight (<120MB) and contains only Debian slim, `libuv1`, `libssl3`, and the compiled application.

---

## 2. Start the Docker Container

### Option A: Standard Single-Core Benchmark (Matches `-t1`)
Pins the container to exactly 1 CPU core to measure isolated single-worker performance:

```bash
docker run -d \
  --name buckthorn-bench \
  --cpus="1.0" \
  -p 8000:8000 \
  buckthorn-bench:latest
```

### Option B: Single-Core with Full Logging Enabled
Enables pure C11 HTTP access logs and Cython application-level structured logging:

```bash
docker run -d \
  --name buckthorn-bench \
  --cpus="1.0" \
  -e BUCKTHORN_ACCESS_LOG=1 \
  -e BUCKTHORN_APP_LOG=1 \
  -p 8000:8000 \
  buckthorn-bench:latest
```

### Option C: Multi-Worker Scaling (`SO_REUSEPORT`)
Runs 4 workers across multiple cores:

```bash
docker run -d \
  --name buckthorn-bench \
  -p 8000:8000 \
  buckthorn-bench:latest \
  python -m buckthorn.cli run app.py:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 3. Verify Container is Running

Check container status:
```bash
docker ps --filter "name=buckthorn-bench"
```

Verify the endpoint is responding:
```bash
curl -i http://127.0.0.1:8000/health
```

Expected response:
```http
HTTP/1.1 200 OK
Content-Length: 87
Content-Type: application/json
...
{"status":"healthy","engine":"Buckthorn C99 / Cython runtime","active_version":"1.0.0"}
```

---

## 4. Run `wrk` Benchmarks Against the Container

From your host machine, execute single-core (`-t1`) benchmarks against port 8000:

### 1. Plaintext / JSON Liveness Probe (`GET /health`)
```bash
wrk -t1 -c100 -d10s --latency http://127.0.0.1:8000/health
```

### 2. API Key Secured Analytics (`GET /analytics`)
```bash
wrk -t1 -c100 -d10s --latency -H "X-API-Key: live_bench_api_key_999" http://127.0.0.1:8000/stores/101/analytics
```

### 3. SIMD JSON Schema POST Validation (`POST /items`)
```bash
wrk -t1 -c100 -d10s --latency -s benchmarks/post_item.lua http://127.0.0.1:8000/items
```

### 4. Dynamic Path & Query Inventory with DI (`GET /inventory`)
```bash
wrk -t1 -c100 -d10s --latency -H "X-Tenant-ID: tenant-corp-01" "http://127.0.0.1:8000/stores/101/inventory/SKU-9942?currency=USD"
```

### 5. Secure Order Checkout with OpenSSL JWT (`POST /checkout`)
```bash
wrk -t1 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout
```

### 6. Realistic Mixed Production Traffic (75% GET, 20% POST, 5% Analytics)
```bash
wrk -t1 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/
```

---

## 5. View Logs & Stop the Container

View live container logs:
```bash
docker logs -f buckthorn-bench
```

Stop and remove the container:
```bash
docker stop buckthorn-bench && docker rm buckthorn-bench
```

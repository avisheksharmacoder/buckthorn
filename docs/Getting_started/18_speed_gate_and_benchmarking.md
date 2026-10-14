# 18. Built-in Benchmarking & CI/CD Speed Gates

## Overview
Performance regressions often slip into production unnoticed when developers introduce heavy ORM queries, unindexed lookups, or unoptimized serialization steps. Benchmarking is usually an afterthought that requires configuring third-party tools like `wrk`, `hey`, or `k6`. Buckthorn includes a **Built-in In-Memory Benchmark Runner** and **CI/CD Speed Gate** (`buckthorn bench`) directly inside the framework CLI.

---

## 1. Layman's Explanation
Think of tuning a race car:
- In traditional setups, you only find out if a change made the car slower after shipping it to a race track, hiring a timing crew, and running full laps. Most teams don't have the time to do this on every minor tweak, so slow code slips into production.
- Buckthorn puts a dynamometer (a built-in speed testing machine) directly in your garage. With a single command, it revs your application engine through 50,000 simulated requests in 0.3 seconds. 

Even better, you can set a **Speed Gate**: *"If any pull request makes this API run slower than 80,000 requests per second, fail the build immediately!"* This guarantees that your team never accidentally merges slow code.

---

## 2. Technical Explanation
1. **The In-Memory Benchmark Harness:**
   - Implemented in [buckthorn/cli.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/cli.py#L110-L190).
   - Rather than suffering from operating system TCP socket overhead, port exhaustion, and loopback networking limits, the benchmark harness passes formatted raw HTTP/1.1 wire frames directly into the C routing pipeline.
   - It exercises the complete production path:
     1. SIMD HTTP parsing via `picohttpparser` in arena memory.
     2. C Radix Tree path resolution (`rax.c`).
     3. Parameter extraction and C validation.
     4. Dependency DAG slot execution.
     5. User route handler execution.
     6. Native C `yyjson` serialization into the output buffer.
2. **High-Precision Microsecond Metrics:**
   - Measures individual request iterations using monotonic hardware timers (`time.perf_counter_ns`).
   - Calculates:
     - **Throughput:** Requests per second (`req/sec`).
     - **Average Latency:** In microseconds (`µs`).
     - **Percentiles:** p50 (median), p95, and p99 tail latencies.
3. **Automated CI/CD Quality Speed Gate (`--gate`):**
   - The `--gate <threshold>` parameter defines a strict performance floor.
   - If the measured throughput falls below the gate threshold (e.g. `measured: 72,000 req/sec < gate: 80,000 req/sec`), the command exits with return code `1`.
   - Adding this to GitHub Actions, GitLab CI, or Jenkins prevents performance regressions from ever reaching production branches.

---

## 3. Code Example & CLI Usage

### A. Run an Instant Benchmark
```bash
# Benchmarks the /items endpoint with 50,000 iterations
buckthorn bench main:app --path /items --iterations 50000
```

### Output:
```text
============================================================
           BUCKTHORN IN-MEMORY BENCHMARK HARNESS            
============================================================
Target:      main:app
Endpoint:    GET /items
Iterations:  50,000
Warmup:      1,000 requests... Done.

Running benchmark...
------------------------------------------------------------
Completed 50,000 requests in 0.384 seconds.

  Throughput:       130,245.81 req/sec
  Avg Latency:      7.68 µs
  p50 Latency:      7.21 µs
  p95 Latency:      11.45 µs
  p99 Latency:      16.80 µs
============================================================
```

### B. Enforcing a Speed Gate in CI/CD
```bash
# Fails the pipeline if throughput drops below 100,000 req/sec
buckthorn bench main:app --path /items --iterations 20000 --gate 100000
```

If throughput passes:
```text
[PASS] Throughput 128,450 req/sec satisfies speed gate of 100,000 req/sec.
(Exit code 0)
```

If a performance regression was introduced:
```text
[FAIL] Speed gate failed: 85,200 req/sec is below required threshold of 100,000 req/sec.
(Exit code 1)
```

### C. GitHub Actions Workflow Integration (`.github/workflows/perf_gate.yml`)
```yaml
name: Performance Speed Gate

on:
  pull_request:
    branches: [main]

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install Dependencies
        run: |
          sudo apt-get install -y libuv1-dev libssl-dev
          pip install -e .
      - name: Run Buckthorn Speed Gate
        run: |
          buckthorn bench main:app --path /api/health --iterations 30000 --gate 90000
```

---

## 4. Part-by-Part CLI Explanation

1. `target: str` (e.g. `main:app`):
   - Loads your application module and compiles all route tables.
2. `--path <url>` (default: `/`):
   - The specific route endpoint to benchmark.
3. `--method <verb>` (default: `GET`):
   - The HTTP method to test (`GET`, `POST`, `PUT`, `DELETE`).
4. `--body <json_string>`:
   - For `POST`/`PUT` endpoints, specifies the raw JSON body payload to exercise deserialization and validation.
5. `--iterations <count>` (default: `10000`):
   - The total number of benchmark requests to execute.
6. `--gate <req_per_sec>`:
   - Sets the minimum acceptable requests per second. Exits with code 1 if not met.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | Standard Python Frameworks | Buckthorn |
|---|---|---|
| **Benchmarking Tool** | None included; requires installing `wrk`, `hey`, or `k6` | **Built-in `buckthorn bench` CLI command** |
| **Execution Mode** | Dependent on OS network stack and port availability | **High-precision in-memory C harness** |
| **CI/CD Integration** | Complex shell scripts and background server orchestration | **Single command with `--gate <N>` exit-code enforcement** |
| **Cold Starts** | Skewed by initial JIT/import costs | **Automated pre-benchmark warmup phase** (1,000 requests) |
| **Latency Precision** | Milliseconds | **Microseconds (`µs`) with full percentiles** (p50, p95, p99) |

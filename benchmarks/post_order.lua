-- benchmarks/post_order.lua
--[[
================================================================================
Buckthorn Authenticated POST Checkout Benchmark Script (wrk)
================================================================================
Exercises:
  * OpenSSL HMAC-SHA256 JWT signature & OAuth2 scopes verification in pure C.
  * Single-pass SIMD yyjson schema validation directly on AST pointers.
  * Dependency injection with database transaction generator lifecycle.
  * Direct-to-buffer response model filtering (internal fields stripped in C).
  * Pre-baked CORS, SecurityHeaders, and RateLimit C middlewares.
  * Cython request timing & telemetry middleware injection.

================================================================================
COMMANDS TO EXECUTE:
================================================================================

1. Standard Benchmark (1 thread, 100 connections, 10 seconds):
    wrk -t1 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout

2. Multi-Core Benchmark (4 threads, 100 connections, 10 seconds):
    wrk -t4 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout

3. High-Concurrency Stress Test (8 threads, 400 connections, 20 seconds):
    wrk -t8 -c400 -d20s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout

================================================================================
]]

wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"
wrk.headers["X-Tenant-ID"] = "tenant-corp-01"
wrk.headers["Authorization"] = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtZXJjaGFudF83NzAxIiwib3JnX2lkIjoib3JnX2VudGVycHJpc2VfYWxwaGEiLCJyb2xlIjoiYWRtaW4iLCJzY29wZXMiOlsib3JkZXJzOndyaXRlIiwiaW52ZW50b3J5OnJlYWQiLCJhbmFseXRpY3M6cmVhZCJdLCJleHAiOjIwMDAwMDAwMDB9.-QbjIBgKqSFb5XVsDR-NYatcdMDKEd4KsZ3EPsAWebM"

wrk.body = '{"sku":"SKU-9942","customer_email":"buyer@example.com","unit_price":49.99,"quantity":2,"shipping_method":"express","customer_note":"Urgent warehouse dispatch","tags":["retail","priority"]}'

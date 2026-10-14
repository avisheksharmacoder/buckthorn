-- benchmarks/mixed_traffic.lua
--[[
================================================================================
Buckthorn Realistic Production Mixed-Traffic Benchmark (wrk)
================================================================================
Traffic Profile:
  * 75% GET /stores/{store_id}/inventory/{sku}?currency=USD
        - Dynamic integer path parameters + string SKU path validation
        - Query string parameter parsing and defaults
        - Header tenant alias extraction ("X-Tenant-ID")
        - Multi-tier generator DB session dependency injection & cleanup
  * 20% POST /stores/{store_id}/checkout
        - OpenSSL HMAC-SHA256 JWT signature verification & OAuth2 scope check
        - Single-pass SIMD yyjson payload parsing & schema validation
        - Response model field filtering (internal fields stripped in C)
  *  5% GET /stores/{store_id}/analytics?period=today
        - Constant-time API Key header verification (CRYPTO_memcmp)
        - Fast query string validation & analytics reporting

Middlewares applied across all requests:
  - Tier-1 C Middlewares: Pre-baked CORS, SecurityHeaders, RateLimiter.
  - Tier-2 Cython HTTP Hook: Dynamic execution timing & server metadata headers.

================================================================================
COMMANDS TO EXECUTE:
================================================================================

1. Standard Realistic Traffic Mix (1 thread, 100 connections, 10 seconds):
    wrk -t1 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

2. Multi-Core Realistic Traffic Mix (4 threads, 100 connections, 10 seconds):
    wrk -t4 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

3. High-Concurrency Production Stress Mix (8 threads, 400 connections, 20 seconds):
    wrk -t8 -c400 -d20s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

================================================================================
]]

local token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtZXJjaGFudF83NzAxIiwib3JnX2lkIjoib3JnX2VudGVycHJpc2VfYWxwaGEiLCJyb2xlIjoiYWRtaW4iLCJzY29wZXMiOlsib3JkZXJzOndyaXRlIiwiaW52ZW50b3J5OnJlYWQiLCJhbmFseXRpY3M6cmVhZCJdLCJleHAiOjIwMDAwMDAwMDB9.-QbjIBgKqSFb5XVsDR-NYatcdMDKEd4KsZ3EPsAWebM"

local get_headers = {
    ["X-Tenant-ID"] = "tenant-corp-01",
    ["Accept"] = "application/json"
}

local post_headers = {
    ["Content-Type"] = "application/json",
    ["X-Tenant-ID"] = "tenant-corp-01",
    ["Authorization"] = token,
    ["Accept"] = "application/json"
}

local analytics_headers = {
    ["X-API-Key"] = "live_bench_api_key_999",
    ["Accept"] = "application/json"
}

local order_payload = '{"sku":"SKU-9942","customer_email":"buyer@example.com","unit_price":49.99,"quantity":2,"shipping_method":"express","customer_note":"Urgent warehouse dispatch","tags":["retail","priority"]}'

-- Seed random per thread
function setup(thread)
    thread:set("id", tostring(thread))
end

function init(args)
    math.randomseed(os.time())
end

request = function()
    local roll = math.random(100)

    if roll <= 75 then
        -- 75% Traffic: Dynamic Inventory Lookup
        local store_id = 100 + math.random(1, 20)
        local sku_num = 9900 + math.random(1, 100)
        local path = "/stores/" .. store_id .. "/inventory/SKU-" .. sku_num .. "?currency=USD"
        return wrk.format("GET", path, get_headers, nil)

    elseif roll <= 95 then
        -- 20% Traffic: Authenticated Checkout
        local store_id = 100 + math.random(1, 20)
        local path = "/stores/" .. store_id .. "/checkout"
        return wrk.format("POST", path, post_headers, order_payload)

    else
        -- 5% Traffic: API-Key Protected Analytics
        local store_id = 100 + math.random(1, 20)
        local path = "/stores/" .. store_id .. "/analytics?period=today"
        return wrk.format("GET", path, analytics_headers, nil)
    end
end

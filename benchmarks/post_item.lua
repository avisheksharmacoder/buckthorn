-- benchmarks/post_item.lua
--[[
wrk Lua Script for JSON POST Schema Validation Benchmark
=========================================================

Command to execute:
    wrk -t4 -c100 -d10s --latency -s benchmarks/post_item.lua http://127.0.0.1:8000/items

High-concurrency stress command:
    wrk -t8 -c400 -d20s --latency -s benchmarks/post_item.lua http://127.0.0.1:8000/items
]]

wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"
wrk.body = '{"name":"Mechanical Keyboard","price":149.99,"quantity":2,"in_stock":true}'

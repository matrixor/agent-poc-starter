# tools/test_from_gateway.py -- run inside mcpgateway container if needed
import json, urllib.request, urllib.parse, urllib.error, os
url_base = os.environ.get("TARGET", "http://mcp-sample:8000")
url = url_base.rstrip("/") + "/mcp"
body = {
    "jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2025-03-26","capabilities":{"roots":{},"sampling":{}},
              "clientInfo":{"name":"cf-debug","version":"0.1"}}
}
headers={"Content-Type":"application/json", "Accept":"application/json, text/event-stream, */*"}
req=urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
with urllib.request.urlopen(req, timeout=10) as r:
    print("Status:", r.status, "CT:", r.headers.get("Content-Type"))
    print(r.read().decode()[:800])

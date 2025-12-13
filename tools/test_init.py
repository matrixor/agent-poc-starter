# tools/test_init.py
import json, urllib.request, urllib.parse, urllib.error

INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {"tools": {}, "resources": {}, "prompts": {}, "sampling": {}, "roots": {}},
        "clientInfo": {"name": "local-test", "version": "0.4"}
    }
}

def post_json(url, body, max_redirects=3):
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream, */*"
    }
    for _ in range(max_redirects + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                print("Status:", resp.status)
                print("Content-Type:", resp.headers.get("Content-Type"))
                print("Mcp-Session-Id:", resp.headers.get("Mcp-Session-Id"))
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            raise
    raise RuntimeError("Too many redirects")

if __name__ == "__main__":
    for u in ("http://localhost:8081/mcp", "http://localhost:8081/mcp/"):
        try:
            status, text = post_json(u, INIT_BODY)
            print(f"OK via {u}:\n{text[:800]}")
            break
        except Exception as ex:
            print(f"Failed via {u}: {ex}")
    else:
        raise SystemExit(1)

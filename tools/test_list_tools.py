# tools/test_list_tools.py
import json, urllib.request, urllib.parse, urllib.error

ACCEPT = "application/json, text/event-stream, */*"
CLIENT_CAPS = {"tools": {}, "resources": {}, "prompts": {}, "sampling": {}, "roots": {}}

def post(url, body, session=None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": ACCEPT}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as resp:
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        text = resp.read().decode("utf-8")
        return resp.status, resp.headers.get("Content-Type"), sid, text

def try_base(base):
    init = {
        "jsonrpc":"2.0","id":1,"method":"initialize",
        "params":{"protocolVersion":"2025-03-26","capabilities": CLIENT_CAPS,
                  "clientInfo":{"name":"local-tooltest","version":"0.1"}}
    }
    st, ct, sid, txt = post(base, init)
    print(f"INIT @ {base} -> {st}, CT={ct}, SID={sid}")
    list_body={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"cursor": None}}
    st2, ct2, sid2, txt2 = post(base, list_body, session=sid)
    print(f"TOOLS/LIST -> {st2}, CT={ct2}, SID={sid2}")
    print(txt2[:1200])
    return True

if __name__ == "__main__":
    ok=False
    for u in ("http://localhost:8081/mcp", "http://localhost:8081/mcp/"):
        try:
            if try_base(u):
                ok=True
                break
        except Exception as e:
            print(u, "ERR:", e)
    if not ok:
        raise SystemExit(1)

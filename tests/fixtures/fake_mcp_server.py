"""A tiny MCP server over stdio for tests: one `echo` tool and one `fail` tool."""
import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back, upper-cased.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {"name": "fail", "description": "Always errors.", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(ident, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": ident}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    ident = msg.get("id")
    if method == "initialize":
        reply(ident, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}})
    elif method == "notifications/initialized":
        sys.stderr.write("fake server ready\n")
        sys.stderr.flush()
    elif method == "tools/list":
        reply(ident, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            reply(ident, {"content": [{"type": "text", "text": str(args.get("text", "")).upper()}]})
        elif name == "fail":
            reply(ident, {"content": [{"type": "text", "text": "boom"}], "isError": True})
        else:
            reply(ident, error={"code": -32601, "message": f"unknown tool {name}"})
    elif ident is not None:
        reply(ident, error={"code": -32601, "message": f"unknown method {method}"})

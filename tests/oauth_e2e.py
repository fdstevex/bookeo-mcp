"""End-to-end check of the OAuth flow a Claude connector performs.

Starts the real HTTP server with a throwaway AUTH_TOKEN (Bookeo is never
called) and walks discovery, registration, login, token exchange, an MCP call
and refresh, plus the ways each step should fail. Run from the repo root:

    .venv/bin/python tests/oauth_e2e.py
"""
import base64, hashlib, json, os, re, secrets, subprocess, sys, time
from urllib.parse import urlparse, parse_qs
import httpx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT, TOKEN = 8765, "test-token-" + secrets.token_hex(8)
BASE = f"http://localhost:{PORT}"
env = {**os.environ, "AUTH_TOKEN": TOKEN, "PUBLIC_URL": BASE, "PYTHONPATH": REPO,
       "API_KEY": "x", "API_SECRET": "y"}
def start():
    p = subprocess.Popen([sys.executable, "-m", "bookeo_mcp.server", "--transport", "streamable-http", "--port", str(PORT)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: httpx.get(BASE + "/mcp", timeout=1); return p
        except httpx.TransportError: time.sleep(0.2)
    raise SystemExit("server did not start")
def ok(name, cond, extra=""):
    print("PASS" if cond else "FAIL", name, extra)
    return bool(cond)
results = []
srv = start()
try:
    c = httpx.Client(base_url=BASE, timeout=10)
    H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"e2e","version":"0"}}}

    r = c.post("/mcp", json=init, headers=H)
    results.append(ok("unauthenticated /mcp -> 401 + resource_metadata", r.status_code == 401 and "resource_metadata=" in r.headers.get("www-authenticate",""), r.headers.get("www-authenticate","")[:90]))
    prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
    results.append(ok("protected-resource metadata", prm.get("resource") == BASE + "/mcp" and prm["authorization_servers"][0].rstrip("/") == BASE, json.dumps(prm)[:120]))
    asm = c.get("/.well-known/oauth-authorization-server").json()
    results.append(ok("AS metadata has register/authorize/token + S256", all(k in asm for k in ("registration_endpoint","authorization_endpoint","token_endpoint")) and "S256" in asm.get("code_challenge_methods_supported", [])))

    REDIRECT = "https://claude.ai/api/mcp/auth_callback"
    reg = c.post("/register", json={"client_name":"Claude","redirect_uris":[REDIRECT],"grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"})
    client_id = reg.json().get("client_id", "")
    results.append(ok("dynamic client registration", reg.status_code == 201 and client_id, f"client_id len={len(client_id)}"))

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"response_type":"code","client_id":client_id,"redirect_uri":REDIRECT,"code_challenge":challenge,"code_challenge_method":"S256","state":"st123","resource":BASE+"/mcp"}
    r = c.get("/authorize", params=q)
    loc = r.headers.get("location","")
    results.append(ok("/authorize redirects to /login", r.status_code == 302 and urlparse(loc).path == "/login"))
    tx = parse_qs(urlparse(loc).query)["tx"][0]
    r = c.get(loc)
    results.append(ok("login page renders, names the client", r.status_code == 200 and "Claude is asking" in r.text and 'type="password"' in r.text))

    t0 = time.time(); r = c.post("/login", data={"tx": tx, "password": "wrong"})
    results.append(ok("wrong token -> 401, slowed, no redirect", r.status_code == 401 and "location" not in r.headers and time.time()-t0 >= 1))
    r = c.post("/login", data={"tx": tx[:-4] + "AAAA", "password": TOKEN})
    results.append(ok("tampered login transaction rejected even with right token", r.status_code == 400))

    r = c.post("/login", data={"tx": tx, "password": TOKEN})
    back = urlparse(r.headers.get("location","")); bq = parse_qs(back.query)
    results.append(ok("right token -> redirect to client with code+state", r.status_code == 302 and back.netloc == "claude.ai" and bq.get("state") == ["st123"] and "code" in bq))
    code = bq["code"][0]

    tok = lambda **d: c.post("/token", data={"client_id": client_id, **d})
    r = tok(grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier="not-the-verifier-" + "x"*40)
    results.append(ok("wrong PKCE verifier rejected", r.status_code == 400, r.text[:80]))
    r = tok(grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)
    t = r.json()
    results.append(ok("code -> access+refresh tokens", r.status_code == 200 and t.get("access_token") and t.get("refresh_token") and t.get("expires_in") == 3600))
    r = tok(grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)
    results.append(ok("authorization code is single-use", r.status_code == 400, r.text[:80]))

    def mcp_init(bearer):
        return c.post("/mcp", json=init, headers={**H, "Authorization": f"Bearer {bearer}"})
    r = mcp_init(t["access_token"])
    results.append(ok("MCP initialize with OAuth access token", r.status_code == 200 and "Bookeo" in r.text))
    sid = r.headers.get("mcp-session-id")
    AH = {**H, "Authorization": f"Bearer {t['access_token']}", "mcp-session-id": sid}
    c.post("/mcp", json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=AH)
    r = c.post("/mcp", json={"jsonrpc":"2.0","id":2,"method":"tools/list"}, headers=AH)
    results.append(ok("tools/list over OAuth", r.status_code == 200 and "search_bookings_by_date" in r.text))
    results.append(ok("static AUTH_TOKEN bearer still works (Claude Code)", mcp_init(TOKEN).status_code == 200))
    results.append(ok("refresh token is not usable as access token", mcp_init(t["refresh_token"]).status_code == 401))
    results.append(ok("garbage bearer rejected", mcp_init("nope").status_code == 401))

    srv.terminate(); srv.wait(); srv = start()
    results.append(ok("access token survives a server restart", mcp_init(t["access_token"]).status_code == 200))
    r = tok(grant_type="refresh_token", refresh_token=t["refresh_token"])
    t2 = r.json()
    results.append(ok("refresh after restart (client id + refresh token both stateless)", r.status_code == 200 and t2.get("access_token") not in (None, t["access_token"])))
    results.append(ok("refreshed access token works", mcp_init(t2["access_token"]).status_code == 200))

    other = c.post("/register", json={"client_name":"Evil","redirect_uris":["https://evil.example/cb"],"grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"}).json()["client_id"]
    r = c.post("/token", data={"client_id": other, "grant_type":"refresh_token", "refresh_token": t2["refresh_token"]})
    results.append(ok("another client cannot use this refresh token", r.status_code == 400))
    r = c.get("/authorize", params={**q, "redirect_uri": "https://evil.example/cb"})
    results.append(ok("unregistered redirect_uri refused", r.status_code == 400 and "location" not in r.headers))
finally:
    srv.terminate()
print(f"\n{sum(results)}/{len(results)} passed"); sys.exit(0 if all(results) else 1)

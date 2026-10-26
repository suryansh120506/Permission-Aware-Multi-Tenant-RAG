"""
app.py - Streamlit demo dashboard ("Secure RAG Portal").

This UI deliberately does NOT import rag.py or auth.py. Every operation goes
over HTTP to the FastAPI service, just as a separately deployed frontend would.

Any text that comes from the API (answers, document chunks) is HTML-escaped
before rendering. The page inserts raw HTML, so document content must never
be able to inject markup or script (stored XSS through the knowledge base).

Run:  streamlit run app.py
"""

import html
import json
import os
import time
from pathlib import Path

import requests
import streamlit as st

API_BASE_URL = os.getenv("RAG_API_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT_S = 30
STARTUP_WAIT_S = 60
SCENE_TEMPLATE = Path(__file__).with_name("scene.html")

# In a real frontend these tokens would come from an OIDC login flow. The
# display fields (name, tier, accent) are cosmetic only; authorization is
# decided entirely by the API from the token.
USERS: dict[str, dict[str, str]] = {
    "Alice (AcmeCorp / HR)": {
        "token": "alice-hr-token", "tenant_id": "AcmeCorp", "role": "HR",
        "name": "Alice", "user_id": "alice", "tier": "Tier 2 (HR & General)",
        "accent": "green", "hex": "#30d158",
    },
    "Bob (AcmeCorp / Engineering)": {
        "token": "bob-eng-token", "tenant_id": "AcmeCorp", "role": "Engineering",
        "name": "Bob", "user_id": "bob", "tier": "Tier 2 (Eng & General)",
        "accent": "blue", "hex": "#0a84ff",
    },
}

# Each document is ingested BY a user who owns that scope. The API rejects
# writes into a role scope other than the caller's own.
SEED_DOCUMENTS = [
    (
        "Alice (AcmeCorp / HR)",
        {
            "document": "The 2026 Q3 Bonus pool is $500,000.",
            "metadata": {"tenant_id": "AcmeCorp", "required_role": "HR", "source": "hr/compensation-2026-q3"},
        },
    ),
    (
        "Bob (AcmeCorp / Engineering)",
        {
            "document": "The new API architecture uses GraphQL.",
            "metadata": {"tenant_id": "AcmeCorp", "required_role": "Engineering", "source": "eng/architecture-rfc"},
        },
    ),
]

SAMPLE_QUESTIONS = ["What is the bonus pool?", "What does the new API architecture use?"]


# --------------------------------------------------------------------------- #
# HTTP client (unchanged backend integration)
# --------------------------------------------------------------------------- #
@st.cache_resource
def http() -> requests.Session:
    return requests.Session()


def api_post(path: str, token: str, payload: dict) -> requests.Response:
    return http().post(
        f"{API_BASE_URL}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_S,
    )


def error_detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except ValueError:
        return resp.text


def wait_for_api() -> bool:
    deadline = time.monotonic() + STARTUP_WAIT_S
    while time.monotonic() < deadline:
        try:
            if http().get(f"{API_BASE_URL}/healthz", timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def seed_documents() -> list[str]:
    """Ingest the demo documents through the API. Returns a status line per document."""
    log = []
    for owner, payload in SEED_DOCUMENTS:
        source = payload["metadata"]["source"]
        try:
            resp = api_post("/v1/ingest", USERS[owner]["token"], payload)
        except requests.RequestException as exc:
            log.append(f"❌ {source}: {exc}")
            continue
        if resp.status_code == 201:
            data = resp.json()
            log.append(f"✅ {source} → {data['tenant_id']}/{data['required_role']} (ingested as {owner.split()[0]})")
        else:
            log.append(f"❌ {source}: HTTP {resp.status_code} {error_detail(resp)}")
    return log


def ask(token: str, question: str) -> dict:
    try:
        resp = api_post("/v1/query", token, {"question": question})
    except requests.RequestException as exc:
        return {"error": f"Could not reach the API at {API_BASE_URL}: {exc}"}
    if not resp.ok:
        return {"error": f"HTTP {resp.status_code}: {error_detail(resp)}"}
    return resp.json()


# --------------------------------------------------------------------------- #
# Styling. Palette follows Apple's dark system colours: graphite surfaces,
# one neutral text scale, and green / blue / red used only for meaning.
# --------------------------------------------------------------------------- #
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg: #000000;
  --surface: #161617;
  --surface-2: #1d1d1f;
  --surface-3: #2c2c2e;
  --hair: rgba(255,255,255,.09);
  --text: #f5f5f7;
  --text-2: #a1a1a6;
  --text-3: #6e6e73;
  --green: #30d158;
  --blue: #0a84ff;
  --red: #ff453a;
  --sans: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', Inter, 'Segoe UI', system-ui, sans-serif;
  --mono: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace;
  --ease: cubic-bezier(.22,.8,.26,1);
}

html, body, .stApp { background: var(--bg) !important; color: var(--text); font-family: var(--sans); -webkit-font-smoothing: antialiased; }
.stApp::before {          /* one soft light source, top centre */
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background: radial-gradient(900px 420px at 50% -8%, rgba(255,255,255,.07), transparent 70%);
}
[data-testid="stMainBlockContainer"] { max-width: 1120px; padding: 3.4rem 1.6rem 6rem; position: relative; z-index: 1; }
h1, h2, h3, p, label, span, div { font-family: var(--sans); }

/* ---------- Hide Streamlit chrome ---------- */
#MainMenu, footer, [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
[data-testid="stStatusWidget"], [data-testid="stDecoration"] { display: none !important; }

/* The expand-sidebar button lives INSIDE the header toolbar, so the toolbar must
   stay in the DOM. The header is made click-through and invisible, and only the
   button is restyled as a floating control. */
header[data-testid="stHeader"] { background: transparent !important; box-shadow: none !important; pointer-events: none; }
[data-testid="stToolbar"] { pointer-events: none; background: transparent !important; }
[data-testid="stExpandSidebarButton"] {
  pointer-events: auto; position: fixed; top: 16px; left: 16px; z-index: 1000002;
  display: inline-flex; align-items: center; gap: 8px; height: 42px; padding: 0 16px 0 12px; border-radius: 999px;
  background: rgba(44,44,46,.72) !important; border: 1px solid rgba(255,255,255,.14) !important;
  -webkit-backdrop-filter: blur(20px) saturate(1.6); backdrop-filter: blur(20px) saturate(1.6);
  box-shadow: 0 8px 28px rgba(0,0,0,.55); color: var(--text) !important; cursor: pointer;
  transition: transform .25s var(--ease), background .2s;
}
[data-testid="stExpandSidebarButton"]:hover { background: rgba(72,72,74,.8) !important; transform: translateY(-1px) scale(1.02); }
[data-testid="stExpandSidebarButton"] * { color: var(--text) !important; }
[data-testid="stExpandSidebarButton"]::after { content: "Identity"; font: 600 13px var(--sans); letter-spacing: .01em; color: var(--text); }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"] { color: var(--text-3) !important; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] { background: var(--surface-2) !important; border-right: 1px solid var(--hair); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: .4rem; }
.brand { display: flex; align-items: center; gap: 11px; padding: 6px 0 4px; }
.brand-mark {
  width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 17px;
  background: linear-gradient(160deg, #3a3a3c, #1c1c1e); border: 1px solid var(--hair);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 6px 14px rgba(0,0,0,.4);
}
.brand b { display: block; font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.brand span { display: block; font-size: 12px; color: var(--text-3); }
.eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--text-3); margin: 22px 0 8px; }

[data-testid="stSidebar"] [data-baseweb="select"] > div {
  background: var(--surface-3) !important; border: 1px solid var(--hair) !important; border-radius: 12px !important; min-height: 44px;
}
[data-testid="stSidebar"] [data-baseweb="select"] * { font-family: var(--sans) !important; font-size: 14px; }

.badge {
  display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 6px 12px; border-radius: 999px;
  font-size: 12px; font-weight: 600; letter-spacing: .01em;
}
.badge i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 10px currentColor; }
.badge.green { color: var(--green); background: rgba(48,209,88,.12); border: 1px solid rgba(48,209,88,.35); }
.badge.blue  { color: var(--blue);  background: rgba(10,132,255,.13); border: 1px solid rgba(10,132,255,.4); }

[data-testid="stSidebar"] .stButton button {
  width: 100%; justify-content: flex-start; text-align: left; background: var(--surface-3); color: var(--text);
  border: 1px solid var(--hair); border-radius: 12px; font: 500 13px var(--sans); padding: .6rem .85rem;
  transition: background .2s, transform .25s var(--ease);
}
[data-testid="stSidebar"] .stButton button:hover { background: #3a3a3c; transform: translateY(-1px); border-color: rgba(255,255,255,.18); }

.kb { display: flex; gap: 10px; align-items: flex-start; font-size: 12px; color: var(--text-2); padding: 9px 11px; border-radius: 12px; background: var(--surface-3); margin-bottom: 7px; }
.kb::before { content: ""; flex: none; width: 7px; height: 7px; border-radius: 50%; margin-top: 4px; background: var(--green); }
.kb.err::before { background: var(--red); }
.kb.err { color: #ffb4ae; }
.kb span { overflow-wrap: anywhere; font-family: var(--mono); font-size: 11.5px; }
.gateway { font: 11px var(--mono); color: var(--text-3); margin-top: 14px; overflow-wrap: anywhere; }

/* ---------- Hero ---------- */
.hero { text-align: center; padding: 1.2rem 0 2.4rem; }
.hero .kicker { font-size: 14px; font-weight: 600; color: var(--text-2); letter-spacing: .01em; }
.hero h1 {
  font-size: clamp(2.6rem, 6.2vw, 4.4rem); line-height: 1.04; font-weight: 700; letter-spacing: -.035em; margin: .35em 0 .3em; padding: 0;
  background: linear-gradient(180deg, #ffffff 30%, #8e8e93 115%); -webkit-background-clip: text; background-clip: text; color: transparent;
}
.hero p { max-width: 620px; margin: 0 auto; font-size: clamp(1rem, 1.6vw, 1.22rem); line-height: 1.5; color: var(--text-2); }
.hero code { font: 500 .86em var(--mono); color: var(--text); background: var(--surface-3); padding: 2px 7px; border-radius: 7px; }

/* ---------- Bento tiles ---------- */
.tile {
  position: relative; border-radius: 28px; padding: 24px; background: var(--surface); border: 1px solid var(--hair);
  transition: transform .5s var(--ease), border-color .3s, box-shadow .5s var(--ease);
  transform-style: preserve-3d;
}
.tile:hover { transform: perspective(1100px) rotateX(2.2deg) translateY(-4px); border-color: rgba(255,255,255,.16); box-shadow: 0 30px 60px -28px rgba(0,0,0,.9); }
.tile + .tile { margin-top: 16px; }
.tile .eyebrow { margin: 0 0 12px; }
.tile h3 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; margin: 0 0 6px; }
.tile p { color: var(--text-2); font-size: 14px; line-height: 1.5; margin: 0; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 9px 16px; margin-top: 16px; font-size: 13px; }
.kv dt { color: var(--text-3); }
.kv dd { margin: 0; text-align: right; font-family: var(--mono); font-size: 12.5px; color: var(--text); overflow-wrap: anywhere; }
.filter { margin-top: 16px; padding: 12px 14px; border-radius: 14px; background: #0a0a0b; border: 1px solid var(--hair); font: 12px/1.7 var(--mono); color: var(--text-2); }
.filter b { color: var(--text); font-weight: 500; }
.filter .op { color: var(--text-3); }
.stat { display: flex; align-items: baseline; gap: 10px; margin-top: 2px; }
.stat .big { font-size: 40px; font-weight: 700; letter-spacing: -.03em; line-height: 1; }
.stat .unit { color: var(--text-3); font-size: 13px; }
.status-pill { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-pill i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.status-pill.ok { color: var(--green); background: rgba(48,209,88,.12); }
.status-pill.no { color: var(--red); background: rgba(255,69,58,.14); }
.status-pill.idle { color: var(--text-2); background: var(--surface-3); }

/* ---------- Chat ---------- */
.section-title { font-size: 28px; font-weight: 700; letter-spacing: -.025em; margin: 3.2rem 0 .4rem; }
.section-sub { color: var(--text-2); font-size: 15px; margin-bottom: 1.4rem; }
[data-testid="stChatMessage"] { background: transparent !important; padding: .5rem 0 !important; }
[data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] { display: none !important; }
.user-row { display: flex; justify-content: flex-end; }
.user-msg { max-width: 78%; padding: 11px 17px; border-radius: 22px 22px 6px 22px; background: var(--surface-3); font-size: 15px; line-height: 1.45; }

.term {
  border-radius: 20px; overflow: hidden; background: #0a0a0b; border: 1px solid var(--hair);
  box-shadow: 0 24px 50px -24px rgba(0,0,0,.95); font-family: var(--mono);
  animation: rise .5s var(--ease) both;
}
@keyframes rise { from { opacity: 0; transform: translateY(10px) scale(.985); } to { opacity: 1; transform: none; } }
.term-bar { display: flex; align-items: center; gap: 7px; padding: 12px 16px; background: var(--surface-2); border-bottom: 1px solid var(--hair); }
.tdot { width: 11px; height: 11px; border-radius: 50%; } .tdot.r { background: #ff5f57; } .tdot.y { background: #febc2e; } .tdot.g { background: #28c840; }
.term-title { margin-left: 10px; font-size: 12px; color: var(--text-3); }
.term-lock { margin-left: auto; font-size: 11px; color: var(--green); }
.term-body { padding: 18px 20px 16px; font-size: 13.5px; line-height: 1.7; color: var(--text-2); }
.pill-ok {
  display: inline-flex; align-items: center; gap: 8px; padding: 5px 12px; border-radius: 999px; font: 600 12px var(--sans);
  color: var(--green); background: rgba(48,209,88,.12); border: 1px solid rgba(48,209,88,.32);
}
.pill-ok i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 10px currentColor; }
.term-cmd { margin: 14px 0 4px; color: var(--text-3); } .term-cmd .ps1 { color: var(--green); }
.term-out { color: var(--text); font-size: 15px; overflow-wrap: anywhere; }
.term-src { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--hair); }
.term-src-h { font: 600 11px var(--sans); letter-spacing: .08em; text-transform: uppercase; color: var(--text-3); margin-bottom: 8px; }
.src { padding: 10px 12px; border-radius: 12px; background: var(--surface-2); margin-bottom: 8px; }
.src-meta { font-size: 11.5px; color: var(--green); } .src-text { color: var(--text); font-size: 13px; overflow-wrap: anywhere; }
.term-meta { margin-top: 12px; font-size: 11.5px; color: var(--text-3); }
.term-meta b, .blocked-meta b { color: var(--text-2); font-weight: 500; }

.blocked {
  border-radius: 20px; padding: 20px 22px 18px; animation: rise .5s var(--ease) both, alarm 1.4s ease-in-out 2;
  background: linear-gradient(180deg, rgba(255,69,58,.22), rgba(120,18,12,.32)); border: 1px solid rgba(255,69,58,.7);
  box-shadow: 0 0 0 1px rgba(255,69,58,.12), 0 18px 50px -14px rgba(255,69,58,.45);
}
@keyframes alarm { 50% { box-shadow: 0 0 0 1px rgba(255,69,58,.35), 0 18px 70px -8px rgba(255,69,58,.75); } }
.blocked-head { display: flex; align-items: center; gap: 10px; color: var(--red); font-weight: 700; font-size: 13px; letter-spacing: .06em; text-transform: uppercase; }
.blocked-code { margin-left: auto; font: 600 11px var(--mono); color: #ffd7d4; background: rgba(255,69,58,.28); border: 1px solid rgba(255,69,58,.55); padding: 3px 10px; border-radius: 999px; }
.blocked-msg { color: #fff1f0; font-size: 17px; font-weight: 500; letter-spacing: -.01em; margin: 12px 0 14px; line-height: 1.4; }
.blocked-meta { font: 12px/1.7 var(--mono); color: #ffaaa4; padding-top: 12px; border-top: 1px solid rgba(255,69,58,.35); }
.api-error { border-radius: 16px; padding: 14px 16px; font: 13px var(--mono); color: #ffd60a; background: rgba(255,214,10,.08); border: 1px solid rgba(255,214,10,.35); }

.empty { border-radius: 22px; padding: 26px; color: var(--text-2); font-size: 15px; line-height: 1.55; background: var(--surface); border: 1px dashed rgba(255,255,255,.14); text-align: center; }
.empty b { color: var(--text); font-weight: 600; }
.empty code { font: 500 .88em var(--mono); background: var(--surface-3); color: var(--text); padding: 2px 7px; border-radius: 7px; }

/* Chat input */
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background: transparent !important; max-width: 1120px; }
[data-testid="stChatInput"] > div { background: var(--surface-2) !important; border: 1px solid var(--hair) !important; border-radius: 999px !important; }
[data-testid="stChatInput"] textarea { font: 15px var(--sans) !important; color: var(--text) !important; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
"""


def esc(value: object) -> str:
    """HTML-escape untrusted text for insertion into raw HTML."""
    return html.escape(str(value)).replace("\n", "<br>")


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def render_seed_log(lines: list[str]) -> None:
    items = []
    for line in lines:
        ok = line.startswith("✅")
        text = line.lstrip("✅❌ ").strip()
        items.append(f'<div class="kb {"" if ok else "err"}"><span>{esc(text)}</span></div>')
    st.html("".join(items))


def filter_html(tenant: str, role: str) -> str:
    return (
        f'<div class="filter"><span class="op">where</span> $and [<br>'
        f'&nbsp;&nbsp;tenant_id <span class="op">==</span> <b>"{esc(tenant)}"</b>,<br>'
        f'&nbsp;&nbsp;required_role <span class="op">==</span> <b>"{esc(role)}"</b><br>]</div>'
    )


def render_scope_tile(user: dict[str, str]) -> None:
    st.html(
        '<div class="tile"><div class="eyebrow">Active scope</div>'
        f'<h3>{esc(user["name"])}</h3>'
        f'<p>{esc(user["user_id"])}@{esc(user["tenant_id"].lower())} · bearer ••••{esc(user["token"][-4:])}</p>'
        f'{filter_html(user["tenant_id"], user["role"])}</div>'
    )


def render_status_tile(last: dict | None) -> None:
    if last is None:
        pill, big, unit, sub = '<span class="status-pill idle"><i></i>Waiting</span>', "–", "", "Ask a question to see the filter at work."
    elif "error" in last:
        pill, big, unit, sub = '<span class="status-pill no"><i></i>Gateway error</span>', "–", "", esc(last["error"])
    elif last["context_found"]:
        n = len(last["sources"])
        pill = '<span class="status-pill ok"><i></i>Context retrieved</span>'
        big, unit, sub = str(n), "authorized chunk" + ("s" if n != 1 else ""), f'Answered in {esc(last["latency_ms"])} ms.'
    else:
        pill = '<span class="status-pill no"><i></i>Blocked at database</span>'
        big, unit, sub = "0", "authorized chunks", f'The LLM was never called · {esc(last["latency_ms"])} ms.'
    st.html(
        f'<div class="tile"><div class="eyebrow">Last request</div>{pill}'
        f'<div class="stat" style="margin-top:14px"><span class="big">{big}</span><span class="unit">{unit}</span></div>'
        f"<p>{sub}</p></div>"
    )


def render_scene(user: dict[str, str], last: dict | None, nonce: int) -> None:
    """Interactive 3D map of the filter. Built from constants and the result flag only."""
    if last is None or "error" in last:
        state = "idle"
    else:
        state = "granted" if last["context_found"] else "blocked"
    params = {
        "tenants": ["AcmeCorp", "Globex"],
        "roles": ["HR", "Engineering"],
        "active": {"tenant": user["tenant_id"], "role": user["role"], "name": user["name"]},
        "accent": user["hex"],
        "state": state,
        "nonce": nonce,  # changes per request, so the pulse replays
    }
    payload = json.dumps(params).replace("</", "<\\/")
    st.iframe(SCENE_TEMPLATE.read_text(encoding="utf-8").replace("__PARAMS__", payload), height=432)


def render_user(question: str) -> None:
    st.html(f'<div class="user-row"><div class="user-msg">{esc(question)}</div></div>')


def render_assistant(result: dict) -> None:
    if "error" in result:
        st.html(f'<div class="api-error">⚠ Gateway error · {esc(result["error"])}</div>')
        return

    scope = result["scope"]
    filter_expr = (
        f'tenant_id=<b>"{esc(scope["tenant_id"])}"</b> AND required_role=<b>"{esc(scope["role"])}"</b>'
    )

    if not result["context_found"]:
        st.html(
            '<div class="blocked">'
            '<div class="blocked-head">⛔ Access boundary enforced'
            '<span class="blocked-code">0 AUTHORIZED CHUNKS</span></div>'
            f'<div class="blocked-msg">{esc(result["answer"])}</div>'
            '<div class="blocked-meta">'
            f"principal <b>{esc(scope['user_id'])}</b> · vector-db filter {filter_expr}<br>"
            f"LLM not invoked · {esc(result['latency_ms'])} ms"
            "</div></div>"
        )
        return

    sources = "".join(
        '<div class="src">'
        f'<div class="src-meta">[{i}] {esc(s["source"])} · similarity {s["similarity"]:.3f}</div>'
        f'<div class="src-text">{esc(s["text"])}</div>'
        "</div>"
        for i, s in enumerate(result["sources"], 1)
    )
    title = f'secure-rag://{esc(scope["tenant_id"].lower())}/{esc(scope["role"].lower())}'
    st.html(
        '<div class="term">'
        '<div class="term-bar"><span class="tdot r"></span><span class="tdot y"></span><span class="tdot g"></span>'
        f'<span class="term-title">{title}</span><span class="term-lock">🔒 scoped</span></div>'
        '<div class="term-body">'
        '<span class="pill-ok"><i></i>Secure Context Retrieved</span>'
        f'<div class="term-cmd"><span class="ps1">{esc(scope["user_id"])}@rag ~ %</span> generate --context authorized</div>'
        f'<div class="term-out">{esc(result["answer"])}</div>'
        f'<div class="term-src"><div class="term-src-h">Source chunks · {len(result["sources"])}</div>{sources}</div>'
        f'<div class="term-meta">vector-db filter {filter_expr} · {esc(result["latency_ms"])} ms</div>'
        "</div></div>"
    )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Secure RAG Portal", page_icon="🔒", layout="wide", initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)

if "seed_log" not in st.session_state:
    with st.spinner(f"Waiting for the API at {API_BASE_URL} and ingesting demo documents…"):
        if wait_for_api():
            st.session_state.seed_log = seed_documents()
        else:
            st.session_state.seed_log = [f"❌ API not reachable at {API_BASE_URL}. Start it with `uvicorn api:app`."]

with st.sidebar:
    st.html(
        '<div class="brand"><div class="brand-mark">🛡️</div><div><b>AcmeCorp IAM</b><span>Identity &amp; access</span></div></div>'
        '<div class="eyebrow">Switch User Context</div>'
    )
    user_label = st.selectbox("Switch User Context", list(USERS), key="active_user", label_visibility="collapsed")
    user = USERS[user_label]
    st.html(f'<span class="badge {user["accent"]}"><i></i>Access Level: {esc(user["tier"])}</span>')

    st.html('<div class="eyebrow">Test queries</div>')
    for q in SAMPLE_QUESTIONS:
        if st.button(q, use_container_width=True):
            st.session_state.pending_question = q

    st.html('<div class="eyebrow">Knowledge base</div>')
    render_seed_log(st.session_state.seed_log)
    if st.button("Re-ingest demo documents", use_container_width=True):
        st.session_state.seed_log = seed_documents()
        st.rerun()
    st.html(f'<div class="gateway">gateway · {esc(API_BASE_URL)}</div>')

# Separate chat history per user, so switching context never shows another user's answers.
history: list[dict] = st.session_state.setdefault("history", {}).setdefault(user_label, [])

# Handle the new question first, so every panel below reflects the latest result.
question = st.chat_input("Ask the secure knowledge base…") or st.session_state.pop("pending_question", None)
if question:
    with st.spinner("Resolving principal · applying scope filter · retrieving…"):
        result = ask(user["token"], question)
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": result})

last = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), None)

st.html(
    '<div class="hero"><div class="kicker">Zero-trust retrieval</div>'
    "<h1>Knowledge, kept<br>within bounds.</h1>"
    "<p>Every query is scoped inside the vector database by <code>tenant_id</code> and <code>required_role</code>. "
    "Both come from your bearer token, never from the client.</p></div>"
)

left, right = st.columns([1.55, 1], gap="medium")
with left:
    render_scene(user, last, nonce=len(history))
with right:
    render_scope_tile(user)
    render_status_tile(last)

st.html(
    '<div class="section-title">Ask anything.</div>'
    f'<div class="section-sub">Chatting as {esc(user["name"])}. Answers are built only from documents inside your scope.</div>'
)

if not history:
    st.html(
        f'<div class="empty">Session established as <b>{esc(user["name"])}</b> '
        f'(<code>{esc(user["tenant_id"])}/{esc(user["role"])}</code>).<br>'
        "Try a test query from the sidebar, or switch users and ask the same question.</div>"
    )

for msg in history:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            render_user(msg["content"])
        else:
            render_assistant(msg["content"])

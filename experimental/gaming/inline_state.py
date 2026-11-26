"""Reach an already enabled local Steam CEF after verifying its owner and page.

sample() only reads structural keyboard and editor metadata. A confirmed left-pad
Space may request focus on the same Steam search input through search_space.
The caller separately verifies the foreground window.
"""
import asyncio
from dataclasses import asdict
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from focus import ERRORS, ROOT, _known_skin, _props, _trusted


_ENDPOINT = "http://127.0.0.1:8080"
_LOCAL = "0100007F:1F90"
_PAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")

# No names, labels, surrounding text, field contents or URL strings leave the page.
_OBSERVE = r"""(() => {
    const origin = 'https://steamloopback.host';
    if (self.top !== self || self.origin !== origin || location.origin !== origin ||
        new URL(document.baseURI).origin !== origin ||
        !document.hasFocus() || document.visibilityState !== 'visible') return null;
    const skin = SKIN;
    const present = e => {
        const r = e.getBoundingClientRect();
        return e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
               r.width > 2 && r.height > 2 && r.bottom > 0 && r.top < innerHeight &&
               r.right > 0 && r.left < innerWidth;
    };
    const visible = selector => Array.from(document.querySelectorAll(selector)).filter(present);
    const cls = name => '.' + CSS.escape(name);
    const safe = ['other'];
    if (visible('dialog,[role="dialog"],[aria-modal="true"],input[type="password"],' +
                [skin.modal,skin.standalone,skin.buffer].map(cls).join(',')).length) return safe;
    const field = document.activeElement;
    // The empty search input fades over an opaque backdrop even while it
    // owns pointer hit testing and keyboard focus.
    const visibleField = e => {
        if (present(e)) return true;
        if (!e.matches('input[type="search"]') || !e.classList.contains(skin.search) ||
            !e.checkVisibility({checkVisibilityCSS: true})) return false;
        const r = e.getBoundingClientRect();
        return r.width > 2 && r.height > 2 &&
               document.elementFromPoint(r.x + r.width/2, r.y + r.height/2) === e;
    };
    if (!field || !visibleField(field) || field.closest('[inert],[aria-hidden="true"]') ||
        field.disabled || field.readOnly || field.matches('[aria-disabled="true"],[aria-readonly="true"]') ||
        /password|one-time-code|cc-/.test(field.autocomplete || '')) return safe;
    let kind;
    if (field.localName === 'input' && ['text','search','email','url','tel'].includes(field.type))
        kind = field.type;
    else if (field.localName === 'textarea') kind = 'textarea';
    else if (field.isContentEditable && field !== document.body) kind = 'contenteditable';
    else return safe;
    if (field.form && field.form.querySelector('input[type="password"]')) return safe;
    const keyboards = visible(cls(skin.keyboard));
    const wrappers = visible(cls(skin.positioner));
    const inline = keyboards.length === 1 && wrappers.length === 1 &&
                   wrappers[0].contains(keyboards[0]) && !wrappers[0].contains(field);
    if (!inline && (keyboards.length || wrappers.length)) return safe;
    const path = [];
    for (let n = field; n !== document.documentElement; n = n.parentElement) {
        if (!n || !n.parentElement || path.length >= 48) return safe;
        path.push(Array.prototype.indexOf.call(n.parentElement.children, n));
    }
    return [inline ? 'inline' : 'editor', Math.round(performance.timeOrigin), kind, path];
})()"""


@lru_cache(maxsize=1)
def _expression(skin):
    return _OBSERVE.replace("SKIN", json.dumps(asdict(skin), separators=(",", ":")))


def _listener(pid):
    """Return the inode for this exact Steam process's loopback-only CEF listener."""
    listeners = [row.split() for row in Path("/proc/net/tcp").read_text().splitlines()[1:]]
    hits = [row[9] for row in listeners if len(row) >= 10 and row[1] == _LOCAL and
            row[3] == "0A" and row[7] == str(os.getuid())]
    if len(hits) != 1:
        return None
    expected = "socket:[" + hits[0] + "]"
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            if os.readlink(fd) == expected:
                return hits[0]
        except FileNotFoundError:
            continue
    return None


def _page_id(page):
    if not isinstance(page, dict) or page.get("type") != "page":
        return None
    identifier = page.get("id", "")
    if not isinstance(identifier, str) or not _PAGE_ID.fullmatch(identifier):
        return None
    url = page.get("url")
    if not isinstance(url, str):
        return None
    url = urlsplit(url)
    if not ((url.scheme == "https" and url.netloc == "steamloopback.host") or
            (url.scheme == "about" and not url.netloc and url.path == "blank")):
        return None
    expected = f"ws://127.0.0.1:8080/devtools/page/{identifier}"
    return identifier if page.get("webSocketDebuggerUrl") == expected else None


def _result(identifier, data):
    if data == ["other"]:
        return "other", None
    if not isinstance(data, list) or len(data) != 4:
        return None
    state, epoch, kind, path = data
    if (state not in ("inline", "editor") or type(epoch) is not int or not 0 < epoch < 2**53 or
            kind not in ("text", "search", "email", "url", "tel", "textarea", "contenteditable") or
            not isinstance(path, list) or not 0 < len(path) <= 48 or
            any(type(n) is not int or not 0 <= n <= 65535 for n in path)):
        return None
    return state, (identifier, epoch, kind, tuple(path))


async def _observe(skin, *, page_id=None, expression=None):
    import aiohttp

    if expression is None:
        expression = _expression(skin)

    async def no_redirect(*args):
        raise ValueError("Unexpected redirect from local Steam")
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(no_redirect)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=.6), trace_configs=[trace],
                                     trust_env=False) as client:
        async with client.get(_ENDPOINT + "/json/list", allow_redirects=False) as response:
            if response.status != 200:
                return None
            # Discovery metadata can include other webviews. Discard everything
            # except validated local page IDs, and bound untrusted response sizes.
            payload = bytearray()
            async for block in response.content.iter_chunked(8192):
                payload.extend(block)
                if len(payload) > 256 * 1024:
                    return None
            pages = json.loads(payload)
        if not isinstance(pages, list) or len(pages) > 128:
            return None
        ids = list(filter(None, (_page_id(page) for page in pages)))
        if not ids or len(ids) > 16:
            return None
        if page_id is not None:
            if ids.count(page_id) != 1:
                return None
            ids = [page_id]

        async def probe(identifier):
            ws_url = f"ws://127.0.0.1:8080/devtools/page/{identifier}"
            async with client.ws_connect(ws_url, max_msg_size=16384) as ws:
                await ws.send_json({"id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": expression, "returnByValue": True, "silent": True,
                    "timeout": 125,
                }})
                msg = await ws.receive_json()
                if msg.get("id") != 1:
                    return None
                value = msg.get("result", {}).get("result", {}).get("value")
                if page_id is not None:
                    return value if value in ("focused", "restored", "blocked") else None
                return _result(identifier, value)

        results = await asyncio.gather(*(probe(i) for i in ids), return_exceptions=True)
        candidates = [result for result in results if isinstance(result, str if page_id is not None else tuple)]
        return candidates[0] if len(candidates) == 1 else None


def verified_request(xid, operation):
    """Contact only the enabled loopback CEF endpoint owned by this Steam window."""
    try:
        if type(xid) is not int or not 0 < xid <= 0xffffffff:
            return None
        marker = (ROOT / ".cef-enable-remote-debugging").lstat()
        if not stat.S_ISREG(marker.st_mode) or marker.st_uid != os.getuid():
            return None
        pid = int(_props(["-id", hex(xid)], ["_NET_WM_PID"])["_NET_WM_PID"], 0)
        if not _trusted(pid):
            return None
        listener = _listener(pid)
        skin = _known_skin()
        if not listener or skin is None:
            return None
        # aiohttp remains optional on systems without a local Steam observer.
        import aiohttp
        try:
            result = asyncio.run(asyncio.wait_for(operation(skin), .7))
        except aiohttp.ClientError:
            return None
        return result if result and _listener(pid) == listener else None
    except (*ERRORS, ImportError, TypeError, RuntimeError):
        return None


def sample(xid):
    """Return (state, editor identity), ('other', None), or None for unavailable evidence."""
    return verified_request(xid, _observe)

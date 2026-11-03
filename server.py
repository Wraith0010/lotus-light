import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import ssl
import time
from pathlib import Path

import aiohttp
import certifi
from aiohttp import WSMsgType, web

from games import PALETTE, Arcade
from light import Light, capabilities, find_model, model_key

ROOT = Path(__file__).parent
HOME = Path(os.environ.get("LOTUS_LIGHT_HOME") or Path(os.environ.get("APPDATA") or Path.home()) / "lotus-light")
CONFIG = HOME / "config.json"
STATE = HOME / "state.json"
LINK = HOME / "link.txt"

DEFAULT_CONFIG = {
    "pin": "",
    "port": 8770,
    "host": "127.0.0.1",
    "relay": "wss://lotus-light.solar-cloud.workers.dev/agent",
    "relay_key": "",
    "address": "",
    "name": "",
    "model": "",
    "idle_disconnect": 30,
}
DEFAULT_STATE = {
    "on": True,
    "mode": "color",
    "color": [255, 0, 0],
    "brightness": 100,
    "effect": "",
    "speed": 50,
}


def load_json(path, default):
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(default)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config():
    saved = {}
    if CONFIG.exists():
        try:
            saved = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            raise SystemExit(f"{CONFIG} is broken (not valid JSON). Fix the typo and start again.")
    cfg = {**DEFAULT_CONFIG, **saved}
    if not CONFIG.exists():
        cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
    if not cfg["relay_key"]:
        cfg["relay_key"] = secrets.token_urlsafe(32)
    if cfg != saved:
        save_json(CONFIG, cfg)
    return cfg


def clamp(value, lo, hi):
    return max(lo, min(hi, int(value)))


class Hub:
    def __init__(self, cfg, fake):
        self.cfg = cfg
        self.loop = asyncio.get_running_loop()
        saved = load_json(STATE, DEFAULT_STATE)
        self.model_key = saved.get("model", "")
        self.state = {k: saved[k] for k in DEFAULT_STATE}
        self.sessions = set()
        self.fails = {}
        self.save_handle = None
        self.light = Light(cfg, fake=fake, on_change=self.light_changed)
        if self.model_key and not self.light.active_model:
            self.light.known_model = find_model(self.model_key)
        self.arcade = Arcade(self, saved)
        self.arcade_pending = False

    def snapshot(self):
        light = self.light
        return {
            "t": "state",
            "state": self.state,
            "caps": capabilities(light.active_model),
            "link": {"status": light.status, "message": light.message, "name": light.device_name},
            "viewers": len(self.sessions),
        }

    async def broadcast(self, msg=None):
        msg = msg or self.snapshot()
        for session in list(self.sessions):
            try:
                await session.send(msg)
            except Exception:
                self.sessions.discard(session)

    def arcade_changed(self):
        if not self.arcade_pending:
            self.arcade_pending = True
            self.loop.call_later(0.06, lambda: asyncio.ensure_future(self.send_arcade()))

    async def send_arcade(self):
        self.arcade_pending = False
        await self.broadcast(self.arcade.view())

    def light_changed(self):
        self.loop.call_soon_threadsafe(self._light_changed)

    def _light_changed(self):
        m = self.light.active_model
        if m and model_key(m) != self.model_key:
            self.model_key = model_key(m)
            self.save_soon()
        asyncio.ensure_future(self.broadcast())

    def update(self, data):
        s = dict(self.state)
        effects = capabilities(self.light.active_model)["effects"]
        if "on" in data:
            s["on"] = bool(data["on"])
        if "color" in data and isinstance(data["color"], list) and len(data["color"]) == 3:
            s.update(color=[clamp(c, 0, 255) for c in data["color"]], mode="color", on=True)
        if "brightness" in data:
            s.update(brightness=clamp(data["brightness"], 1, 100), on=True)
        if "effect" in data and data["effect"] in effects:
            s.update(effect=data["effect"], mode="effect", on=True)
        if "speed" in data:
            s["speed"] = clamp(data["speed"], 0, 100)
        if s["mode"] == "effect" and s["effect"] not in effects:
            s["mode"] = "color"
        self.state = s
        if not self.arcade.busy:
            self.light.request(s)
        self.save_soon()

    def save_soon(self):
        if self.save_handle:
            self.save_handle.cancel()
        self.save_handle = self.loop.call_later(1, self.save)

    def save(self):
        self.save_handle = None
        save_json(STATE, {**self.state, "model": self.model_key, "scores": self.arcade.scores, "wins": self.arcade.wins})

    def check_pin(self, ip, pin):
        now = time.monotonic()
        fails = [t for t in self.fails.get(ip, []) if now - t < 600]
        if len(fails) >= 8:
            return "locked"
        if secrets.compare_digest(str(pin).encode(), str(self.cfg["pin"]).encode()):
            self.fails.pop(ip, None)
            return "ok"
        self.fails[ip] = fails + [now]
        return "bad"


class Session:
    def __init__(self, hub, send, ip):
        self.hub = hub
        self.send = send
        self.ip = ip or "?"
        self.authed = False
        self.id = secrets.token_hex(4)
        self.name = "Guest"
        self.color = list(PALETTE)[len(hub.sessions) % len(PALETTE)]

    def public(self):
        return {"id": self.id, "name": self.name, "color": self.color}

    async def start(self):
        if self.hub.cfg["pin"]:
            await self.send({"t": "need_pin"})
        else:
            await self.join()

    async def join(self):
        self.authed = True
        self.hub.sessions.add(self)
        await self.send({"t": "you", "id": self.id})
        await self.hub.broadcast()
        self.hub.arcade_changed()

    async def receive(self, raw):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if not self.authed:
            if data.get("t") != "auth":
                return
            result = self.hub.check_pin(self.ip, data.get("pin", ""))
            if result == "ok":
                await self.send({"t": "auth", "ok": True})
                await self.join()
            else:
                text = "Too many wrong tries. Wait 10 minutes." if result == "locked" else "Wrong PIN."
                await self.send({"t": "auth", "ok": False, "msg": text})
            return
        kind = data.get("t")
        if kind == "set":
            try:
                self.hub.update(data)
            except (TypeError, ValueError):
                pass
            await self.hub.broadcast()
        elif kind == "hello":
            name = "".join(ch for ch in str(data.get("name") or "") if ch.isprintable()).strip()[:16]
            self.name = name or self.name
            if data.get("color") in PALETTE:
                self.color = data["color"]
            self.hub.arcade_changed()
        elif kind == "g":
            self.hub.arcade.handle(self, data)

    async def close(self):
        if self in self.hub.sessions:
            self.hub.arcade.left(self)
            self.hub.sessions.discard(self)
            await self.hub.broadcast()
            self.hub.arcade_changed()


async def index(request):
    return web.FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


async def socket(request):
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4096)
    await ws.prepare(request)
    session = Session(request.app["hub"], ws.send_json, request.headers.get("CF-Connecting-IP") or request.remote)
    await session.start()
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await session.receive(msg.data)
    finally:
        await session.close()
    return ws


def page_url(relay_url):
    return re.sub(r"^ws", "http", relay_url).rsplit("/agent", 1)[0]


def banner(lines):
    print("\n" + "=" * 60)
    for line in lines:
        print(f"  {line}")
    print("=" * 60 + "\n")


async def relay(hub, url, key):
    ctx = ssl.create_default_context(cafile=certifi.where()) if url.startswith("wss://") else None
    sessions = {}
    first = True
    delay = 2

    def sender(ws, cid):
        async def send(data):
            await ws.send_str(json.dumps({"t": "send", "id": cid, "data": data}))
        return send

    async def keepalive(ws, seen):
        while not ws.closed:
            await asyncio.sleep(25)
            if time.monotonic() - seen["pong"] > 80:
                await ws.close()
                return
            await ws.send_str("ping")

    while True:
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url, headers={"Authorization": f"Bearer {key}"}, ssl=ctx, max_msg_size=65536) as ws:
                    delay = 2
                    if first:
                        LINK.write_text(page_url(url) + "\n", encoding="utf-8")
                        banner([
                            "Online. Send your friend this link and PIN:",
                            "",
                            page_url(url),
                            f"PIN: {hub.cfg['pin'] or '(none)'}",
                            "",
                            "The link never changes.",
                        ])
                        first = False
                    else:
                        print("Back online")
                    seen = {"pong": time.monotonic()}
                    pinger = asyncio.create_task(keepalive(ws, seen))
                    try:
                        async for msg in ws:
                            if msg.type != WSMsgType.TEXT:
                                continue
                            if msg.data == "pong":
                                seen["pong"] = time.monotonic()
                                continue
                            data = json.loads(msg.data)
                            cid = data.get("id")
                            if data.get("t") == "open" and cid:
                                sessions[cid] = Session(hub, sender(ws, cid), data.get("ip"))
                                await sessions[cid].start()
                            elif data.get("t") == "msg" and cid in sessions:
                                await sessions[cid].receive(data.get("data"))
                            elif data.get("t") == "close" and cid in sessions:
                                await sessions.pop(cid).close()
                    finally:
                        pinger.cancel()
        except aiohttp.WSServerHandshakeError as e:
            if e.status == 403:
                print("The link is locked to a different laptop install (its key is in another config.json).")
                print(f"Copy that config.json to {CONFIG}, or reset the relay (see README).")
                delay = 300
            else:
                print(f"Relay said no ({e.status}), retrying")
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as e:
            print(f"Can't reach the relay ({e.__class__.__name__}), retrying in {delay}s")
        for session in sessions.values():
            await session.close()
        sessions.clear()
        await asyncio.sleep(delay)
        if delay < 60:
            delay *= 2


def find_cloudflared():
    options = [
        shutil.which("cloudflared"),
        ROOT / "cloudflared.exe",
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ]
    return next((str(p) for p in options if p and Path(p).exists()), None)


async def tunnel(port):
    exe = find_cloudflared()
    if not exe:
        print("cloudflared is not installed: winget install --id Cloudflare.cloudflared")
        return
    while True:
        proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            async for line in proc.stderr:
                found = re.search(rb"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if found:
                    banner(["Temporary tunnel link (changes on restart):", "", found.group().decode()])
            await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
        print("Tunnel closed, reopening in 5 seconds")
        await asyncio.sleep(5)


async def make_app(cfg, fake, use_tunnel):
    hub = Hub(cfg, fake)
    app = web.Application()
    app["hub"] = hub
    app.router.add_get("/", index)
    app.router.add_get("/ws", socket)

    async def start(app):
        app["tasks"] = [asyncio.create_task(hub.light.run())]
        if cfg["relay"]:
            app["tasks"].append(asyncio.create_task(relay(hub, cfg["relay"], cfg["relay_key"])))
        if use_tunnel:
            app["tasks"].append(asyncio.create_task(tunnel(cfg["port"])))

    async def stop(app):
        hub.arcade.stop()
        for task in app["tasks"]:
            task.cancel()
        await asyncio.gather(*app["tasks"], return_exceptions=True)
        await hub.light.drop()
        hub.save()

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake", action="store_true", help="no Bluetooth, print the bytes instead")
    parser.add_argument("--relay", help="relay agent URL, or 'off'")
    parser.add_argument("--tunnel", action="store_true", help="also open a temporary cloudflared link")
    args = parser.parse_args()
    cfg = load_config()
    if args.relay:
        cfg["relay"] = "" if args.relay == "off" else args.relay
    port = int(cfg["port"])
    print(f"Light control running on http://127.0.0.1:{port}")
    print(f"PIN: {cfg['pin'] or '(none)'}   (settings: {CONFIG})")
    if args.fake:
        print("Fake mode: nothing is sent over Bluetooth")
    if cfg["relay"]:
        print("Connecting to the relay...")
    web.run_app(make_app(cfg, args.fake, args.tunnel), host=cfg["host"], port=port, print=None)


if __name__ == "__main__":
    main()

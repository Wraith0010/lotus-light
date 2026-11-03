import asyncio
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
MODELS = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
EFFECTS = json.loads((ROOT / "definitions.json").read_text(encoding="utf-8"))["effects_definitions"]

DEFAULT_BY_UUID = {
    "0000fff3-0000-1000-8000-00805f9b34fb": "ELK-BLEDOM",
    "0000ffe1-0000-1000-8000-00805f9b34fb": "LEDBLE",
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e": "DMRRBA-007",
}
LIGHT_SERVICES = {
    "0000fff0-0000-1000-8000-00805f9b34fb",
    "0000ffe0-0000-1000-8000-00805f9b34fb",
}
WRITE_GAP = {"ELK-BLEDOB": 0.1, "ELK-BLEDDM": 0.3}


def model_key(m):
    return f"{m['name']}#{m['handle']}" if m.get("handle") is not None else m["name"]


def find_model(key):
    for m in MODELS:
        if model_key(m).lower() == key.lower():
            return m
    return None


def models_for_name(name):
    n = (name or "").strip().lower()
    found = [m for m in MODELS if n.startswith(m["name"].lower())]
    if not found:
        return []
    longest = max(len(m["name"]) for m in found)
    return [m for m in found if len(m["name"]) == longest]


def pick_model(name, handle=None, uuid=None):
    found = models_for_name(name)
    for m in found:
        if m.get("handle") is not None and m["handle"] == handle:
            return m
    for m in found:
        if m.get("handle") is None:
            return m
    if found:
        return found[0]
    fallback = DEFAULT_BY_UUID.get((uuid or "").lower())
    return next((m for m in MODELS if m["name"] == fallback and m.get("handle") is None), None)


def looks_like_light(device, adv):
    name = adv.local_name or device.name or ""
    if models_for_name(name):
        return True
    return bool(LIGHT_SERVICES & {u.lower() for u in adv.service_uuids})


def build(model, kind, **values):
    cmd = model["commands"].get(kind)
    if not cmd:
        return None
    return bytes(int(values[x] if isinstance(x, str) else x) & 0xFF for x in cmd)


def effects_for(model):
    if not model or not model["commands"].get("effect"):
        return {}
    return EFFECTS.get(model.get("effects_class", "EFFECTS"), {})


def capabilities(model):
    cmds = model["commands"] if model else {}
    effects = effects_for(model)
    return {
        "model": model_key(model) if model else "",
        "color": bool(cmds.get("color")) if model else True,
        "effects": list(effects),
        "speed": bool(cmds.get("effect_speed")) and bool(effects),
    }


def scale(rgb, brightness):
    return [round(c * brightness / 100) for c in rgb]


class LightError(Exception):
    pass


class Light:
    def __init__(self, cfg, fake=False, on_change=None):
        self.address = cfg.get("address", "").strip()
        self.name_hint = cfg.get("name", "").strip().lower()
        self.forced = find_model(cfg["model"]) if cfg.get("model") else None
        self.idle = float(cfg.get("idle_disconnect", 30))
        self.fake = fake
        self.on_change = on_change or (lambda: None)
        self.client = None
        self.char = None
        self.model = self.forced or (find_model("ELK-BLEDOM") if fake else None)
        self.known_model = None
        self.device_name = ""
        self.status = "idle"
        self.message = ""
        self.desired = None
        self.applied = None
        self.wake = asyncio.Event()
        self.last_use = 0.0
        self.fake_linked = False
        self.probe = True
        self.give_up_at = 0.0

    def set_status(self, status, message=""):
        if (status, message) != (self.status, self.message):
            self.status, self.message = status, message
            self.on_change()

    def request(self, state):
        self.desired = json.loads(json.dumps(state))
        self.give_up_at = time.monotonic() + 120
        self.wake.set()

    @property
    def active_model(self):
        return self.model or self.known_model

    async def run(self):
        backoff = 3
        while True:
            if self.probe:
                self.probe = False
                try:
                    await self.ensure()
                except Exception as e:
                    self.set_status("error", str(e) or e.__class__.__name__)
                    await self.drop()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            if self.desired is not None and self.desired != self.applied:
                try:
                    await self.ensure()
                    await self.apply()
                    backoff = 3
                except Exception as e:
                    self.set_status("error", str(e) or e.__class__.__name__)
                    await self.drop()
                    if time.monotonic() < self.give_up_at:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        self.wake.set()
                    else:
                        self.desired = self.applied = None
                        backoff = 3
            elif self.connected and self.idle > 0 and time.monotonic() - self.last_use > self.idle:
                await self.drop()
                self.set_status("idle")

    @property
    def connected(self):
        return self.fake_linked or bool(self.client and self.client.is_connected)

    async def ensure(self):
        if self.connected:
            return
        if self.fake:
            self.fake_linked = True
            self.applied = None
            self.last_use = time.monotonic()
            self.set_status("connected")
            return
        from bleak import BleakClient

        self.set_status("searching")
        device = await self.find()
        if not device:
            raise LightError("Light not found. It may be unplugged, out of range, or connected to a phone.")
        self.set_status("connecting")
        client = BleakClient(device, disconnected_callback=self._lost, timeout=20)
        await client.connect()
        try:
            await self._setup(client, device)
        except Exception:
            await client.disconnect()
            raise
        self.client = client
        self.applied = None
        self.set_status("connected")

    async def _setup(self, client, device):
        chars = list(client.services.characteristics.values())
        name = device.name or self.device_name
        wanted = {m["write_uuid"] for m in ([self.forced] if self.forced else models_for_name(name))}
        wanted = wanted or set(DEFAULT_BY_UUID)
        char = next((c for c in chars if c.uuid.lower() in wanted), None)
        if not char:
            raise LightError("Connected, but this light uses a command format this script doesn't know.")
        self.char = char
        self.model = self.forced or pick_model(name, char.handle, char.uuid)
        if not self.model:
            raise LightError(f"Unknown light type '{name}'. Run scan.py test to pick one.")
        self.known_model = self.model
        self.device_name = name
        self.on_change()
        if name.lower().startswith(("melk", "modelx")):
            for packet in (b"\x7e\x07\x83", b"\x7e\x04\x04"):
                await client.write_gatt_char(char, packet, response=False)
                await asyncio.sleep(1)

    async def find(self):
        from bleak import BleakScanner

        if self.address:
            return await BleakScanner.find_device_by_address(self.address, timeout=10)
        found = await BleakScanner.discover(timeout=8, return_adv=True)
        lights = [(d, a) for d, a in found.values() if looks_like_light(d, a)]
        if self.name_hint:
            lights = [(d, a) for d, a in lights if self.name_hint in (a.local_name or d.name or "").lower()]
        if not lights:
            return None
        device, adv = max(lights, key=lambda x: x[1].rssi)
        self.address = device.address
        self.device_name = adv.local_name or device.name or ""
        print(f"Found light {self.device_name!r} at {device.address}")
        return device

    def _lost(self, _client):
        self.client = None
        self.applied = None
        if self.status == "connected":
            self.set_status("idle")

    async def drop(self):
        client, self.client = self.client, None
        self.applied = None
        self.fake_linked = False
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def send(self, kind, **values):
        packet = build(self.model, kind, **values)
        if packet is None:
            return
        self.last_use = time.monotonic()
        if self.fake:
            print(f"  -> {kind:<13} {packet.hex(' ')}")
            return
        await self.client.write_gatt_char(self.char, packet, response=False)
        await asyncio.sleep(WRITE_GAP.get(self.model["name"], 0.05))

    async def apply(self):
        target = self.desired
        prev = self.applied or {}
        cmds = self.model["commands"]
        powering_on = target["on"] and not prev.get("on")
        full = not prev or powering_on

        if full or target["on"] != prev.get("on"):
            await self.send("turn_on" if target["on"] else "turn_off")

        if target["on"]:
            effects = effects_for(self.model)
            use_effect = target["mode"] == "effect" and target["effect"] in effects
            mode_changed = target["mode"] != prev.get("mode")
            if use_effect:
                if full or mode_changed or target["effect"] != prev.get("effect"):
                    await self.send("effect", v=effects[target["effect"]])
                if full or mode_changed or target["effect"] != prev.get("effect") or target["speed"] != prev.get("speed"):
                    lo, hi = self.speed_range()
                    await self.send("effect_speed", v=lo + (hi - lo) * target["speed"] / 100)
            else:
                rgb = target["color"] if cmds.get("brightness") else scale(target["color"], target["brightness"])
                bright_changed = not cmds.get("brightness") and target["brightness"] != prev.get("brightness")
                if full or mode_changed or target["color"] != prev.get("color") or bright_changed:
                    r, g, b = rgb
                    await self.send("color", r=r, g=g, b=b)
            if cmds.get("brightness") and (full or mode_changed or target["brightness"] != prev.get("brightness")):
                await self.send("brightness", i=target["brightness"])

        self.applied = target
        self.last_use = time.monotonic()

    def speed_range(self):
        r = self.model.get("effect_speed_range")
        return (r["min"], r["max"]) if r else (0, 100)

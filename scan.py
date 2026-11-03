import asyncio
import sys

from bleak import BleakClient, BleakScanner

from light import MODELS, build, looks_like_light, model_key, models_for_name
from server import CONFIG, load_config, save_json


async def scan():
    print("Scanning for 10 seconds...")
    print("Close LotusLamp X on your phone first, the light only takes one connection at a time.\n")
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    rows = sorted(found.values(), key=lambda x: -x[1].rssi)
    lights = []
    for device, adv in rows:
        name = adv.local_name or device.name or ""
        if looks_like_light(device, adv):
            lights.append((device, name))
            guess = ", ".join(model_key(m) for m in models_for_name(name)) or "unknown name"
            print(f"  LIGHT  {name or '(no name)':<22} {device.address}  signal {adv.rssi}  type: {guess}")
    if not lights:
        print("No lights found. Other Bluetooth devices nearby:")
        for device, adv in rows[:15]:
            print(f"         {adv.local_name or device.name or '(no name)':<22} {device.address}  signal {adv.rssi}")
    return lights


async def test(address):
    device = await BleakScanner.find_device_by_address(address, timeout=10) if address else None
    name = device.name if device else ""
    if not device:
        lights = await scan()
        if not lights:
            return
        device, name = lights[0]
    print(f"\nConnecting to {name or '(no name)'} at {device.address}")
    async with BleakClient(device, timeout=20) as client:
        chars = {c.uuid.lower(): c for c in client.services.characteristics.values()}
        print("Characteristics:", ", ".join(chars) or "none")
        likely = models_for_name(name)
        tried = set()
        colors = [("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255)), ("RED", (255, 0, 0))]
        for m in likely + [m for m in MODELS if m not in likely]:
            char = chars.get(m["write_uuid"].lower())
            word, (r, g, b) = colors[len(tried) % len(colors)]
            on, color = build(m, "turn_on"), build(m, "color", r=r, g=g, b=b)
            if not char or not on or not color or (on, color) in tried:
                continue
            tried.add((on, color))
            if name.lower().startswith(("melk", "modelx")):
                for packet in (b"\x7e\x07\x83", b"\x7e\x04\x04"):
                    await client.write_gatt_char(char, packet, response=False)
                    await asyncio.sleep(1)
            for packet in (on, color):
                await client.write_gatt_char(char, packet, response=False)
                await asyncio.sleep(0.3)
            answer = await asyncio.to_thread(input, f"Tried {model_key(m)}. Did the light turn {word}? [y/N] ")
            if answer.strip().lower().startswith("y"):
                cfg = load_config()
                cfg.update(address=device.address, model=model_key(m))
                save_json(CONFIG, cfg)
                print(f"Saved {model_key(m)} and the light's address to config.json. Now run start.bat.")
                return
    print("None of the known formats worked. Copy everything above and send it over.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["test"]:
        asyncio.run(test(args[1] if len(args) > 1 else ""))
    else:
        asyncio.run(scan())

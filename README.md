# Light control

Control a Bluetooth LED light (the kind the LotusLamp X app uses) from anywhere with a link and a PIN.
The laptop next to the light talks to it over Bluetooth and connects out to a small Cloudflare relay,
so the link never changes: https://lotus-light.solar-cloud.workers.dev

## Laptop setup

1. Download https://lotus-light.solar-cloud.workers.dev/download and unzip it anywhere.
2. Close LotusLamp X on your phone. The light only takes one connection at a time.
3. Double-click `start.bat`. The first time it installs Python and the libraries it needs.
   If it had to install Python, close the window and run `start.bat` again.
4. When it says Online it shows the link and the PIN. Send both to your friend.

If the page says Connected but the light doesn't react, close `start.bat`, run `find-light.bat`,
type `y` when the light changes color, then run `start.bat` again.

## Things to know

- Settings (PIN, saved light, the laptop's relay key) live in `%APPDATA%\lotus-light\config.json`.
  Change the PIN there and restart `start.bat`.
- To update, download again and unzip over the old folder. Settings stay in `%APPDATA%`, so the link keeps working.
- The laptop must stay awake: Settings > System > Power, sleep Never when plugged in, and
  "When I close the lid" set to Do nothing.
- After 30 seconds without changes the laptop lets go of the light so the phone app works again.
  `idle_disconnect` in the config changes that (0 keeps it connected).
- To start it with Windows, press Win+R, type `shell:startup`, and put a shortcut to `start.bat` there.
- The first laptop that connects owns the link. If that laptop's config is lost, change `ROOM` in
  `relay/wrangler.toml` and run `relay/deploy.bat` to free the link.
- `python server.py --fake` runs everything without Bluetooth and prints the bytes it would send.

## Relay

`relay/` is the Cloudflare Worker behind the link. It serves the page and passes messages between
the page and the laptop. PIN checks happen on the laptop. Deploy it with `relay/deploy.bat`.

The light command table (`models.json`, `definitions.json`) comes from
[elkbledom](https://github.com/dave-code-ruiz/elkbledom), MIT license, see `elkbledom-LICENSE.txt`.

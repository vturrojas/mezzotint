# Mezzotint case

A printable enclosure for a Pimoroni Inky wHAT (red) stacked on a Raspberry Pi 3 A+. Every part comes from `mezzotint_case.py` (build123d), and every dimension is a field in the `P` dataclass. Override any of them with a JSON file:

```bash
python mezzotint_case.py                 # defaults = the measured build below
python mezzotint_case.py my.json         # e.g. {"pi_front_z": 18.9, "win_overlap": 0.7}
python fitcheck.py                       # exact boolean clash check against stand-in hardware
python render.py preview.png && python assembly.py assembly.png
```

Outside size: **96 × 83 × 28 mm**. The keyhole blocks add 4.5 mm behind it.

## Parts

| File | What it is | Print it |
|---|---|---|
| `mezzotint_fitring.stl` | The bezel plus the first 7 mm of wall | **Print this first** (about 20 min). The wHAT should drop in with a hair of play, and the image edges should sit just inside the window's bevel. |
| `mezzotint_body.stl` | Bezel and walls in one piece | Front face down on the bed. No supports. |
| `mezzotint_cover.stl` | Back plate with keyholes | Inside face (the one with the rings) down. No supports. |
| `mezzotint_cradle.stl` | Desk stand, leaning back 15° | Base down. No supports. |

## Clear PETG settings

- **Nozzle** 240 °C, **bed** 80 °C, **part fan** 30–50 %. Dry the spool if it has been open a while; wet PETG strings and clouds.
- **Layer** 0.2 mm, **4 walls**. The 2.4 mm walls then print as solid perimeters, which looks much cleaner in clear than infill showing through.
- **Bottom layers: 11.** The whole 2.2 mm front plate becomes solid. Set the bottom surface pattern to **concentric**, so the bezel's texture follows the frame shape and looks deliberate.
- The **bed surface** sets the look of the bezel. A textured PEI sheet gives an even frosted mat. Smooth PEI or glass gives a glassier front. Use glue stick or a release agent on smooth PEI, because PETG can bond to it hard enough to take chips out.
- **Infill** 40 % gyroid for the cradle; the other parts are almost all perimeters anyway.

## Hardware

- **2 × M3 × 8 countersunk screws** (ISO 10642 / DIN 7991). They cut their own thread into the 2.5 mm pilot holes.
- **4 × self-adhesive foam dots**, 6 mm across and 1.5–2 mm thick (EVA or PE foam). One goes in each ring on the cover. They press the Pi's screw heads lightly, which holds the whole stack against the bezel without loading the display glass.
- **Right-angle micro-USB cable, "left/right angle" type.** Seen from the back, with the Pi's GPIO header at the top, the cable must head **left**, toward the middle of the frame. It runs along the bottom inside the case and leaves through the notch at the bottom-centre of the cover.
- **2 wall screws** (#6 or 3.5–4 mm, with a head under 8 mm) and anchors, if you hang it.
- Reuse the Pi's own screws and standoffs.

## Assembly

1. **Peel the protective film off the display.** Its red pull tab would get pinched by the wall.
2. Plug the right-angle cable into the Pi.
3. Drop the wHAT/Pi stack into the body face-first. Lay the cable along the bottom and out through the cover's notch.
4. Stick the foam dots into the four rings on the cover.
5. Hook the cover's top edge under the lip, swing it closed, and fit the two screws.

To swap the SD card, take the cover off and lift the stack out. The card sits right against the wall so it can't be knocked, which also means you can't remove it with the stack in place.

## Measured dimensions

| | |
|---|---|
| wHAT board | 90 × 77 mm |
| Image area | 84.8 × 63.6 mm (400 × 300 px), 2.6 mm from the left edge and 2.3 mm from the top, scaled from a photo of a print (±0.4 mm). The bezel overlaps the image by 0.5 mm per side (`win_overlap`) to hide that tolerance. |
| Pi 3 A+ | 65 × 57 mm. It sits 4 mm from the wHAT's edge on the SD side and 6 mm from the top. The SD card ends flush with the wHAT's edge. |
| Stack depth | 20 mm from the front of the glass to the back of the Pi. Allow 2.5 mm behind that for solder tails and the SD card. |

If the fit ring shows the window is off, change `active_left` / `active_top` by the error and rebuild. If the cover rocks or the stack rattles, change the foam thickness; nothing needs reprinting.

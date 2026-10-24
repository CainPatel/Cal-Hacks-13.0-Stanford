# Approval key: how to build it

The box on the owner's desk. When a big or unusual payment comes up, the green light
blinks and the payment shows on a web page the device serves itself. Green button = pay it.
Red button = don't. The device signs the answer with a key that never leaves it, so
nothing else, not even our own server, can approve a payment.

## Parts

| Part | How many | Have it? |
|---|---|---|
| ESP32 dev board (rental booth, opens Fri 6 PM; an ESP32-S3 is ideal) | 1 | rent |
| USB data cable that fits the board (and the USB-C adapter for the laptop) | 1 | check |
| Breadboard (no soldering allowed in the venue) | 1 | need |
| Push buttons | 2 | have 1 |
| LEDs, green + red | 2 | have |
| Resistors, 220–330 Ω (one per LED, or the LED burns out) | 2 | need |
| Male-to-male jumper wires | about 6 | check |

## Wiring

The ESP32 pins are labeled on the board (G4, G5, G16, G17, GND or similar).

```
Pin 4  ── green-labeled button ── GND      (APPROVE)
Pin 5  ── red-labeled button   ── GND      (REJECT)
Pin 16 ── resistor ── green LED long leg;  green LED short leg ── GND
Pin 17 ── resistor ── red LED long leg;    red LED short leg   ── GND
```

Buttons have no direction; put one across the middle gap of the breadboard. LEDs do have one:
the long leg goes toward the pin.

## Flash it (Arduino IDE)

1. Plug the board into the laptop.
2. Open `firmware/approval_key/approval_key.ino` in Arduino IDE.
3. Tools > Board > esp32 > pick the board you rented (ESP32S3 Dev Module, or ESP32 Dev Module).
4. Tools > Port > the one that showed up when you plugged it in.
5. Click Upload (the right arrow). If it hangs on "Connecting...", hold the board's BOOT button until it starts.
6. Tools > Serial Monitor, set to 115200 baud. Press the board's RESET (EN) button.

You should see:

```
PUBKEY 04ab12...   <- send this whole line to A, it goes in config/contract.yaml
IP 10.0.0.42       <- the device's address
```

Red light on and nothing printing means it can't join the WiFi.

## Test it

From the repo folder, on a laptop on the same WiFi:

```
uv run firmware/test_device.py 10.0.0.42
```

Open `http://10.0.0.42:8080` in a browser, press green on the device, and the script prints
`APPROVED, signature valid`. Run it again and press red: `REJECTED, signature valid`.

## WiFi gotchas

- The ESP32 only sees 2.4 GHz WiFi.
- Venue WiFi often blocks devices from talking to each other. If the test can't reach the
  device, use a phone hotspot for both laptop and device (iPhone: turn on "Maximize
  Compatibility"), and change `WIFI_SSID` / `WIFI_PASS` at the top of the sketch.
- The server that talks to the device has to run on a laptop on that same network.

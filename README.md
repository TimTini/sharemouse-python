# ShareMouse Python

Control Windows PCs on a LAN with one mouse and keyboard. The server owns the
physical input devices; clients receive input when the pointer crosses a shared
screen edge. Each client chooses right, left, top or bottom (one client per edge).
Movement uses relative pixels; entry and return positions scale to screen height
or width. Each PC currently uses its primary display.

## Requirements

- Windows 10 or newer, standard x64 CPython **3.11–3.14** (not ARM64 or free-threaded).
- Both PCs must be reachable on the LAN. Being connected to the same router is
  insufficient when guest Wi-Fi/client isolation blocks communication.
- Allow inbound TCP **8765** on the server from the intended clients. The app does
  not modify firewall rules, routers, startup settings or power settings.

The CMD launchers create a project-local `.venv` and install pinned packages from
`vendor/packages/`, with SHA256 checks and `--no-index`. They never download Python
or packages. Install Python separately if absent. Keep the bundled wheels with the
source; do not move a virtual environment between computers.

## Setup without typing commands or copying certificates

1. On the server, double-click **configure.cmd**, select **1 (server)** and save.
2. Double-click **run_sharemouse.cmd**, select **1 (Run ShareMouse)**. A new server
   displays an eight-digit pairing PIN for 120 seconds. Leave that window open.
3. On the client, double-click **configure.cmd**, select **2 (client)**, select the
   server from the LAN scan or enter its local IP, then choose its screen position.
4. Double-click **run_sharemouse.cmd**, select **1**, and enter the PIN displayed on
   the server. Public certificates are authenticated and saved automatically.
   Input control starts after pairing; subsequent runs use saved mutual TLS trust.

Start the server first. Keep the Run windows open; Ctrl+C stops the program.
The menus also display this computer's saved role and configuration. Automatic
scan covers the preferred physical IPv4 LAN with at most 256 addresses; manual
local IP entry is available. Unauthenticated discovery labels do not prove identity.

For an additional client, close the server's existing Run window, reopen the menu
and choose **4 (Pair new device)**. Configure the new client and use its Run menu to
enter the new PIN; on a client with existing trust, choose **4** explicitly. After
pairing, select Run on the server. Previously paired clients can reconnect.

Keep each PC's own `tls/` directory when updating. Never copy `key.pem` to another
PC. If a connection is lost at the very last confirmation, one side can retain
trust while the other rolls back; reopen pairing explicitly on both sides to
repair it. To revoke a peer, stop ShareMouse, remove its fingerprint-named public
certificate from `tls/peers/`, then restart. Remove trust on both PCs for full removal.

## Network and security

The application connects only to literal loopback/private/link-local IP addresses
(`localhost` maps to loopback). There is no telemetry, update check, cloud relay,
Internet service, clipboard synchronization or general file-transfer feature.
Runtime dependencies are bundled; their source URLs are provenance, not startup
requests. Operating-system/background traffic is outside this application's scope.

Normal input uses TLS with certificates trusted on both computers. Each computer
creates its own private key locally. A fresh pairing window temporarily uses
plain WebSocket on the same port, accepting only discovery, SPAKE2 confirmation
and authenticated **public** certificates; it cannot dispatch mouse/keyboard input.
Public certificates are visible on the LAN during pairing. Private keys and the
PIN are never sent. The random PIN expires after 120 seconds, at most five
attempts, or one successful pairing. Use the PIN shown on the intended server.

Pairing uses upstream `spake2` for PAKE and `cryptography` for HKDF. The upstream
Python SPAKE2 implementation is **not constant-time**; this integration has not
received an independent cryptographic audit. This is not an ISMS compliance
certification. Your organization's rules must permit the application's LAN input
and public-certificate exchange. Bundled package provenance and licenses are in
[vendor/README.md](vendor/README.md).

## Recovery and responsiveness

Press **Ctrl+Alt+Esc on the server keyboard** to reclaim input locally. Ordinary
Escape still reaches the client. Display-off/suspend notifications make a client
unavailable; a missing heartbeat response also returns ownership (0.5-second
probes, 2-second deadline, normally about 2.5 seconds including probe scheduling).
OS scheduling can delay these callbacks. No sleep/display policy is changed.

The server sends queued motion as soon as it can. Adjacent movements already
waiting in the queue can share one message, so the send rate follows input when
the connection keeps up. The client preserves intermediate edge calculations
and places its cursor once per batch. Clicks, scrolling and keyboard events
remain ordered.

If the remote cursor stutters, look for red `[MOUSE]` warnings in the client
console. They report a pause followed by a jump during recent movement, a large
incoming step, many steps applied in one jump, or slow local cursor processing.
The receive gap is measured on the client and can include pauses or scheduling
on either PC as well as network delays.

On the server, compare the time of a `[NET]` warning with the client warning.
`tcp_rtt` is Windows' estimate for the actual ShareMouse TCP connection.
`heartbeat` includes time spent in the applications on both PCs.
`retrans` is the increase in server bytes retransmitted since the last sample
(normally about 0.5 seconds), or since connection start on the first sample.
It counts bytes rather than a packet loss percentage. Samples are printed
when the client connects, about every 10 seconds, and on high latency or new
retransmissions.

## Checks and source layout

Double-click **test.cmd** to run checks without starting input hooks. Developers
can use `python -m tests`; `python -m tests --hardware` additionally reads the
actual primary screen/pointer state without injecting input or changing displays.

The resolution matrix includes 1,536 geometry checks, 256 handler round trips
with fake devices, and 24 transfers in three mixed four-client layouts, covering
high-to-low, low-to-high, portrait and ultrawide screens on all four edges.
Pairing checks cover fresh discovery, wrong PIN, tampering, replay, expiry,
attempt limits, interrupted confirmation, and actual loopback mTLS after pairing.
These do not replace physical two-PC tests for sleep, DPI, typing and perceived
smoothness; those remain to be verified on the user's hardware.

- `configure.cmd`, `configure.py`: configuration and LAN scan.
- `run_sharemouse.cmd`, `run_sharemouse.py`: offline setup, pairing and saved-role runner.
- `pairing.py`: one-use PIN bootstrap; `link.py`: LAN WebSocket and TLS transport.
- `server.py`, `client.py`: input ownership and remote event execution.
- `devices.py`, `power.py`, `utils.py`: Windows input, power notifications and geometry.
- `tests/`: regression checks; `vendor/`: pinned offline runtime wheels.

Use the CMD entrypoints; the old launcher and direct server/client command-line
paths have been removed. Local configuration, TLS keys, caches and logs are ignored
by Git. The project is MIT-licensed; see [LICENSE](LICENSE). Originally based on
[DanielJohanness/sharemouse-python](https://github.com/DanielJohanness/sharemouse-python),
inspired by the commercial [ShareMouse](https://www.sharemouse.com/).

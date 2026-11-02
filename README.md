# ShareMouse-Py - Open Source Mouse Sharing

Open-source Python implementation of ShareMouse functionality - Control multiple PCs with one mouse and keyboard.

## 🎯 Features

- ✅ **Seamless Mouse Control** - Move cursor across multiple PCs like extended monitors
- ✅ **Edge Detection** - Automatic transition when cursor reaches screen edge
- ✅ **Cross-platform** - Works on Windows and macOS
- ✅ **Keyboard Forwarding** - Full keyboard support including special keys
- ✅ **Click & Scroll** - Full mouse button and scroll wheel support
- ✅ **Keyboard Shortcuts** - Ctrl+C, Ctrl+V, and all shortcuts work
- ✅ **Multi-directional** - Support left, right, top, bottom positioning
- ✅ **Auto-reconnect** - Handles network interruptions gracefully
- ✅ **Low Latency** - WebSocket-based real-time communication
- ✅ **Free & Open Source** - No licensing fees, fully customizable

## 🆚 ShareMouse vs ShareMouse-Py

| Feature | ShareMouse (Commercial) | ShareMouse-Py (This) |
|---------|------------------------|----------------------|
| Mouse & Keyboard Control | ✅ | ✅ |
| Edge Detection | ✅ | ✅ |
| Cross-platform | ✅ | ✅ |
| Special Keys (Ctrl/Alt/Cmd) | ✅ | ✅ |
| Keyboard Shortcuts | ✅ | ✅ |
| Drag & Drop Files | ✅ | 🚧 Planned |
| LAN server scan | ✅ | ✅ |
| Encryption | ✅ | ✅ TLS between paired computers |
| GUI Application | ✅ | 🚧 Planned |
| Price | 💰 $24.95+ | ✅ Free |
| Open Source | ❌ | ✅ |

## 📋 Requirements

- Python 3.11+ (verified here with Python 3.14)
- Windows 10+ or macOS 10.14+
- Network connection (LAN recommended for best performance)

## 🚀 Quick Start

### 1. Install Dependencies

**Windows: double-click without typing commands**

- Double-click **`configure.cmd`** on each PC. Select **1 (server)** on the PC
  with the physical mouse/keyboard, or **2 (client)** on the PC to control.
- For a client, the script scans the preferred physical Windows LAN for running
  ShareMouse servers. Select a numbered result or enter the server's local IP
  manually, then select the screen position with a number.
- Double-click **`run_sharemouse.cmd`**, then select **1** to run. The same menu
  can reconfigure the PC or display saved settings. Keep its window open;
  Ctrl+C stops ShareMouse. Start the server before scanning from a client.

The launchers use the local `.venv`. If it is missing, they use an installed
Python 3.11+ to create it. No packages are downloaded. The program only
connects to loopback, link-local, and private LAN addresses. If Python is
absent, the window displays the official download link; it does not open that
page, and no global Python install or firewall change is performed
automatically. Errors remain visible until a key is pressed. Do not copy a
virtual environment from another PC; copy the source and let the launcher
create one locally.

The LAN scan checks the selected TCP port and ShareMouse discovery response;
it does not activate remote input. Automatic scanning is limited to one preferred
physical IPv4 LAN with at most 256 addresses. Larger networks, blocked discovery
and older servers can use manual IP entry. Discovery labels are not authentication.
Pairing is the certificate in `tls/peers`.

There is nothing to install with pip. `requirements.txt` lists no packages.

### 2. Launch Application

**Saved server/client configuration (recommended):**

Run these commands from this repository on **each computer**, using its own
Python environment. On Windows, use `.\.venv\Scripts\python.exe` in place of
`python` if Python is installed in the repository's `.venv`.

**Server: the computer whose physical mouse and keyboard you use:**

```bash
python configure.py --role server --host 0.0.0.0
python run_sharemouse.py
```

The configuration script displays the machine name, its SERVER role, listening
port and local IPv4 candidates. Choose the server's LAN IP for the clients.
`0.0.0.0` means listen on all IPv4 adapters; use `--host YOUR_LAN_IP` to bind
one adapter. The default port is 8765; `--port NUMBER` changes it on either role.

**Client: the computer being controlled (example server IP 192.168.1.100):**

```bash
python configure.py --role client --host 192.168.1.100 --position right
python run_sharemouse.py
```

`--position` describes this client's screen relative to the server:
`right`, `left`, `top`, or `bottom`. Use one client per edge. Each computer
gets `tls/cert.pem` and `tls/key.pem`. Copy only `cert.pem` into the other
computer's `tls/peers` folder, in both directions, then start the programs.
Leave `key.pem` where it was created. Run the server
first, then the clients. Both sides need their runner open. Move the server
pointer across the configured edge to control that client, and back through
the facing edge to return. Ctrl+C stops the runner.

To inspect the saved role without starting control:

```bash
python configure.py --show
python run_sharemouse.py --check
```

`--check` validates configuration only; it does not verify dependencies,
firewall or a working connection. Run `python -m tests.test_setup` to check the
local installation. Running `python configure.py` without arguments prompts
for role and endpoint. Configuration is saved to ignored `config.json` beside
the scripts; existing configuration is copied into `backup/` before replacement.
Configure each computer separately: a configuration copied from another machine
is rejected. No automatic startup, firewall rule or network discovery is installed.

The server's live log identifies each connecting client by its self-reported
machine name, remote address and edge; the configuration display shows saved
settings, not a live inventory. Machine names are labels, not authentication.

Functional limits: there is no clipboard/file transfer or monitor spanning.
Local input suppression is implemented for Windows;
equivalent macOS behavior has not been verified. Connections use TLS.
Each computer keeps its private key and trusts only certificates copied into
`tls/peers`. Copy `tls/cert.pem` both ways, then restart. Use a trusted LAN as
well. Client-held keys/buttons are
released on return, disconnect and shutdown; this cannot recover after the
client process is forcibly killed or the client machine crashes.

Double-click **`test.cmd`** to run the regression suite without typing commands.
Developer entrypoint: `python -m tests`. Add `--hardware` to also read actual
screen dimensions and pointer position, or run `python -m tests.test_setup`.
Tests use fake input controllers and loopback connections; they do not move the
physical cursor, change resolution, or put a computer to sleep. Full results
stay in ignored `.cache/tests/results.log`.

The resolution suite covers 640×480, 1366×768, 1920×1080, 2560×1440,
3840×2160, 5120×2880, portrait 1080×1920 and ultrawide 3440×1440:
64 ordered server/client pairs across all four edges, 1,536 boundary/round-trip
geometry checks, 256 real-handler round trips with fake devices, and three
mixed four-client layouts (24 transfers). It checks normalized entry/return,
unscaled relative movement, batching/clamping, no immediate re-entry and
isolation of clicks/scroll/movement to the selected client. These are simulated
resolution tests, not physical multi-monitor or per-monitor DPI validation.
Before relying on a pair of PCs, verify movement and return across the shared
edge, left/right click, drag, scroll, typing, modifier shortcuts and disconnect
while holding a key. These physical two-computer checks have not been run here.

**Legacy interactive launcher (does not read the saved configuration):**
```bash
python launcher.py
```

The launcher provides an interactive menu:
- Start as Controller (primary PC with mouse/keyboard)
- Start as Controlled (secondary PC to be controlled)
- Test Setup
- Show Network Info

**Manual Way:**

**Primary PC (Controller):**
```bash
python server.py
```

**Secondary PC (Controlled):**
```bash
python client.py wss://192.168.1.100:8765 right
```

### 3. Setup Layout

Configure the physical position of your secondary PC relative to primary:
- `right` - Secondary PC is to the RIGHT of primary
- `left` - Secondary PC is to the LEFT of primary
- `top` - Secondary PC is ABOVE primary
- `bottom` - Secondary PC is BELOW primary

### 4. Use It!

1. Move your mouse to the edge of primary screen
2. Cursor seamlessly appears on secondary screen
3. Control secondary PC with your primary mouse/keyboard
4. Move back to return control to primary PC

## 📁 Project Structure

```
sharemouse-py/
├── configure.cmd          # Double-click configuration
├── run_sharemouse.cmd     # Double-click runner / shared Python bootstrap
├── test.cmd               # Double-click regression suite
├── configure.py           # Configuration and LAN scan
├── run_sharemouse.py       # Saved-role runner
├── server.py              # Controller - primary PC
├── client.py              # Controlled - secondary PC
├── devices.py             # Windows input
├── power.py               # Windows power notifications
├── link.py                # LAN WebSocket/TLS transport
├── utils.py               # Handoff geometry and key encoding
├── launcher.py            # Legacy interactive launcher
├── tests/                 # All regression checks and setup smoke
│   ├── __main__.py         # Suite runner
│   ├── test_resolutions.py # Resolution matrix / mixed clients
│   └── test_*.py           # Input, recovery, config, CMD, TLS, setup
├── requirements.txt
└── README.md
```

## ⚙️ Configuration

### Server (Primary PC)

The secondary PC picks the side (`left`, `right`, `top`, or `bottom`). One machine per edge. The primary listens on every edge that has a client.

```python
server = MouseServer(
    host='0.0.0.0',        # Listen on all interfaces
    port=8765,             # WebSocket port
    edge_threshold=5       # Pixels from edge to trigger (default: 5)
)
```

### Client (Secondary PC)

```bash
# Syntax
python client.py [SERVER_URI] [POSITION]

# Examples
python client.py wss://192.168.1.100:8765 right
python client.py wss://192.168.1.100:8765 left
python client.py wss://localhost:8765 top
```

## 🔧 Advanced Usage

### Find Your IP Address

**Windows:**
```cmd
ipconfig
```

**macOS/Linux:**
```bash
ifconfig
# or
ip addr show
```

### Test Local Setup (Single PC)

**Terminal 1:**
```bash
python server.py
```

**Terminal 2:**
```bash
python client.py wss://localhost:8765 right
```

### Multiple Secondary PCs

You can connect multiple secondary PCs to one primary:

**Primary PC:**
```bash
python server.py
```

**Secondary PC 1 (Right):**
```bash
python client.py wss://192.168.1.100:8765 right
```

**Secondary PC 2 (Left):**
```bash
python client.py wss://192.168.1.100:8765 left
```

## 🐛 Troubleshooting

### macOS: "Operation not permitted"

Grant Accessibility permissions:
1. System Preferences → Security & Privacy → Privacy → Accessibility
2. Add Terminal or Python to the list
3. Restart terminal

### Windows: Firewall Blocking

- Allow Python through Windows Firewall when prompted
- Or manually: Control Panel → Windows Defender Firewall → Allow an app

### Connection Issues

- Ensure both PCs are on the same network
- Check firewall isn't blocking port 8765
- Verify IP address is correct
- Try disabling VPN if active

### Cursor Stuck at Edge

Coming back onto the primary takes a few pixels of movement past the shared edge, then the pointer is placed just inside that edge so it does not immediately leave again.

Press **Ctrl+Alt+Esc on the server keyboard** to return immediately, even if the
client cannot respond. Ordinary Esc still works in applications on the client.

The client reports Windows display-off/suspend events, releases held input and
becomes unavailable until its display/session resumes. The server probes each
connected client every 0.5 seconds; a missing application response has a 2-second
deadline, so a half-open connection normally returns control within about
2.5 seconds. No screen or sleep setting is changed. Power events and timers
still depend on the operating system scheduling the application.

Update and restart **both** computers for heartbeat and batched motion support.
Keep their existing `tls/` folders and pairing certificates. An old client
without heartbeat replies is disconnected by the updated server.

### High Latency

The server uses one ordered sender and batches adjacent motion at up to 240
updates/second by default. The client replays each movement step for edge/clamp
math, then updates the OS cursor once per batch; clicks, scroll and keys stay
in order. This avoids a coroutine/TLS write and OS cursor placement for every
mouse polling event without dropping intermediate edge crossings. It is a
transport update target, not a guarantee of display FPS. The constructor's
`move_hz` can be tuned between 30 and 1000; the default does not change mouse
sensitivity.

Run `python -m tests.test_recovery` for fake-device burst, ordering, heartbeat,
power-state, local rescue hotkey and stalled-upgrade regressions. These checks do not
physically suspend a computer or measure perceived mouse smoothness.

- Use wired LAN instead of WiFi
- Close bandwidth-heavy applications
- Check for network congestion
- Reduce distance between PCs

## 🎨 How It Works

### Architecture

```
Primary PC (Controller)          Secondary PC (Controlled)
┌──────────────────┐             ┌──────────────────┐
│                  │             │                  │
│  Mouse/Keyboard  │             │                  │
│       ↓          │             │                  │
│   server.py      │             │   client.py      │
│       ↓          │             │       ↑          │
│  Edge Detection  │             │  Event Executor  │
│       ↓          │             │       ↑          │
│   WebSocket ─────┼─────────────┼→ WebSocket       │
│                  │   Network   │                  │
└──────────────────┘             └──────────────────┘
```

### Edge Detection Flow

```
1. Primary: cursor reaches an edge that has a connected PC
2. Primary: parks its cursor and tells that PC to take over (the cursor is hidden on Windows)
3. Secondary: places the cursor on the facing edge
4. Primary: sends movement deltas
5. Secondary: applies them
6. Secondary: cursor crosses back through the shared edge
7. Primary: shows its cursor just inside that edge
```

### Delta-based Movement

Unlike absolute positioning, we use delta (relative) movement:
- More accurate across different screen resolutions
- Smoother cursor motion
- Prevents "jumping" artifacts
- Mimics real extended monitor behavior

## 🔐 Security Considerations

This application provides full mouse and keyboard control over a paired PC.
The link is TLS. `configure.py` creates `tls/cert.pem` and `tls/key.pem` on
each computer. Copy only `cert.pem` into the other computer's `tls/peers`
folder, then restart both programs. The fingerprint from `configure.py --show`
identifies a certificate. A computer that was not paired cannot connect, and
keystrokes are not readable on the network. The private key stays on the
computer that created it.

## 🛣️ Roadmap

### Planned Features

- [ ] **Drag & Drop** - Transfer files by dragging
- [x] **LAN server scan** - Choose a running server or enter its address manually
- [x] **Encryption** - TLS between paired computers
- [ ] **GUI Application** - Native desktop app
- [ ] **System Tray** - Background operation
- [ ] **Hotkeys** - Quick enable/disable
- [ ] **Multi-monitor** - Support for multiple monitors per PC
- [ ] **Linux Support** - Full Linux compatibility

### Future Improvements

- [ ] Better error handling
- [ ] Connection quality indicator
- [ ] Latency measurement display
- [x] Configuration file support
- [ ] Logging system
- [ ] Performance optimizations

## 📝 Tips & Best Practices

- **Use LAN:** Wired connection provides lowest latency
- **Screen Alignment:** Physically align monitors for intuitive cursor flow
- **Edge Threshold:** Adjust `edge_threshold` if triggering too easily/hard
- **Return Distance:** The pointer must travel 5px past the shared edge before control comes back
- **Testing:** Test locally first before network setup
- **Firewall:** Ensure port 8765 is open on both PCs

## 🤝 Contributing

Contributions are welcome! Areas that need help:
- File drag & drop functionality
- Auto-discovery protocol
- GUI development
- Linux testing and support
- Documentation improvements

## 📄 License

MIT License - Free to use and modify.

## 🙏 Acknowledgments

Inspired by [ShareMouse](https://www.sharemouse.com/) - the commercial solution for mouse/keyboard sharing.

## 📧 Support

- Create an issue for bug reports
- Star the repo if you find it useful
- Share with others who might benefit

---

**ShareMouse-Py** - Because mouse sharing should be free and open source! 🐭✨

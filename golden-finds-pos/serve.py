"""
Production entry point for the shop machine.

Runs a real WSGI server (waitress) rather than Flask's development one,
bound to every interface so that the till machine serves the app to
itself and to any phone or tablet on the same shop wifi. Nothing here
reaches the internet: this is a local server on the local network, which
is what keeps it working when the line is down.

    python serve.py

On the shop laptop it is started hidden by windows/launch.vbs, so there is
no console window to close by accident. In that case output goes to
instance/server.log instead.
"""

import logging
import socket
import sys

HOST = "0.0.0.0"
PORT = 8000


def already_running():
    """True if a till is already answering on this computer."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def local_address():
    """
    The machine's address on the shop network, for the startup message.
    Connects to a UDP address without sending anything, purely to find
    out which interface the OS would route through.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    from app import create_app
    from app.config import INSTANCE_DIR

    hidden = sys.stdout is None  # started with pythonw: no console at all
    if hidden:
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(INSTANCE_DIR / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        logging.basicConfig(
            stream=log, level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if already_running():
        # Double-clicking the shortcut twice must not start a second till.
        print(f"Golden Finds is already running on port {PORT}.")
        return

    from waitress import serve as waitress_serve

    app = create_app()

    print(f"\n  {app.config['SHOP_NAME']} is running.\n")
    print(f"    On this computer:      http://localhost:{PORT}")
    print(f"    On the shop wifi:      http://{local_address()}:{PORT}\n")
    print("  Press Ctrl+C to stop.\n")

    waitress_serve(app, host=HOST, port=PORT, threads=8)


if __name__ == "__main__":
    main()

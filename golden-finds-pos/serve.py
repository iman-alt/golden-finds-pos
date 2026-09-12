"""
Production entry point for the shop machine.

Runs a real WSGI server (waitress) rather than Flask's development one,
bound to every interface so that the till machine serves the app to
itself and to any phone or tablet on the same shop wifi. Nothing here
reaches the internet: this is a local server on the local network, which
is what keeps it working when the line is down.

    python serve.py

To reach it from another device on the same wifi, use the address this
prints on startup.
"""

import socket

from waitress import serve as waitress_serve

from app import create_app

HOST = "0.0.0.0"
PORT = 8000


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
    app = create_app()

    print(f"\n  {app.config['SHOP_NAME']} is running.\n")
    print(f"    On this computer:      http://localhost:{PORT}")
    print(f"    On the shop wifi:      http://{local_address()}:{PORT}\n")
    print("  Press Ctrl+C to stop.\n")

    waitress_serve(app, host=HOST, port=PORT, threads=8)


if __name__ == "__main__":
    main()

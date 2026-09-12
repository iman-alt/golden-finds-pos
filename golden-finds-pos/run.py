"""
Development entry point.

For the shop machine use a real server instead:
    waitress-serve --host=0.0.0.0 --port=8000 "run:app"
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

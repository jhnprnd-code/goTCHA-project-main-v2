"""
Entry point for the packaged goTCHA server (build it with build_exe.bat -> dist/goTCHA.exe).

Does what run_gotcha.bat does: serves the API and the /collect page on 127.0.0.1, then opens
the page in the default browser. sessions.jsonl, consent.jsonl and an optional model.joblib
live next to the .exe. Env vars (GOTCHA_ADMIN_KEY, GOTCHA_ORIGINS, ...) work as documented in
backend/backend_api.py; GOTCHA_PORT changes the port (default 8000).
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("GOTCHA_PORT", "8000"))

# Same default as run_gotcha.bat; an already-set GOTCHA_ADMIN_KEY wins. Must be set before
# backend_api is imported, because it reads the environment at import time.
os.environ.setdefault("GOTCHA_ADMIN_KEY", "120522")
os.environ.setdefault("GOTCHA_ORIGINS", f"http://localhost:{PORT},http://127.0.0.1:{PORT}")

if not getattr(sys, "frozen", False):   # running from source: backend/ is not on the path yet
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))


def _open_browser_when_up() -> None:
    for _ in range(100):
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, PORT)) == 0:
                webbrowser.open(f"http://{HOST}:{PORT}/collect")
                return
        time.sleep(0.2)


def main() -> None:
    import uvicorn
    from backend_api import app

    print(f"goTCHA server: http://{HOST}:{PORT}/collect   (close this window or press Ctrl+C to stop)")
    threading.Thread(target=_open_browser_when_up, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)


if __name__ == "__main__":
    try:
        main()
    except (OSError, SystemExit) as exc:   # e.g. the port is already in use
        print(f"\ngoTCHA could not start: {exc}")
        input("Press Enter to close...")
        raise

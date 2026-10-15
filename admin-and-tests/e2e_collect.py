"""
End-to-end test of the collection flow in a real (headless) Chromium.

    python tests/e2e_collect.py

Starts its own server on port 8100 with temp data files, so it never touches your real
sessions.jsonl. Checks:
  * consent gating and API guards
  * the full UI flow: consent -> about -> practice + 3 timed levels -> participant code
  * the client's hit count agrees with the server's physics replay (no client/server drift)
  * a main-thread stall is detected and the level is flagged for a redo (frame gate)
  * /verify returns "retry" for stuttering sessions, capped at MAX_RETRIES per IP
"""
import importlib
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
from gamecore import obstacle_window  # noqa: E402

PORT = 8100
BASE = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="gotcha_e2e_"))
env = dict(os.environ, GOTCHA_DATA=str(tmp / "sessions.jsonl"), GOTCHA_CONSENT=str(tmp / "consent.jsonl"),
           GOTCHA_ADMIN_KEY="test-admin", GOTCHA_DEBUG="1")
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend_api:app", "--port", str(PORT),
                           "--no-access-log", "--log-level", "warning"], cwd=ROOT, env=env)
results, failures = [], []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not cond:
        failures.append(name)


def wait_up():
    for _ in range(60):
        try:
            requests.get(BASE + "/collect", timeout=1)
            return
        except requests.RequestException:
            time.sleep(0.25)
    raise SystemExit("server did not start")


def api_guards():
    base = {"consent": True, "adult": True, "consent_version": "2026-10-v3",
            "group": "sighted", "keyboard": "laptop", "aids": ["none"]}
    check("start without consent -> 400", requests.post(BASE + "/collect/start", json={**base, "consent": False}).status_code == 400)
    check("start without adult -> 400", requests.post(BASE + "/collect/start", json={**base, "adult": False}).status_code == 400)
    check("old consent version -> 409", requests.post(BASE + "/collect/start", json={**base, "consent_version": "old"}).status_code == 409)
    check("bot label without admin key -> 403", requests.post(BASE + "/collect/start", json={**base, "label": "bot"}).status_code == 403)
    r = requests.post(BASE + "/collect/start", json={**base, "label": "bot", "bot_kind": "t", "admin_key": "test-admin"})
    check("bot label with admin key ok, pid starts with B", r.status_code == 200 and r.json()["pid"].startswith("B"))
    check("unknown pid challenge -> 404", requests.get(BASE + "/collect/challenge?pid=NOPE").status_code == 404)


def verify_retry_cap(out):
    """4 stuttering sessions from one IP: first 3 get 'retry', the 4th stops being excused."""
    def one(i):
        ch = requests.get(BASE + "/challenge").json()
        ev = []
        for s in ch["spawns_ms"]:
            c = obstacle_window(s)[2] + random.gauss(0, 40)
            ev += [{"k": "d", "t": round(c, 1)}, {"k": "u", "t": round(c + random.gauss(90, 20), 1)}]
        ev.sort(key=lambda e: e["t"])
        time.sleep(ch["level_end_ms"] / 1000 + 0.3)
        tele = {"n_frames": 1100, "dt_median_ms": 16.7, "dt_p95_ms": 30, "dt_max_ms": 640, "n_over_50": 4,
                "n_over_100": 3, "timer_res_ms": 0.1, "dpr": 1, "inner_w": 1200, "outer_w": 1200,
                "canvas_css_w": 800, "blur_count": 0, "untrusted_count": 0, "repeat_count": 0, "ua": "test"}
        r = requests.post(BASE + "/verify", json={"nonce": ch["nonce"], "events": ev, "telemetry": tele})
        out[i] = r.json()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(4)]
    for t in ths:
        t.start()
    return ths


def play_level(page, jitter=25.0, stall=False):
    page.wait_for_function("window.__gotcha && window.__gotcha.state === 'GAME'", timeout=15000)
    if stall:  # block the main thread for 350 ms every 1.5 s
        page.evaluate("window.__stall = setInterval(() => { const s = performance.now(); while (performance.now() - s < 350); }, 1500)")
    spawns, = page.evaluate("[window.__gotcha.spawns]")
    for s in spawns:
        target = obstacle_window(s)[2] + random.gauss(0, jitter)
        while page.evaluate("performance.now() - window.__gotcha.t0") < target - 4:
            time.sleep(0.004)
        page.keyboard.down("Space")
        time.sleep(max(0.03, random.gauss(0.09, 0.02)))
        page.keyboard.up("Space")
    page.wait_for_function("window.__gotcha.state === 'RESULT'", timeout=60000)
    if stall:
        page.evaluate("clearInterval(window.__stall)")
    hits = page.evaluate("window.__gotcha.hit.filter(Boolean).length")
    return len(spawns) - hits


def register_via_ui(page):
    page.goto(BASE + "/collect")
    check("final consent version shown", "Consent version 2026-10-v3" in page.locator("#step-consent").inner_text())
    check("draft placeholders removed", page.locator(".todo").count() == 0)
    check("continue disabled before consent", page.is_disabled("#btn-consent"))
    page.check("#chk-adult")
    check("continue still disabled with one box", page.is_disabled("#btn-consent"))
    page.check("#chk-consent")
    page.click("#btn-consent")
    page.click("#btn-about")
    check("about form rejects empty answers", "answer all three" in page.inner_text("#about-err").lower())
    page.check("input[name=group][value=low_vision]")
    page.check("input[name=aids][value=browser_zoom]")
    page.check("input[name=aids][value=none]")
    check("'None' unticks other aids", not page.is_checked("input[name=aids][value=browser_zoom]"))
    page.check("input[name=keyboard][value=laptop]")
    page.click("#btn-about")
    page.wait_for_selector("#gotcha-container button")


def main():
    wait_up()
    api_guards()
    retry_out = {}
    retry_threads = verify_retry_cap(retry_out)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # ---- stage A: full participant flow
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        register_via_ui(page)
        page.click("#gotcha-container button")           # Start practice
        for i in range(4):
            cleared_client = play_level(page)
            last = [json.loads(l) for l in (tmp / "sessions.jsonl").read_text().splitlines()][-1]
            cleared_server = last["features"]["cleared"]
            check(f"level {i}: client hits agree with server replay", cleared_client == cleared_server,
                  f"client {cleared_client} / server {cleared_server}")
            check(f"level {i}: recorded valid, practice={i == 0}", last["valid"] and last["practice"] == (i == 0),
                  f"flags={last['flags']}")
            if i < 3:
                page.wait_for_selector("#gotcha-container button", timeout=5000)
                page.click("#gotcha-container button")
        page.wait_for_selector("#step-done:not([hidden])", timeout=10000)
        pid = page.inner_text("#pid-out")
        check("participant code shown on done page", len(pid) == 7 and pid[0] == "P", pid)
        check("no JS errors in flow", not errors, "; ".join(errors))
        page.close()

        # ---- stage B: stall -> flagged -> redo
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        register_via_ui(page)
        page.click("#gotcha-container button")
        play_level(page, stall=True)
        check("stalled level shows redo message", "do that again" in page.inner_text("#gotcha-container").lower())
        page.click("#gotcha-container button")           # Try again
        cleared = play_level(page)
        check("redo after stall is accepted as practice", "Practice done" in page.inner_text("#gotcha-container"))
        browser.close()

    for t in retry_threads:
        t.join()
    recs = [json.loads(l) for l in (tmp / "sessions.jsonl").read_text().splitlines()]
    collect = [r for r in recs if r["source"] == "collect"]
    stalled = [r for r in collect if any(f.startswith("frame:") for f in r["flags"])]
    check("stalled level flagged frame:* and invalid", len(stalled) >= 1 and not stalled[0]["valid"] and stalled[0]["practice"],
          str(stalled[0]["flags"]) if stalled else "")
    check("records carry spawns, events, telemetry, params, schema 2",
          all(r["schema"] == 2 and r["spawns_ms"] and r["events"] and r["telemetry"] and r["params"] for r in collect))
    t0 = collect[0]["telemetry"]
    check("telemetry looks sane (fps, timer res, dpr)", t0["n_frames"] > 600 and t0["dt_median_ms"] < 40 and t0["dpr"] >= 1,
          f"frames={t0['n_frames']} median_dt={t0['dt_median_ms']} timer_res={t0['timer_res_ms']} dpr={t0['dpr']}")
    out = [retry_out[i] for i in range(4)]
    n_retry = sum(1 for o in out if o.get("retry"))
    check("/verify retry cap: 3 retries then no more excuses", n_retry == 3 and sum(1 for o in out if not o.get("retry") and not o.get("success")) == 1,
          f"retries={n_retry}")
    levels = sorted(collect, key=lambda r: r["ts"])
    print(f"\n{len(collect)} collect records, data in {tmp}")
    print(f"{len(results) - len(failures)}/{len(results)} checks passed")


try:
    main()
finally:
    server.terminate()
sys.exit(1 if failures else 0)

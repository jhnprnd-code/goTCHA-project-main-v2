"""
Unified browser-automation attacker: drives the REAL collection page via Playwright.
Supports headless concurrency or headed visual debugging.

    python bot_playwright.py --kind gauss --runs 4 --levels 3 --admin-key $GOTCHA_ADMIN_KEY
    python bot_playwright.py --kind replay --pool sessions.jsonl --headed
"""
import argparse
import asyncio
import json
import os
import random
import sys
import time

import requests
from playwright.async_api import async_playwright

from bot_lib import KINDS, build_events, gen_offsets, load_human_pool

INTENTS = []

async def sleep_until(target: float):
    delay = target - time.perf_counter()
    if delay > 0.004:
        await asyncio.sleep(delay - 0.003)
    while time.perf_counter() < target:
        pass

async def run_one(browser, idx, args, pool, sem):
    async with sem:
        rng = random.Random(args.seed * 1000 + idx)
        kind = args.label or args.kind
        timing = "gauss" if args.kind == "dom_untrusted" else args.kind
        
        # Fast-path registration via API 
        r = await asyncio.to_thread(
            lambda: requests.post(
                f"{args.base}/collect/start",
                timeout=15,
                json={
                    "consent": True,
                    "adult": True,
                    "consent_version": args.consent_version,
                    "group": "sighted",
                    "keyboard": "other",
                    "aids": ["none"],
                    "label": "bot",
                    "bot_kind": f"pw_{kind}",
                    "admin_key": args.admin_key,
                },
            )
        )
        if r.status_code != 200:
            raise RuntimeError(f"[run {idx}] register failed: {r.status_code} {r.text}")

        pid = r.json()["pid"]
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(f"{args.base}/collect")
        
        # Bypass UI directly to the game widget
        await page.evaluate("(p) => { pid = p; startPlay(); }", pid)
        await page.wait_for_selector("#gotcha-container button")
        await page.click("#gotcha-container button")

        for lvl in range(args.levels + 1):
            await page.wait_for_function("window.__gotcha && window.__gotcha.state === 'GAME'", timeout=20000)
            page_now, t0, spawns = await page.evaluate(
                "[performance.now(), window.__gotcha.t0, window.__gotcha.spawns]")
            
            py_now = time.perf_counter()
            lvl_now = page_now - t0
            events = build_events(spawns, gen_offsets(timing, len(spawns), rng, pool))
            INTENTS.append({"pid": pid, "order": lvl, "events": events})
            
            for ev in events:
                await sleep_until(py_now + (ev["t"] - lvl_now) / 1000.0)
                if args.kind == "dom_untrusted":
                    kind_js = "keydown" if ev["k"] == "d" else "keyup"
                    await page.evaluate(
                        "(k) => window.dispatchEvent(new KeyboardEvent(k, {code: 'Space', key: ' '}))", kind_js)
                else:
                    await (page.keyboard.down("Space") if ev["k"] == "d" else page.keyboard.up("Space"))
                    
            await page.wait_for_function("window.__gotcha.state === 'RESULT'", timeout=60000)
            if lvl < args.levels:
                await page.click("#gotcha-container button")
                
        await ctx.close()
        print(f"[run {idx}] {pid} done")

async def main(args):
    pool = load_human_pool(args.pool) if args.pool else None
    sem = asyncio.Semaphore(args.concurrent)
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        try:
            outcomes = await asyncio.gather(
                *[run_one(browser, i, args, pool, sem) for i in range(args.runs)], 
                return_exceptions=True
            )
            failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
            for failure in failures:
                print(f"Bot run failed: {type(failure).__name__}: {failure}", file=sys.stderr)
            if failures:
                raise SystemExit(f"{len(failures)} of {args.runs} bot runs failed.")
        finally:
            await browser.close()
            if args.intent_out:
                with open(args.intent_out, "a", encoding="utf-8") as f:
                    for row in INTENTS:
                        f.write(json.dumps(row) + "\n")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=KINDS + ["dom_untrusted"], required=True)
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--concurrent", type=int, default=2)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-key", default=os.environ.get("GOTCHA_ADMIN_KEY", ""))
    ap.add_argument("--pool", nargs="*", default=[], help="human sessions.jsonl for fitted/replay")
    ap.add_argument("--label", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--consent-version", default="2026-10-v5")
    ap.add_argument("--intent-out", default="", help="append intended key times here (for analyze_artifacts.py)")
    ap.add_argument("--headed", action="store_true", help="Watch the bot play visually")
    a = ap.parse_args()
    
    if a.levels > 3:
        raise SystemExit("--levels must be 3 or less: the study page ends after 3 timed levels")
        
    if a.kind in ("fitted", "replay") and not a.pool:
        raise SystemExit(f"--kind {a.kind} needs --pool")
        
    asyncio.run(main(a))
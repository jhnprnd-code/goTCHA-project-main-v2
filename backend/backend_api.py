"""
goTCHA edge API (v2).

What changed from v1:
  * Every session is logged with everything needed to recompute features offline
    (spawns, raw events, telemetry, game params). v1 did not log the spawns, which
    made offline training impossible.
  * The engine is split: precheck() = hard, non-statistical rules (real-time bound,
    event structure); behavior() = the statistical verdict. behavior() is the part
    the trained model will replace.
  * A frame-quality gate returns "retry" for sessions with severe client stutter
    instead of rejecting a human. Retries are capped, because the telemetry is
    client-reported and an attacker could claim stutter to dodge classification.
  * /collect/* endpoints: consent registry, participant codes, labeled data
    collection. Bot labels require an admin key so the public page cannot poison
    the dataset.

Run:  uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
Env:  GOTCHA_SECRET, GOTCHA_SITE_SECRET, GOTCHA_ORIGINS, GOTCHA_DEBUG=1,
      GOTCHA_ADMIN_KEY, GOTCHA_DATA, GOTCHA_CONSENT
"""
import hmac
import json
import logging
import math
import os
import secrets
import statistics
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Literal, Optional, Tuple
from model_runtime import load_model

import jwt  # type: ignore[import-not-found]
from fastapi import FastAPI, HTTPException, Request  # type: ignore[import-not-found]
from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
from fastapi.responses import FileResponse  # type: ignore[import-not-found]
from pydantic import BaseModel, Field  # type: ignore[import-not-found]
from gamecore import (GAME, N_OBSTACLES, AIRTIME_MS, LEAD_IN_MS, MIN_GAP_MS, MAX_GAP_MS,
                         make_layout, replay, obstacle_window)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gotcha")
HERE = Path(__file__).resolve().parent

# ------------------------------------------------------------------ config
SECRET_KEY = os.environ.get("GOTCHA_SECRET")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    log.warning("GOTCHA_SECRET not set: using a random key (tokens die on restart).")

SITE_SECRET = os.environ.get("GOTCHA_SITE_SECRET", "dev-site-secret")
if SITE_SECRET == "dev-site-secret":
    log.warning("Using the default dev GOTCHA_SITE_SECRET. Demo only.")

ADMIN_KEY = os.environ.get("GOTCHA_ADMIN_KEY", "")
ALLOWED_ORIGINS = os.environ.get(
    "GOTCHA_ORIGINS", "http://localhost:9000,http://127.0.0.1:9000"
).split(",")
DEBUG = os.environ.get("GOTCHA_DEBUG") == "1"  # DEBUG leaks reasons to the client
DATA_FILE = Path(os.environ.get("GOTCHA_DATA", HERE / "sessions.jsonl"))
CONSENT_FILE = Path(os.environ.get("GOTCHA_CONSENT", HERE / "consent.jsonl"))

# Frame-quality gate. UNCALIBRATED: check against collected telemetry.
MAX_FRAME_GAP_MS = float(os.environ.get("GOTCHA_MAX_FRAME_GAP_MS", "250.0"))
MAX_SLOW_FRAME_RATIO = float(os.environ.get("GOTCHA_MAX_SLOW_RATIO", "0.10"))
MIN_AVG_FPS = float(os.environ.get("GOTCHA_MIN_FPS", "20.0"))

SCHEMA_VERSION = 2
CONSENT_VERSION = "2026-10-v4"   # bump when the consent text on collect.html changes
CHALLENGE_TTL_S = 90
TOKEN_TTL_S = 120
RATE_LIMIT_PER_MIN = 30
MAX_PENDING = 10_000
MAX_LEVELS_PER_PID = 12
MAX_RETRIES = 3                  # stutter-retries per IP per 10 min before we stop excusing it
RETRY_WINDOW_S = 600

# Relaxed limits to account for screen recorders
MAX_FRAME_GAP_MS = 500.0         # Increased from 250.0
MAX_SLOW_FRAME_RATIO = 0.30      # Increased from 0.10
MIN_AVG_FPS = 10.0               # Decreased from 20.0

# ----------------game params (shared w/ client)------------------------- 
GAME = {
    "world_w": 800, "world_h": 300, "ground_y": 250,
    "player_x": 100, "player_size": 48, "obstacle_size": 48,
    "speed_px_s": 300.0,
    "gravity_px_s2": 3000.0,
    "jump_v_px_s": 1000.0,
}
N_OBSTACLES = 8
LEAD_IN_MS = 2000
MIN_GAP_MS, MAX_GAP_MS = 1300, 2600

_V, _G = GAME["jump_v_px_s"], GAME["gravity_px_s2"]
AIRTIME_MS = 2 * _V / _G * 1000.0
_root = math.sqrt(_V ** 2 - 2 * _G * GAME["obstacle_size"])
CLEAR_FROM_MS = (_V - _root) / _G * 1000.0
CLEAR_UNTIL_MS = (_V + _root) / _G * 1000.0


def obstacle_window(spawn_ms: float) -> Tuple[float, float, float]:
    """(lo, hi, center): jump-start times (ms since level start) that clear this obstacle."""
    w, px, ps = GAME["world_w"], GAME["player_x"], GAME["player_size"]
    osz, speed = GAME["obstacle_size"], GAME["speed_px_s"]
    overlap_start = spawn_ms + (w - (px + ps)) / speed * 1000.0
    overlap_end = spawn_ms + (w - (px - osz)) / speed * 1000.0
    lo = overlap_end - CLEAR_UNTIL_MS
    hi = overlap_start - CLEAR_FROM_MS
    return lo, hi, (lo + hi) / 2.0


assert obstacle_window(0)[1] > obstacle_window(0)[0], "physics params make the level unwinnable"


# ------------------------------state--------------------------------- 
@dataclass
class Challenge:
    spawns_ms: List[int]
    level_end_ms: float
    issued_at: float
    meta: dict = field(default_factory=dict)

    def is_expired(self, current_time: float, ttl: int) -> bool:
        """Encapsulation: The object determines its own expiration state."""
        return current_time - self.issued_at > ttl


ml_engine = load_model(HERE / "model.joblib")


CHALLENGES: Dict[str, Challenge] = {}
TOKENS: Dict[str, float] = {}
PARTICIPANTS: Dict[str, dict] = {}     # pid -> registry record (loaded from CONSENT_FILE)
LEVELS_DONE: Dict[str, int] = defaultdict(int)   # pid -> recorded submissions
VALID_DONE: Dict[str, int] = defaultdict(int)    # pid -> valid recorded submissions
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_retries: Dict[str, Deque[float]] = defaultdict(deque)
_rng = secrets.SystemRandom()


def _purge() -> None:
    now = time.time()
    for n in [n for n, c in CHALLENGES.items() if c.is_expired(now, CHALLENGE_TTL_S)]:
        del CHALLENGES[n]
    for j in [j for j, exp in TOKENS.items() if exp < now]:
        del TOKENS[j]


def _bucket_check(store: Dict[str, Deque[float]], key: str, window_s: float, limit: int) -> int:
    """Record a hit and return how many hits are in the window (including this one)."""
    now = time.time()
    q = store[key]
    while q and now - q[0] > window_s:
        q.popleft()
    q.append(now)
    return len(q)


def _rate_limit(ip: str) -> None:
    if _bucket_check(_hits, ip, 60, RATE_LIMIT_PER_MIN) > RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many challenges. Slow down.")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _dump(m: BaseModel) -> dict:
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()


# -------------------------------models---------------------------------
class Event(BaseModel):
    k: Literal["d", "u"]    # key down / key up
    t: float                # ms since level start (client clock)


class Telemetry(BaseModel):
    """Client-reported environment/quality data. Untrusted: used for retries and for
    calibration, never as proof of anything."""
    n_frames: int = Field(0, ge=0, le=1_000_000)
    dt_median_ms: float = Field(0, ge=0, le=1e7)
    dt_p95_ms: float = Field(0, ge=0, le=1e7)
    dt_max_ms: float = Field(0, ge=0, le=1e7)
    n_over_50: int = Field(0, ge=0, le=1_000_000)
    n_over_100: int = Field(0, ge=0, le=1_000_000)
    timer_res_ms: float = Field(0, ge=0, le=1e5)
    dpr: float = Field(1, ge=0, le=100)
    inner_w: int = Field(0, ge=0, le=100_000)
    outer_w: int = Field(0, ge=0, le=100_000)
    canvas_css_w: float = Field(0, ge=0, le=100_000)
    blur_count: int = Field(0, ge=0, le=10_000)
    untrusted_count: int = Field(0, ge=0, le=10_000)
    repeat_count: int = Field(0, ge=0, le=100_000)
    ua: str = Field("", max_length=300)

class BaseEvaluator(ABC):
    """Abstract interface defining polymorphic behavior for all rule or ML engines."""
    
    @abstractmethod
    def precheck(self, ch: Challenge, events: List[Event], elapsed_ms: float):
        pass

    @abstractmethod
    def behavior(self, ch: Challenge, strokes: List[list], feats: dict) -> Verdict:
        pass


class VerificationRequest(BaseModel):
    nonce: str = Field(max_length=64)
    events: List[Event] = Field(max_length=500)
    telemetry: Optional[Telemetry] = None


class CollectSubmit(VerificationRequest):
    pid: str = Field(max_length=16)


class CollectStart(BaseModel):
    consent: bool
    adult: bool
    consent_version: str = Field(max_length=40)
    group: Literal["sighted", "low_vision", "prefer_not_to_say"]
    keyboard: Literal["laptop", "external", "other"]
    aids: List[Literal["none", "browser_zoom", "os_magnifier", "high_contrast", "screen_reader"]] = Field(
        default_factory=list, max_length=5)
    # Bot data only: requires the admin key so the public page can't poison the dataset.
    label: Literal["human", "bot"] = "human"
    bot_kind: str = Field("", max_length=40)
    admin_key: str = Field("", max_length=200)


# ---------------------------- verdict --------------------------------- 
@dataclass
class Verdict:
    verdict: str            # "human",  "bot", "inconclusive"
    reasons: List[str]
    features: dict


def replay(spawns: List[float], strokes: List[list], attribution_ms: float = 1000.0):
    
    """
    Replays the player's key strokes against the level, exactly as the server physics sees it.
    Shared by the rule engine, the feature extractor, and the evaluation code so users can
    never disagree about what happened.
    Returns (accepted, per_obstacle)
      accepted:     [(down_ms, dwell_ms|None)] jumps that actually started (not airborne)
      per_obstacle: one dict per obstacle, in order:
                    {"center", "cleared", "err" (jump_start - ideal, ms) | None, "dwell" | None}
    """
    accepted, last_start = [], -math.inf
    for down, up in strokes:
        if down >= last_start + AIRTIME_MS:
            accepted.append((down, (up - down) if up is not None else None))
            last_start = down

    used, per_obstacle = set(), []
    for spawn in spawns:
        lo, hi, center = obstacle_window(spawn)
        cleared = any(lo <= s <= hi for s, _ in accepted)
        cands = [(abs(s - center), i) for i, (s, _) in enumerate(accepted)
                 if i not in used and abs(s - center) <= attribution_ms]
        err = dwell = None
        if cands:
            _, i = min(cands)
            used.add(i)
            err = accepted[i][0] - center
            dwell = accepted[i][1]
        per_obstacle.append({"center": center, "cleared": cleared, "err": err, "dwell": dwell})
    return accepted, per_obstacle


class VerificationEngine(BaseEvaluator):
    # PLACEHOLDERS for the rule-based behavior()
    MIN_ATTEMPTS = 5
    MIN_CLEARED = 3
    MIN_TIMING_STDEV_MS = 15.0
    MIN_DWELL_STDEV_MS = 8.0
    MIN_DWELL_MS = 10.0
    MAX_DWELL_MS = 3000.0
    MAX_ACCEPTED_RATIO = 2.0
    REALTIME_SLACK_MS = 1500.0
    MAX_EVENTS = 200

    def _pair_events(self, events: List[Event], level_end_ms: float, elapsed_ms: float):
        if len(events) > self.MAX_EVENTS:
            return None
        last_t, pending, strokes = -1.0, False, []
        for e in events:
            if not math.isfinite(e.t) or e.t < 0 or e.t < last_t:
                return None
            if e.t > level_end_ms + 1000 or e.t > elapsed_ms + 250:
                return None
            last_t = e.t
            if e.k == "d" and not pending:
                strokes.append([e.t, None])
                pending = True
            elif e.k == "u" and pending:
                strokes[-1][1] = e.t
                pending = False
        return strokes

    def precheck(self, ch: Challenge, events: List[Event], elapsed_ms: float):
        """Hard rules. Returns (strokes, early_verdict_or_None, feats)."""
        feats = {"elapsed_ms": round(elapsed_ms)}
        if elapsed_ms < ch.level_end_ms - self.REALTIME_SLACK_MS:
            return None, Verdict("bot", ["Submitted before the level could have been played."], feats), feats
        strokes = self._pair_events(events, ch.level_end_ms, elapsed_ms)
        if strokes is None:
            return None, Verdict("bot", ["Malformed or impossible event stream."], feats), feats
        return strokes, None, feats

    def behavior(self, ch: Challenge, strokes: List[list], feats: dict) -> Verdict:
        """Rule-based behavioral verdict. The trained model replaces this method."""
        bot, inconclusive = [], []
        accepted, per_obs = replay(ch.spawns_ms, strokes)
        n_obs = len(ch.spawns_ms)
        errs = [o["err"] for o in per_obs if o["err"] is not None]
        dwells = [o["dwell"] for o in per_obs if o["err"] is not None]
        cleared = sum(1 for o in per_obs if o["cleared"])
        feats.update(n_keystrokes=len(strokes), n_accepted_jumps=len(accepted),
                     cleared=cleared, attempts=len(errs), n_obstacles=n_obs)

        if len(accepted) > self.MAX_ACCEPTED_RATIO * n_obs:
            inconclusive.append("Too many key presses (mashing).")
        if len(errs) < self.MIN_ATTEMPTS:
            inconclusive.append("Not enough attempts to evaluate.")
        if cleared < self.MIN_CLEARED:
            inconclusive.append("Too few obstacles cleared to evaluate.")

        if len(errs) >= self.MIN_ATTEMPTS:
            sd = statistics.stdev(errs)
            feats["timing_err_stdev_ms"] = round(sd, 1)
            feats["timing_err_mean_ms"] = round(statistics.mean(errs), 1)
            if sd < self.MIN_TIMING_STDEV_MS:
                bot.append(f"Timing too consistent (stdev={sd:.1f}ms).")
            valid = [d for d in dwells if d is not None]
            if len(valid) < self.MIN_ATTEMPTS:
                inconclusive.append("Incomplete key-release data.")
            else:
                if min(valid) < self.MIN_DWELL_MS:
                    bot.append("Implausibly short key hold.")
                kept = [d for d in valid if d <= self.MAX_DWELL_MS]
                if len(kept) >= 2:
                    dsd = statistics.stdev(kept)
                    feats["dwell_stdev_ms"] = round(dsd, 1)
                    if dsd < self.MIN_DWELL_STDEV_MS:
                        bot.append(f"Key hold too consistent (stdev={dsd:.1f}ms).")

        if bot:
            return Verdict("bot", bot + inconclusive, feats)
        if inconclusive:
            return Verdict("inconclusive", inconclusive, feats)
        return Verdict("human", ["Natural variance detected."], feats)

    def analyze(self, ch: Challenge, events: List[Event], elapsed_ms: float) -> Verdict:
        strokes, early, feats = self.precheck(ch, events, elapsed_ms)
        return early if early else self.behavior(ch, strokes, feats)


engine = VerificationEngine()


def frame_problem(t: Optional[Telemetry], level_end_ms: float) -> Optional[str]:
    """Reason the client's frame timing was too unstable to judge the player, or None."""
    if t is None:
        return None
    secs = max(level_end_ms / 1000.0, 1.0)
    if t.dt_max_ms > MAX_FRAME_GAP_MS:
        return f"frame stall of {t.dt_max_ms:.0f} ms"
    if t.n_frames and t.n_over_50 / t.n_frames > MAX_SLOW_FRAME_RATIO:
        return "too many slow frames"
    if t.n_frames / secs < MIN_AVG_FPS:
        return f"average {t.n_frames / secs:.0f} fps"
    return None


# -----------------------------logging--------------------------------
def _append(path: Path, rec: dict) -> None:
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError as exc:
        log.warning("could not write %s: %s", path.name, exc)


def _session_record(source: str, ch: Challenge, events: List[Event], tele: Optional[Telemetry],
                    elapsed_ms: float, **extra) -> dict:
    return {
        "schema": SCHEMA_VERSION, "ts": round(time.time(), 3), "source": source,
        "spawns_ms": ch.spawns_ms, "level_end_ms": ch.level_end_ms, "params": GAME,
        "elapsed_ms": round(elapsed_ms),
        "events": [{"k": e.k, "t": e.t} for e in events],
        "telemetry": _dump(tele) if tele else None,
        **extra,
    }


def _load_state() -> None:
    """Rebuild the in-memory registry and level counters after a restart."""
    if CONSENT_FILE.exists():
        for line in CONSENT_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                PARTICIPANTS[rec["pid"]] = rec
            except (ValueError, KeyError):
                continue
    if DATA_FILE.exists():
        for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("source") == "collect" and rec.get("pid"):
                LEVELS_DONE[rec["pid"]] += 1
                if rec.get("valid"):
                    VALID_DONE[rec["pid"]] += 1


_load_state()


# ------------------------------app----------------------------------
app = FastAPI(title="goTCHA Edge API")
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
)


def _new_challenge(meta: Optional[dict] = None) -> Tuple[str, Challenge]:
    spawns, t = [], float(LEAD_IN_MS)
    for _ in range(N_OBSTACLES):
        spawns.append(round(t))
        t += _rng.uniform(MIN_GAP_MS, MAX_GAP_MS)
    level_end = spawns[-1] + (GAME["world_w"] + GAME["obstacle_size"]) / GAME["speed_px_s"] * 1000.0
    nonce = secrets.token_urlsafe(16)
    ch = Challenge(spawns, level_end, time.time(), meta or {})
    CHALLENGES[nonce] = ch
    return nonce, ch


def _challenge_body(nonce: str, ch: Challenge, **extra) -> dict:
    return {"nonce": nonce, "spawns_ms": ch.spawns_ms, "level_end_ms": ch.level_end_ms,
            "params": GAME, **extra}


@app.get("/challenge")
async def get_challenge(request: Request):
    _purge()
    _rate_limit(_client_ip(request))
    if len(CHALLENGES) >= MAX_PENDING:
        raise HTTPException(503, "Busy.")
    nonce, ch = _new_challenge()
    return _challenge_body(nonce, ch)

@app.middleware("http")
async def add_isolation_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    return response


@app.post("/verify")
async def verify(payload: VerificationRequest, request: Request):
    _purge()
    ch = CHALLENGES.pop(payload.nonce, None)   # pop = single use, even on failure
    if ch is None:
        raise HTTPException(410, "Challenge expired or already used.")
    elapsed_ms = (time.time() - ch.issued_at) * 1000.0

    strokes, early, feats = engine.precheck(ch, payload.events, elapsed_ms)
    retry = False
    if early:
        v = early
    else:
        problem = frame_problem(payload.telemetry, ch.level_end_ms)
        if problem:
            n = _bucket_check(_retries, _client_ip(request), RETRY_WINDOW_S, MAX_RETRIES)
            if n <= MAX_RETRIES:
                retry = True
                v = Verdict("retry", [f"Unstable frame timing: {problem}"], feats)
            else:
                v = Verdict("inconclusive", ["Repeated unstable frame timing."], feats)
        elif ml_engine:
            rec = {"spawns_ms": ch.spawns_ms, "events": [{"k": e.k, "t": e.t} for e in payload.events]}
            v_dict = ml_engine.judge(rec)
            v = Verdict(v_dict["verdict"], [v_dict["reason"]], feats)
        else:
            v = engine.behavior(ch, strokes, feats)

    _append(DATA_FILE, _session_record("verify", ch, payload.events, payload.telemetry, elapsed_ms,
                                       verdict=v.verdict, features=v.features))
    log.info("verdict=%s %s", v.verdict, v.features)

    if retry:
        return {"success": False, "retry": True,
                "detail": "Your browser stuttered during that attempt. This is not your fault; please try again."}
    if v.verdict != "human":
        body = {"success": False,
                "detail": "Verification not completed. Press SPACE to jump over each red block, then try again."}
        if DEBUG:
            body.update(result=v.verdict, reasons=v.reasons, features=v.features)
        return body

    now = int(time.time())
    jti = payload.nonce
    token = jwt.encode({"jti": jti, "iat": now, "exp": now + TOKEN_TTL_S, "human": True},
                       SECRET_KEY, algorithm="HS256")
    TOKENS[jti] = now + TOKEN_TTL_S
    body = {"success": True, "token": token, "expires_in": TOKEN_TTL_S}
    if DEBUG:
        body["features"] = v.features
    return body


class SiteVerifyRequest(BaseModel):
    token: str
    secret: str


@app.post("/siteverify")
async def siteverify(req: SiteVerifyRequest):
    """Called by the HOST's backend (never the browser) to redeem a token once."""
    if not hmac.compare_digest(req.secret, SITE_SECRET):
        raise HTTPException(401, "Bad site secret.")
    try:
        claims = jwt.decode(req.token, SECRET_KEY, algorithms=["HS256"],
                            options={"require": ["exp", "jti"]})
    except jwt.PyJWTError:
        return {"success": False, "error": "invalid-or-expired-token"}
    if TOKENS.pop(claims["jti"], None) is None:
        return {"success": False, "error": "token-already-used"}
    return {"success": True}


# --------------------------data collection---------------------------- 
@app.post("/collect/start")
async def collect_start(req: CollectStart, request: Request):
    """Register a participant after consent. Returns an anonymous participant code."""
    if not (req.consent and req.adult):
        raise HTTPException(400, "Consent and confirmation of age are required.")
    if req.consent_version != CONSENT_VERSION:
        raise HTTPException(409, "The consent form was updated. Please reload the page.")
    if _bucket_check(_hits, "start:" + _client_ip(request), 60, 20) > 20:
        raise HTTPException(429, "Too many registrations. Slow down.")

    label = "human"
    if req.label == "bot":
        if not ADMIN_KEY or not hmac.compare_digest(req.admin_key, ADMIN_KEY):
            raise HTTPException(403, "Bot labels require the admin key.")
        label = "bot"

    pid = ("B" if label == "bot" else "P") + secrets.token_hex(3).upper()
    rec = {"pid": pid, "ts": round(time.time(), 3), "consent_version": req.consent_version,
           "label": label, "bot_kind": req.bot_kind if label == "bot" else "",
           "group": req.group, "keyboard": req.keyboard, "aids": req.aids}
    PARTICIPANTS[pid] = rec
    _append(CONSENT_FILE, rec)
    return {"pid": pid}


@app.get("/collect/challenge")
async def collect_challenge(pid: str):
    _purge()
    if pid not in PARTICIPANTS:
        raise HTTPException(404, "Unknown participant code.")
    if LEVELS_DONE[pid] >= MAX_LEVELS_PER_PID:
        raise HTTPException(429, "Level limit reached for this participant code.")
    if len(CHALLENGES) >= MAX_PENDING:
        raise HTTPException(503, "Busy.")
    nonce, ch = _new_challenge({"pid": pid})
    return _challenge_body(nonce, ch, practice=VALID_DONE[pid] == 0)


@app.post("/collect/submit")
async def collect_submit(req: CollectSubmit):
    _purge()
    ch = CHALLENGES.pop(req.nonce, None)
    if ch is None:
        raise HTTPException(410, "Challenge expired or already used.")
    if ch.meta.get("pid") != req.pid or req.pid not in PARTICIPANTS:
        raise HTTPException(400, "Participant code does not match this challenge.")
    p = PARTICIPANTS[req.pid]
    elapsed_ms = (time.time() - ch.issued_at) * 1000.0

    # Nothing is thrown away, so the problems are flagged so training can decide, and so the
    # frame gate can be calibrated against real telemetry.
    flags: List[str] = []
    strokes, early, feats = engine.precheck(ch, req.events, elapsed_ms)
    if early:
        flags.append("hard:" + early.reasons[0])
    problem = frame_problem(req.telemetry, ch.level_end_ms)
    if problem:
        flags.append("frame:" + problem)

    cleared = 0
    if strokes is not None:
        _, per_obs = replay(ch.spawns_ms, strokes)
        cleared = sum(1 for o in per_obs if o["cleared"])
    valid = not flags
    practice = VALID_DONE[req.pid] == 0
    level_idx = LEVELS_DONE[req.pid]
    LEVELS_DONE[req.pid] += 1
    if valid:
        VALID_DONE[req.pid] += 1

    _append(DATA_FILE, _session_record(
        "collect", ch, req.events, req.telemetry, elapsed_ms,
        pid=req.pid, label=p["label"], bot_kind=p.get("bot_kind", ""), group=p["group"],
        level_idx=level_idx, practice=practice, valid=valid, flags=flags,
        features={"cleared": cleared, "n_obstacles": len(ch.spawns_ms)},
    ))
    detail = ""
    if not valid:
        detail = ("Your browser stuttered during that level, so we will redo it. This is not your fault."
                  if problem else "Something looked off with that level, so we will redo it.")
    return {"accepted": True, "valid": valid, "retry": not valid, "practice": practice,
            "level_idx": level_idx, "cleared": cleared, "n_obstacles": len(ch.spawns_ms),
            "detail": detail}


# -----------------------------static----------------------------- 
_NO_CACHE = {"Cache-Control": "no-cache"}
FRONTEND_DIR = HERE.parent / "frontend" # Add this line

@app.get("/collect")
async def collect_page():
    return FileResponse(FRONTEND_DIR / "collect.html", headers=_NO_CACHE)

@app.get("/gotcha-widget.js")
async def widget_js():
    return FileResponse(FRONTEND_DIR / "gotcha-widget.js", media_type="application/javascript", headers=_NO_CACHE)

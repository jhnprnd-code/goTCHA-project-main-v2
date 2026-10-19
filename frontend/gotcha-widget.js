/**
 * gotcha-widget.js (v3 - MVC Architecture)
 * 
 * Implements a strict Model-View-Controller separation.
 * - GotchaModel: Manages state, telemetry, and raw event data independently of the UI.
 * - GotchaView: Handles Canvas rendering, DOM injection, CSS scaling (magnifier fix), and ARIA observers.
 * - GotchaController: Orchestrates network requests, game loops, and input event binding.
 */

class GotchaModel {
    constructor(opts) {
        this.apiBase = (opts.apiBase !== undefined ? opts.apiBase : "http://localhost:8000").replace(/\/$/, "");
        this.collect = opts.collect || null;
        this.promptOpts = Object.assign({
            title: "goTCHA Gateway",
            text: "Press the SPACE bar to jump over the red blocks. It takes about 20 seconds.",
            button: "Verify I Am Human",
        }, opts.prompt || {});
    }

    resetRun() {   // per-attempt state only; leaves the loaded level alone
        this.events = [];
        this.keyHeld = false;
        this.token = null;
        this.counters = { blur: 0, untrusted: 0, repeat: 0 };
        this.frameTimes = [];
        this.jumpStart = -Infinity;
        this.t0 = 0;
        this.timerRes = 0;
    }

    reset() {
        this.state = "PROMPT";
        this.resetRun();
        this.spawns = [];
        this.hit = [];
    }

    loadLevel(data) {
        this.nonce = data.nonce;
        this.spawns = data.spawns_ms;
        this.levelEnd = data.level_end_ms;
        this.p = data.params;
        this.airtimeMs = (2 * this.p.jump_v_px_s / this.p.gravity_px_s2) * 1000;
        this.hit = new Array(this.spawns.length).fill(false);
    }

    recordEvent(type, timestamp) {
        this.events.push({ k: type, t: timestamp });
    }

    generateTelemetry() {
        const r1 = (x) => Math.round(x * 10) / 10;
        const f = this.frameTimes;
        const d = [];
        for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]);
        const s = [...d].sort((a, b) => a - b);
        const q = (p) => (s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0);

        // canvas_css_w is read from the DOM, so the Controller adds it before submitting.
        return {
            n_frames: f.length,
            dt_median_ms: r1(q(0.5)),
            dt_p95_ms: r1(q(0.95)),
            dt_max_ms: r1(s.length ? s[s.length - 1] : 0),
            n_over_50: d.filter((x) => x > 50).length,
            n_over_100: d.filter((x) => x > 100).length,
            timer_res_ms: this.timerRes || 0,
            dpr: window.devicePixelRatio || 1,
            inner_w: window.innerWidth || 0,
            outer_w: window.outerWidth || 0,
            blur_count: this.counters.blur,
            untrusted_count: this.counters.untrusted,
            repeat_count: this.counters.repeat,
            ua: (navigator.userAgent || "").slice(0, 300),
        };
    }
}


class GotchaView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) throw new Error(`goTCHA: #${containerId} not found`);
        
        this._injectStyles();
        this._initDOM();
    }

    _injectStyles() {
        if (document.getElementById("gotcha-styles")) return;
        const s = document.createElement("style");
        s.id = "gotcha-styles";
        s.textContent = `
            .gotcha-subtitle{font-size:clamp(14px,2vw,18px);color:#ccc;margin-top:-6px}
            .gotcha-btn-row{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}
            .gotcha-btn-alt{background:#333;color:#fff;border:2px solid #fff}
            .gotcha-root{position:relative;width:100%;max-width:800px;aspect-ratio:8/3;background:#000;border:3px solid #fff;box-sizing:border-box;color:#fff;font-family:Arial,Helvetica,sans-serif;outline:none}
            .gotcha-root canvas{display:block;width:100%;height:100%}
            .gotcha-overlay{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:16px;text-align:center;background:rgba(0,0,0,.9)}
            .gotcha-title{font-size:clamp(20px,3.2vw,28px);font-weight:bold}
            .gotcha-text{font-size:clamp(16px,2.4vw,20px);line-height:1.4;max-width:34em}
            .gotcha-btn{padding:12px 28px;min-height:48px;background:#ff0;color:#000;border:0;font-size:20px;font-weight:bold;cursor:pointer}
            .gotcha-btn:focus-visible{outline:4px solid #fff;outline-offset:3px}
            .gotcha-sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}`;
        document.head.appendChild(s);
    }

    _el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    }

    _initDOM() {
        const c = this.container;
        c.classList.add("gotcha-root");
        c.setAttribute("role", "group");
        c.setAttribute("aria-label", "goTCHA game");
        c.tabIndex = -1;

        this.canvas = document.createElement("canvas");
        // Fixed internal resolution protects physics math regardless of CSS scaling
        this.canvas.width = 800; 
        this.canvas.height = 300;
        this.canvas.setAttribute("role", "img");
        this.canvas.setAttribute("aria-label", "Game area: jump over the red blocks with the space bar");
        this.ctx = this.canvas.getContext("2d");

        this.overlay = this._el("div", "gotcha-overlay");
        this.live = this._el("div", "gotcha-sr-only");
        this.live.setAttribute("aria-live", "polite");

        c.append(this.canvas, this.overlay, this.live);
    }

    showOverlay({ title, subtitle, titleColor = "#fff", lines = [], button = null, buttons = [], focus = false }) {
        this.overlay.replaceChildren();
        this.overlay.style.display = "flex";

        const t = this._el("div", "gotcha-title", title);
        t.style.color = titleColor;
        this.overlay.appendChild(t);

        if (subtitle) {
            this.overlay.appendChild(this._el("div", "gotcha-subtitle", subtitle));
        }

        lines.forEach((l) => this.overlay.appendChild(this._el("div", "gotcha-text", l)));

        // `button` is the single-button shorthand; `buttons` takes several.
        const allButtons = [...buttons];
        if (button) allButtons.push(button);

        if (allButtons.length > 0) {
            const row = this._el("div", "gotcha-btn-row");
            allButtons.forEach((btn, i) => {
                const b = this._el("button", `gotcha-btn ${btn.cls || ""}`, btn.label);
                b.type = "button";
                b.addEventListener("click", btn.onClick);
                row.appendChild(b);
                if (focus && i === 0) b.focus();
            });
            this.overlay.appendChild(row);
        }
        
        this.live.textContent = [title, subtitle, ...lines].filter(Boolean).join(". ");
    }

    hideOverlay() {
        this.overlay.style.display = "none";
        this.live.textContent = "Game started. Press space to jump.";
    }

    getCanvasCssWidth() {
        return Math.round(this.canvas.getBoundingClientRect().width * 10) / 10;
    }

    drawFrame(t, model) {
        const { world_w: W, world_h: H, ground_y: G, player_x: PX, player_size: PS,
                obstacle_size: OS, speed_px_s: V, gravity_px_s2: g, jump_v_px_s: v0 } = model.p;
        const ctx = this.ctx;
        
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = "#999";
        ctx.fillRect(0, G, W, 4);

        const tau = (t - model.jumpStart) / 1000;
        const h = tau >= 0 && tau <= model.airtimeMs / 1000 ? v0 * tau - 0.5 * g * tau * tau : 0;

        let cleared = 0;
        model.spawns.forEach((s, i) => {
            if (t < s) return;
            const x = W - (V * (t - s)) / 1000;
            if (x + OS < PX && !model.hit[i]) cleared++;
            if (x + OS < 0) return;
            
            if (!model.hit[i] && x < PX + PS && x + OS > PX && h < OS) model.hit[i] = true;
            
            ctx.lineWidth = 4;
            ctx.strokeStyle = "#fff";
            if (model.hit[i]) {
                ctx.strokeRect(x, G - OS, OS, OS); 
            } else {
                ctx.fillStyle = "#f44";
                ctx.fillRect(x, G - OS, OS, OS);
                ctx.strokeRect(x, G - OS, OS, OS);
            }
        });

        ctx.fillStyle = "#ff0";
        ctx.fillRect(PX, G - PS - h, PS, PS);

        ctx.fillStyle = "#fff";
        ctx.font = "bold 26px Arial";
        ctx.textAlign = "left";
        ctx.fillText(`Cleared: ${cleared} / ${model.spawns.length}`, 16, 36);
        ctx.textAlign = "right";
        ctx.fillText("SPACE = jump", W - 16, 36);
        
        if (t < model.spawns[0]) {
            ctx.textAlign = "center";
            ctx.fillText("Get ready…", W / 2, 120);
        }
    }

    clearCanvas() {
        this.ctx.fillStyle = "#000";
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    }
}


class GotchaWidget {
    constructor(containerId, opts = {}) {
        this.model = new GotchaModel(opts);
        this.view = new GotchaView(containerId);
        
        this.onSuccess = opts.onSuccess || (() => {});
        this.onFailure = opts.onFailure || (() => {});
        this.onLevelDone = opts.onLevelDone || (() => null);

        // Bind event contexts
        this._onKeyDown = this._onKeyDown.bind(this);
        this._onKeyUp = this._onKeyUp.bind(this);
        this._onBlur = this._onBlur.bind(this);
        this._onVisibility = this._onVisibility.bind(this);
        this._frame = this._frame.bind(this);

        this._renderPrompt();
    }

    _renderPrompt(focus = false) {
        this.model.state = "PROMPT";
        this.view.clearCanvas();
        
        let btns = [{ 
            label: this.model.promptOpts.button || "PLAY", 
            onClick: () => this.startChallenge() 
        }];
        
        if (this.model.promptOpts.showExit) {
            btns.push({
                label: "EXIT",
                cls: "gotcha-btn-alt",
                onClick: () => {
                    this.view.showOverlay({ title: "Goodbye", lines: ["You can safely close this tab."] });
                }
            });
        }

        this.view.showOverlay({
            title: this.model.promptOpts.title,
            subtitle: this.model.promptOpts.subtitle,
            lines: [this.model.promptOpts.text],
            buttons: btns,
            focus,
        });
    }

    setPrompt(p) {
        Object.assign(this.model.promptOpts, p);
        if (this.model.state === "PROMPT") this._renderPrompt();
    }

    _fail(message, title = "Verification not completed") {
        this.model.state = "RESULT";
        this.model.token = null;
        this.view.showOverlay({
            title,
            titleColor: "#f44",
            lines: [message],
            button: { label: "Try again", onClick: () => this.startChallenge() },
            focus: true,
        });
        this.onFailure(message);
    }

    async startChallenge() {
        if (["LOADING", "GAME", "PROCESSING"].includes(this.model.state)) return;
        
        this.model.state = "LOADING";
        this.view.showOverlay({ title: "Loading…" });

        const url = this.model.collect
            ? `${this.model.apiBase}/collect/challenge?pid=${encodeURIComponent(this.model.collect.pid)}`
            : `${this.model.apiBase}/challenge`;
            
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            this.model.loadLevel(data);
            this.model.resetRun();
            this._beginGame();
        } catch {
            this._fail("Could not reach the service. Please try again.");
        }
    }

    _measureTimerRes() {
        let min = Infinity;
        let prev = performance.now();
        const stop = prev + 25;
        while (prev < stop) {
            const n = performance.now();
            if (n !== prev) {
                min = Math.min(min, n - prev);
                prev = n;
            }
        }
        return isFinite(min) ? Math.round(min * 1000) / 1000 : 0;
    }

    _beginGame() {
        this.model.timerRes = this._measureTimerRes();
        this.model.state = "GAME";
        
        this.view.hideOverlay();
        window.addEventListener("keydown", this._onKeyDown);
        window.addEventListener("keyup", this._onKeyUp);
        window.addEventListener("blur", this._onBlur);
        document.addEventListener("visibilitychange", this._onVisibility);

        this.view.container.focus(); 
        this.model.t0 = performance.now();
        this.raf = requestAnimationFrame(this._frame);
    }

    _cleanup() {
        cancelAnimationFrame(this.raf);
        window.removeEventListener("keydown", this._onKeyDown);
        window.removeEventListener("keyup", this._onKeyUp);
        window.removeEventListener("blur", this._onBlur);
        document.removeEventListener("visibilitychange", this._onVisibility);
    }

    _eventTime(e) {
        let ts = e.timeStamp;
        const now = performance.now();
        if (!(ts > 0) || Math.abs(ts - now) > 1000) ts = now;
        return ts - this.model.t0;
    }

    _onKeyDown(e) {
        if (this.model.state !== "GAME" || e.code !== "Space") return;
        e.preventDefault();
        
        if (e.repeat) { this.model.counters.repeat++; return; }
        if (!e.isTrusted) { this.model.counters.untrusted++; return; } 
        
        const t = this._eventTime(e);
        this.model.recordEvent("d", t);
        this.model.keyHeld = true;
        
        if (t >= this.model.jumpStart + this.model.airtimeMs) {
            this.model.jumpStart = t;
        }
    }

    _onKeyUp(e) {
        if (this.model.state !== "GAME" || e.code !== "Space" || !this.model.keyHeld) return;
        e.preventDefault();
        this.model.recordEvent("u", this._eventTime(e));
        this.model.keyHeld = false;
    }

    _onBlur() {
        if (this.model.state !== "GAME") return;
        this.model.counters.blur++;
        if (this.model.keyHeld) {
            this.model.recordEvent("u", performance.now() - this.model.t0);
            this.model.keyHeld = false;
        }
    }

    _onVisibility() {
        if (document.hidden && this.model.state === "GAME") {
            this._cleanup();
            this._fail("The challenge was interrupted because the tab was hidden.");
        }
    }

    _frame() {
        if (this.model.state !== "GAME") return;
        const now = performance.now();
        this.model.frameTimes.push(now);
        
        const t = now - this.model.t0; 
        this.view.drawFrame(t, this.model);
        
        if (t >= this.model.levelEnd) {
            this._finish();
            return;
        }
        this.raf = requestAnimationFrame(this._frame);
    }

    async _finish() {
        this._cleanup();
        if (this.model.keyHeld) {
            this.model.recordEvent("u", performance.now() - this.model.t0);
            this.model.keyHeld = false;
        }
        
        this.model.state = "PROCESSING";
        this.view.showOverlay({ title: this.model.collect ? "Saving…" : "Analyzing behavioral pattern…" });

        const telemetry = this.model.generateTelemetry();
        telemetry.canvas_css_w = this.view.getCanvasCssWidth();

        const payload = {
            nonce: this.model.nonce,
            events: this.model.events.map((e) => ({ k: e.k, t: Math.round(e.t * 10) / 10 })),
            telemetry: telemetry,
        };
        
        if (this.model.collect) payload.pid = this.model.collect.pid;
        const url = `${this.model.apiBase}${this.model.collect ? "/collect/submit" : "/verify"}`;

        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            
            if (res.status === 410) {
                this._fail("This level expired. Please try again.");
            } else if (data.retry) {
                this._fail(data.detail || "Your browser stuttered. Please try again.", "Let's do that again");
            } else if (this.model.collect && res.ok && data.accepted) {
                this.model.state = "RESULT";
                const next = this.onLevelDone(data) || {};
                this.view.showOverlay({
                    title: next.title || "Level recorded",
                    titleColor: "#0f0",
                    lines: next.lines || [],
                    button: next.button ? { label: next.button, onClick: () => this.startChallenge() } : null,
                    focus: !!next.button,
                });
            } else if (!this.model.collect && res.ok && data.success) {
                this.model.state = "RESULT";
                this.model.token = data.token;
                this.view.showOverlay({ title: "Verification successful!", titleColor: "#0f0", lines: ["You may continue."] });
                this.onSuccess(data.token);
            } else {
                this._fail(data.detail || "That did not work. Please try again.");
            }
        } catch {
            this._fail("Could not reach the service. Please try again.");
        }
    }

    destroy() {
        this._cleanup();
        this.view.container.replaceChildren();
    }
}

"""
ui/app.py — NiceGUI web interface for CueBridge.
"""

import asyncio
import json
import logging
from typing import Any

from nicegui import app, ui

from config import ConfigManager
from cue_engine import CueEngine
from logger_setup import get_recent_logs, register_ui_callback, unregister_ui_callback, clear_log_buffer
from osc_handler import (
    OSCHandler,
    get_osc_traffic, clear_osc_traffic,
    register_osc_callback, unregister_osc_callback,
    register_recall_callback, unregister_recall_callback,
    get_recall_queue,
)
from switcher_manager import SwitcherManager
from switchers.base import SwitcherStatus
from ui.cues import build_cues_panel

logger = logging.getLogger("cuebridge.ui")

_STATUS_COLORS = {
    SwitcherStatus.CONNECTED:    ("cb-dot-ok",   "Connected"),
    SwitcherStatus.CONNECTING:   ("cb-dot-warn", "Connecting…"),
    SwitcherStatus.RECONNECTING: ("cb-dot-warn", "Reconnecting…"),
    SwitcherStatus.ERROR:        ("cb-dot-live", "Error"),
    SwitcherStatus.DISCONNECTED: ("cb-dot-off",  "Disconnected"),
}

_TYPE_LABELS = {
    "atem":     "Blackmagic ATEM",
    "barco":    "Barco Event Master",
    "pixelhue": "PixelHue",
}

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg:        #0c0e13;
  --s1:        #111318;
  --s2:        #171a22;
  --s3:        #1d2130;
  --border:    #252a38;
  --border-s:  #191d29;
  --accent:    #e07b3a;
  --accent-bg: rgba(224,123,58,0.12);
  --live:      #e5534b;
  --live-bg:   rgba(229,83,75,0.12);
  --pvw:       #4d9de0;
  --pvw-bg:    rgba(77,157,224,0.12);
  --ok:        #4ade80;
  --ok-bg:     rgba(74,222,128,0.1);
  --warn:      #f59e0b;
  --text:      #e6e9f2;
  --text-2:    #737a94;
  --text-3:    #363d52;
  --mono:      'JetBrains Mono', 'Cascadia Code', 'Fira Code', ui-monospace, monospace;
  --sans:      'Inter', system-ui, -apple-system, sans-serif;
}

html, body { background: var(--bg) !important; font-family: var(--sans); color: var(--text); }

/* ── Scrollbars ────────────────���────────────────── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

/* ── Quasar overrides ──────��────────────────────── */
.q-card  { background: var(--s1) !important; border: 1px solid var(--border); border-radius: 6px !important; }
.q-tabs  { background: var(--s1) !important; border-bottom: 1px solid var(--border); }
.q-tab-panels { background: var(--bg) !important; }
.q-tab__indicator { background: var(--accent) !important; height: 2px !important; }
.q-tab {
  font-family: var(--sans);
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-3) !important;
  min-height: 44px;
}
.q-tab--active { color: var(--text) !important; }
.q-tab__label  { font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; }
.q-header { background: var(--s1) !important; border-bottom: 1px solid var(--border) !important; box-shadow: none !important; }

.q-field--outlined .q-field__control {
  background: var(--s2) !important;
  border-color: var(--border) !important;
  border-radius: 5px !important;
}
.q-field--outlined .q-field__control:hover { border-color: var(--text-2) !important; }
.q-field--outlined.q-field--focused .q-field__control { border-color: var(--accent) !important; }
.q-field__label { color: var(--text-2) !important; font-size: 0.8rem !important; }
.q-field__native, .q-field__input, .q-field__prepend { color: var(--text) !important; }
.q-select__dropdown-icon { color: var(--text-2) !important; }
.q-menu { background: var(--s2) !important; border: 1px solid var(--border) !important; }
.q-item { color: var(--text) !important; }
.q-item:hover { background: var(--s3) !important; }
.q-item--active { color: var(--accent) !important; }

.q-btn { font-family: var(--sans) !important; font-weight: 500 !important; letter-spacing: 0.02em !important; border-radius: 5px !important; }
.q-dialog__backdrop { background: rgba(0,0,0,0.7) !important; }

/* ── Wordmark ────────────────────────────────────── */
.cb-logo { display: flex; align-items: baseline; gap: 0; line-height: 1; }
.cb-logo-cue    { color: var(--accent); font-weight: 700; font-size: 1.15rem; letter-spacing: -0.01em; }
.cb-logo-bridge { color: var(--text); font-weight: 300; font-size: 1.15rem; letter-spacing: 0.06em; }

/* ── Status pills ────────────────────────────────── */
.cb-pill {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 0.65rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  padding: 2px 9px; border-radius: 100px;
}
.cb-pill-ok   { background: var(--ok-bg);   color: var(--ok);   border: 1px solid rgba(74,222,128,0.25); }
.cb-pill-live { background: var(--live-bg); color: var(--live); border: 1px solid rgba(229,83,75,0.3); }
.cb-pill-dim  { background: var(--s3);      color: var(--text-2); border: 1px solid var(--border); }
.cb-pill-warn { background: rgba(245,158,11,0.1); color: var(--warn); border: 1px solid rgba(245,158,11,0.25); }
.cb-pill-pvw  { background: var(--pvw-bg);  color: var(--pvw);  border: 1px solid rgba(77,157,224,0.25); }

/* ── Status dots ────────────���────────────────────── */
.cb-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.cb-dot-ok   { background: var(--ok);   box-shadow: 0 0 5px rgba(74,222,128,0.5); }
.cb-dot-live { background: var(--live); box-shadow: 0 0 5px rgba(229,83,75,0.5); animation: blink 1.6s ease-in-out infinite; }
.cb-dot-warn { background: var(--warn); }
.cb-dot-off  { background: var(--text-3); }

@keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0.35; } }

/* ── Alert bar ──────────────────────��────────────── */
.cb-alert {
  background: var(--live); color: #fff;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  padding: 5px 20px; display: flex; align-items: center; justify-content: center; gap: 8px;
}

/* ── Switcher card ──────────────────���────────────── */
.sw-card {
  background: var(--s1);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.sw-card-bad  { border-color: var(--live) !important; }
.sw-card-head {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-s);
}
.sw-type-chip {
  font-size: 0.6rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
  padding: 2px 7px; border-radius: 3px;
  background: var(--s3); color: var(--text-2); border: 1px solid var(--border);
  white-space: nowrap;
}
.sw-name  { font-size: 0.95rem; font-weight: 600; color: var(--text); }
.sw-addr  { font-family: var(--mono); font-size: 0.7rem; color: var(--text-3); }
.sw-stats { font-family: var(--mono); font-size: 0.68rem; color: var(--text-3); }
.sw-detail { font-size: 0.72rem; color: var(--live); }

/* ── Preset cue list ───────────────��─────────────── */
.cue-table { width: 100%; border-collapse: collapse; }
.cue-head { font-size: 0.62rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-3); border-bottom: 1px solid var(--border); }
.cue-head th { padding: 6px 10px; font-weight: 700; text-align: left; }
.cue-row { border-bottom: 1px solid var(--border-s); transition: background 80ms; }
.cue-row:hover { background: var(--s2); }
.cue-num  { font-family: var(--mono); font-size: 0.72rem; color: var(--text-3); padding: 7px 10px; width: 52px; text-align: right; }
.cue-name { font-size: 0.83rem; color: var(--text); padding: 7px 10px; }
.cue-btns { padding: 4px 8px; white-space: nowrap; text-align: right; }

/* ── Action buttons ──────────────────────────────── */
.cb-btn-sm {
  font-size: 0.65rem !important; font-weight: 600 !important;
  padding: 3px 9px !important; letter-spacing: 0.05em !important;
  border-radius: 4px !important; border-width: 1px !important; border-style: solid !important;
}
.cb-btn-pvw  { background: var(--pvw-bg) !important;  color: var(--pvw) !important;  border-color: rgba(77,157,224,0.3) !important; }
.cb-btn-pgm  { background: var(--ok-bg) !important;   color: var(--ok) !important;   border-color: rgba(74,222,128,0.3) !important; }
.cb-btn-take { background: var(--ok-bg) !important;   color: var(--ok) !important;   border-color: rgba(74,222,128,0.3) !important; }
.cb-btn-cut  { background: var(--live-bg) !important; color: var(--live) !important; border-color: rgba(229,83,75,0.3) !important; }
.cb-btn-ghost {
  background: transparent !important; color: var(--text-2) !important;
  border-color: var(--border) !important;
}
.cb-btn-ghost:hover { color: var(--text) !important; border-color: var(--text-3) !important; }
.cb-btn-accent { background: var(--accent) !important; color: #fff !important; border-color: var(--accent) !important; }
.cb-btn-danger { background: var(--live-bg) !important; color: var(--live) !important; border-color: rgba(229,83,75,0.25) !important; }

/* ── Section labels ──────────────────────────────── */
.cb-label {
  font-size: 0.63rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--text-3);
}

/* ── OSC reference ───────────────────────────────── */
.osc-cmd {
  font-family: var(--mono); font-size: 0.72rem;
  color: var(--accent); background: var(--accent-bg);
  padding: 2px 7px; border-radius: 3px; white-space: nowrap;
}

/* ── Log / monitor ──────────────────────��────────── */
.log-line {
  font-family: var(--mono); font-size: 0.71rem; line-height: 1.55;
  border-bottom: 1px solid var(--border-s); padding: 1px 0;
}
.log-ts  { color: var(--text-3); }
.log-msg { color: var(--text-2); }
.log-err { color: var(--live); }
.log-wrn { color: var(--warn); }

.monitor-pane, .log-pane {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 10px 14px;
  overflow-y: auto;
  height: calc(100vh - 200px);
  max-height: 720px;
}

/* ── Expansion ─────────���─────────────────────────── */
.q-expansion-item__toggle-icon { color: var(--text-3) !important; }
.q-expansion-item__content     { background: var(--bg) !important; }
.q-item__section--side         { color: var(--text-3) !important; }

/* ── Misc inputs ─────────────��───────────────────── */
.q-toggle__track { background: var(--border) !important; }
.q-toggle--checked .q-toggle__track { background: var(--accent) !important; }
.q-badge { font-size: 0.6rem !important; font-weight: 700 !important; letter-spacing: 0.06em !important; }

/* ── Config card ─────────────────────────────────── */
.cfg-card {
  background: var(--s1); border: 1px solid var(--border); border-radius: 6px;
  padding: 16px 20px;
}

/* ── Cue toast element ───────────────────────────── */
@keyframes cb-drop {
  from { opacity:0; transform:translateX(-50%) translateY(-14px) scale(0.95); }
  to   { opacity:1; transform:translateX(-50%) translateY(0)     scale(1);    }
}
@keyframes cb-fade {
  from { opacity:1; }
  to   { opacity:0; }
}
.cb-toast {
  position: fixed;
  top: 64px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
  pointer-events: none;
  background: #111318;
  border: 1px solid #252a38;
  border-radius: 8px;
  padding: 11px 18px 10px;
  min-width: 220px;
  max-width: 420px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.03);
  animation: cb-drop 0.28s cubic-bezier(0.34,1.4,0.64,1) forwards;
}
.cb-toast.cb-toast-hide {
  animation: cb-fade 0.22s ease forwards;
}
</style>
"""


def _fmt_uptime(secs: float | None) -> str:
    if secs is None:
        return "—"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ──────────────────────────────────────────���──────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def setup_ui(
    config: ConfigManager,
    manager: SwitcherManager,
    osc: OSCHandler,
    engine: CueEngine,
) -> None:
    @ui.page("/")
    async def index():
        _build_page(config, manager, osc, engine)


# ───────────────────────���────────────────────────────��────────────────────────
# Page builder
# ────────────────────────────────────────────��─────────────────────────��──────

def _build_page(
    config: ConfigManager,
    manager: SwitcherManager,
    osc: OSCHandler,
    engine: CueEngine,
) -> None:
    ui.add_head_html(f"<style>{_CSS}")

    # ── Header ─────────────────��──────────────────────────────────────────────
    with ui.header().classes("q-header flex-col w-full p-0"):
        with ui.row().classes("items-center gap-5 px-6 py-3 w-full"):
            # Wordmark
            with ui.element("div").classes("cb-logo"):
                ui.html('<span class="cb-logo-cue">CUE</span><span class="cb-logo-bridge">BRIDGE</span>')

            ui.element("div").classes("w-px h-5 bg-border").style("background: var(--border)")

            ui.label("OSC Switcher Bridge").classes("text-xs tracking-widest uppercase").style("color: var(--text-3)")

            ui.space()

            # OSC status pill
            osc_pill = ui.element("div").classes("cb-pill cb-pill-dim").style("cursor: default")
            with osc_pill:
                osc_dot = ui.element("div").classes("cb-dot cb-dot-off")
                osc_lbl = ui.label("OSC OFF")

        # Disconnect alert (hidden by default)
        alert_row = ui.element("div").classes("cb-alert").style("display:none")
        with alert_row:
            ui.html('<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21L12 2l11 19H1zm11-3v-2h-2v2h2zm0-4v-4h-2v4h2z"/></svg>')
            alert_lbl = ui.label("").style("font-size:0.7rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase")

    # ── Tabs ─────────────────────────────��───────────────���────────────────────
    with ui.tabs().classes("w-full") as tabs:
        tab_cues    = ui.tab("Cues",         icon="queue_music")
        tab_sw      = ui.tab("Switchers",    icon="switch_video")
        tab_osc     = ui.tab("OSC",          icon="settings_input_antenna")
        tab_test    = ui.tab("Test",         icon="play_circle")
        tab_monitor = ui.tab("Monitor",      icon="monitor_heart")
        tab_log     = ui.tab("Log",          icon="terminal")

    with ui.tab_panels(tabs, value=tab_cues).classes("w-full flex-1"):

        with ui.tab_panel(tab_cues):
            build_cues_panel(engine, manager, config)

        with ui.tab_panel(tab_sw):
            _build_switchers_panel(config, manager)

        with ui.tab_panel(tab_osc):
            _build_osc_panel(config, osc)

        with ui.tab_panel(tab_test):
            _build_test_panel(manager)

        with ui.tab_panel(tab_monitor):
            _build_osc_monitor_panel()

        with ui.tab_panel(tab_log):
            _build_log_panel()

    # ── Header update timer ──────────────────────��────────────────────────��───
    async def _refresh_header() -> None:
        if osc.is_running:
            osc_dot.classes(remove="cb-dot-off cb-dot-live", add="cb-dot-ok")
            osc_pill.classes(remove="cb-pill-dim cb-pill-live", add="cb-pill-ok")
            osc_lbl.set_text(f"OSC :{osc.port}")
        else:
            osc_dot.classes(remove="cb-dot-ok cb-dot-warn", add="cb-dot-off")
            osc_pill.classes(remove="cb-pill-ok cb-pill-warn", add="cb-pill-dim")
            osc_lbl.set_text("OSC OFF")

        # Alert for any switcher that isn't cleanly up or intentionally down.
        # CONNECTING included: connect() cancels _reconnect_task at entry, so task
        # check is unreliable — just treat all non-idle states as needing the banner.
        bad = [
            sw for sw in manager.all_switchers()
            if sw.status not in (SwitcherStatus.CONNECTED, SwitcherStatus.DISCONNECTED)
        ]
        if bad:
            parts = []
            for sw in bad:
                if sw.status in (SwitcherStatus.RECONNECTING, SwitcherStatus.CONNECTING):
                    parts.append(f"{sw.name.upper()} RECONNECTING…")
                else:
                    parts.append(f"{sw.name.upper()} OFFLINE")
            alert_lbl.set_text("  ·  ".join(parts))
            alert_row.style("display: flex")
        else:
            alert_row.style("display: none")

    ui.timer(2.0, _refresh_header)

    # ── Cue toast — written directly to document.body via JS to avoid NiceGUI
    # element update and position:fixed ancestor-transform issues.
    import time as _time
    import json as _json
    _recall_q  = get_recall_queue()
    _hide_at   = [0.0]
    _toast_up  = [False]

    _TOAST_CSS = (
        "position:fixed;top:68px;left:50%;transform:translateX(-50%);"
        "z-index:10000;pointer-events:none;"
        "background:#111318;border:1px solid #252a38;border-radius:8px;"
        "padding:11px 18px 10px;min-width:220px;max-width:420px;"
        "box-shadow:0 12px 40px rgba(0,0,0,0.6),0 0 0 1px rgba(255,255,255,0.03);"
        "transition:opacity 0.28s ease;"
    )

    def _drain_toast_queue() -> None:
        now = _time.monotonic()
        if _recall_q:
            ev      = _recall_q.popleft()
            ok      = ev["ok"]
            mode    = ev["mode"]
            preset  = str(ev["preset"])
            sw_name = ev["switcher"]
            badge   = mode if ok else "FAIL"
            col     = "#4ade80" if (ok and mode == "PGM") else "#4d9de0" if ok else "#e5534b"
            inner   = (
                f'<div style="position:absolute;top:0;left:0;right:0;height:2px;border-radius:8px 8px 0 0;background:{col}"></div>'
                f'<div style="font-size:0.6rem;font-weight:800;letter-spacing:0.12em;text-transform:uppercase;color:{col};margin-bottom:5px">{badge}</div>'
                f'<div style="font-family:monospace;font-size:0.92rem;font-weight:500;color:#e6e9f2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{preset}</div>'
                f'<div style="font-size:0.68rem;color:#737a94;margin-top:2px">{sw_name}</div>'
            )
            ui.run_javascript(f"""
(function(){{
    var t=document.getElementById('cb-live-toast');
    if(!t){{t=document.createElement('div');t.id='cb-live-toast';
        t.style.cssText={_json.dumps(_TOAST_CSS)};document.body.appendChild(t);}}
    t.innerHTML={_json.dumps(inner)};
    t.style.opacity='1';t.style.display='block';
}})();""")
            _hide_at[0] = now + 2.5
            _toast_up[0] = True
        elif _toast_up[0] and _hide_at[0] and now >= _hide_at[0]:
            ui.run_javascript(
                "(function(){var t=document.getElementById('cb-live-toast');"
                "if(t){t.style.opacity='0';"
                "setTimeout(function(){var t2=document.getElementById('cb-live-toast');"
                "if(t2)t2.style.display='none';},280);}})();"
            )
            _hide_at[0] = 0.0
            _toast_up[0] = False

    ui.timer(0.15, _drain_toast_queue)


# ────────────────────���──────────────────────────��───────────────────────────��─
# Preset list
# ───────────────────────────────────────���──────────────────────────────��──────

def _copy_osc(cmd: str) -> None:
    js = f"""
(function() {{
    var text = {json.dumps(cmd)};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).catch(function() {{ _fb(text); }});
    }} else {{ _fb(text); }}
    function _fb(t) {{
        var el = document.createElement('textarea');
        el.value = t; el.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
        document.body.appendChild(el); el.focus(); el.select();
        try {{ document.execCommand('copy'); }} catch(e) {{}}
        document.body.removeChild(el);
    }}
}})();
"""
    ui.run_javascript(js)
    ui.notify(f"Copied  {cmd}", timeout=1100, type="positive")


def _build_preset_list(switcher_name: str, presets, sw_type: str = "") -> None:
    safe_name = switcher_name
    is_atem = sw_type == "atem"

    # Transport buttons (not for ATEM — macros are self-contained)
    if not is_atem:
        with ui.row().classes("items-center gap-2 py-3 px-1"):
            ui.label("Transport").classes("cb-label mr-2")
            ui.button("Take", icon="play_arrow",
                      on_click=lambda: _copy_osc(f"/switcher/{safe_name}/take")
                      ).props("dense flat no-caps").classes("cb-btn-sm cb-btn-take")
            ui.button("Cut",  icon="flash_on",
                      on_click=lambda: _copy_osc(f"/switcher/{safe_name}/cut")
                      ).props("dense flat no-caps").classes("cb-btn-sm cb-btn-cut")

    # Search
    search = ui.input(
        placeholder=f"Search {'macros' if is_atem else 'presets'}…"
    ).props("dense outlined clearable").classes("w-full mb-2")

    # Cue table
    cue_container = ui.element("div").classes("w-full")

    def _render_rows(query: str = "") -> None:
        cue_container.clear()
        q = query.strip().lower()
        filtered = [p for p in presets if q in p.name.lower()] if q else presets

        with cue_container:
            with ui.element("div").classes("flex items-center border-b px-1 py-1").style(
                "border-color: var(--border); gap:0"
            ):
                ui.label("#").classes("cb-label").style("width:52px;text-align:right;padding:0 10px")
                ui.label("Name").classes("cb-label flex-1").style("padding:0 10px")
                ui.label("Copy OSC").classes("cb-label").style("padding:0 10px;text-align:right")

            for preset in filtered:
                pid  = preset.id
                name = preset.name

                if is_atem:
                    cmd_run = f"/atem/{safe_name}/macro/{pid}"
                    with ui.element("div").classes("cue-row flex items-center").style("gap:0"):
                        ui.label(str(pid)).classes("cue-num")
                        ui.label(name).classes("cue-name flex-1 truncate")
                        with ui.element("div").classes("cue-btns flex gap-1 flex-shrink-0"):
                            ui.button("Run", icon="play_circle",
                                      on_click=lambda c=cmd_run: _copy_osc(c)
                                      ).props("dense flat no-caps").classes("cb-btn-sm cb-btn-pgm")
                else:
                    cmd_preview = f"/switcher/{safe_name}/preview {pid}"
                    cmd_program = f"/switcher/{safe_name}/program {pid}"
                    with ui.element("div").classes("cue-row flex items-center").style("gap:0"):
                        ui.label(str(pid)).classes("cue-num")
                        ui.label(name).classes("cue-name flex-1 truncate")
                        with ui.element("div").classes("cue-btns flex gap-1 flex-shrink-0"):
                            ui.button("PVW", icon="visibility",
                                      on_click=lambda c=cmd_preview: _copy_osc(c)
                                      ).props("dense flat no-caps").classes("cb-btn-sm cb-btn-pvw")
                            ui.button("PGM", icon="play_circle",
                                      on_click=lambda c=cmd_program: _copy_osc(c)
                                      ).props("dense flat no-caps").classes("cb-btn-sm cb-btn-pgm")

    search.on("input", lambda: _render_rows(search.value))
    _render_rows()


# ───────────────────────��─────────────────────────────────────────────────────
# Switchers Panel
# ──────────────────────────────────────────────��──────────────────────────────

def _build_switchers_panel(config: ConfigManager, manager: SwitcherManager) -> None:
    container = ui.column().classes("w-full gap-3 p-5")
    _dialog_flag: dict = {"open": False}

    def _render_switchers() -> None:
        if _dialog_flag["open"]:
            return
        container.clear()
        with container:
            with ui.row().classes("items-center gap-3 mb-1"):
                ui.label("Switchers").classes("text-sm font-semibold tracking-wide").style("color:var(--text)")
                ui.space()
                ui.button("Add Switcher", icon="add",
                          on_click=lambda: _open_switcher_dialog(
                              None, config, manager, _render_switchers, _dialog_flag)
                          ).props("no-caps").classes("cb-btn-sm cb-btn-accent").style("padding:5px 12px !important")

            switchers = manager.all_switchers()
            if not switchers:
                with ui.element("div").classes("w-full flex flex-col items-center gap-2 py-16"):
                    ui.html('<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--text-3)"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>')
                    ui.label("No switchers configured").style("color:var(--text-3);font-size:0.85rem")
                    ui.label("Click Add Switcher to begin.").style("color:var(--text-3);font-size:0.75rem")
                return

            for sw in switchers:
                _build_switcher_card(sw, config, manager, _dialog_flag, _render_switchers)

    _render_switchers()


def _build_switcher_card(sw, config, manager, dialog_flag, refresh_fn) -> None:
    _ALL_DOT = "cb-dot-ok cb-dot-warn cb-dot-live cb-dot-off"

    initial_dot, _ = _STATUS_COLORS.get(sw.status, ("cb-dot-off", "Disconnected"))
    is_bad_init = sw.status in (SwitcherStatus.ERROR, SwitcherStatus.RECONNECTING)

    card = ui.element("div").classes("sw-card w-full" + (" sw-card-bad" if is_bad_init else ""))
    with card:
        # ── Card header ───────────────────────────────────────────────────────
        with ui.element("div").classes("sw-card-head"):
            dot = ui.element("div").classes(f"cb-dot {initial_dot}")

            with ui.element("div").classes("flex flex-col flex-1 gap-0.5 min-w-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(sw.name).classes("sw-name truncate")
                    type_str = _TYPE_LABELS.get(sw._config_type(), sw._config_type())
                    ui.html(f'<span class="sw-type-chip">{type_str}</span>')
                with ui.row().classes("items-center gap-2"):
                    ui.label(sw.address).classes("sw-addr")
                    detail_lbl = ui.label(sw.status_detail or "")

                stats_lbl = ui.label("").classes("sw-stats")

            # Action buttons (right side)
            with ui.element("div").classes("flex gap-1.5 flex-shrink-0"):
                connect_btn = ui.button(
                    "Connect", icon="link",
                    on_click=lambda s=sw: _do_connect(s, manager, refresh_fn)
                ).props("no-caps dense").classes("cb-btn-sm cb-btn-pvw")

                disconnect_btn = ui.button(
                    "Disconnect", icon="link_off",
                    on_click=lambda s=sw: _do_disconnect(s, manager, refresh_fn)
                ).props("no-caps dense").classes("cb-btn-sm cb-btn-ghost")

                refresh_btn = ui.button(
                    "Refresh", icon="sync",
                    on_click=lambda s=sw: _do_refresh(s, manager, refresh_fn)
                ).props("no-caps dense").classes("cb-btn-sm cb-btn-ghost")

                ui.button(
                    "Edit", icon="edit",
                    on_click=lambda s=sw: _open_switcher_dialog(
                        s.id, config, manager, refresh_fn, dialog_flag)
                ).props("no-caps dense").classes("cb-btn-sm cb-btn-ghost")

                ui.button(
                    "Remove", icon="delete",
                    on_click=lambda s=sw: _do_remove(s, manager, refresh_fn, dialog_flag)
                ).props("no-caps dense").classes("cb-btn-sm cb-btn-danger")

        # Type chip — render inline (can't bind text to a non-label element easily)
        # Done via static label in the header above; type doesn't change at runtime

        # ── Preset expansion ─────────────────────────────────────────────────
        if sw.presets:
            sw_type = sw._config_type()
            label = f"{len(sw.presets)} Macros" if sw_type == "atem" else f"{len(sw.presets)} Presets"
            with ui.expansion(label, icon="queue_music") \
                    .classes("w-full").style("border-top: 1px solid var(--border-s)"):
                _build_preset_list(sw.name, sw.presets, sw_type)

        # ── Per-card timer ────────────────────────────────────────────────────
        _DETAIL_STYLE = {
            SwitcherStatus.CONNECTED:    "font-size:0.72rem;color:var(--ok)",
            SwitcherStatus.CONNECTING:   "font-size:0.72rem;color:var(--warn)",
            SwitcherStatus.RECONNECTING: "font-size:0.72rem;color:var(--warn)",
            SwitcherStatus.ERROR:        "font-size:0.72rem;color:var(--live)",
            SwitcherStatus.DISCONNECTED: "font-size:0.72rem;color:var(--text-3)",
        }
        _prev: dict = {"n_presets": len(sw.presets)}

        def _tick(s=sw) -> None:
            dot_cls, _ = _STATUS_COLORS.get(s.status, ("cb-dot-off", ""))
            dot.classes(remove=_ALL_DOT, add=dot_cls)
            detail_lbl.set_text(s.status_detail or "")
            detail_lbl.style(_DETAIL_STYLE.get(s.status, "font-size:0.72rem;color:var(--text-3)"))

            up = _fmt_uptime(s.uptime_seconds)
            stats_lbl.set_text(f"up {up}  ·  {s.command_count} cmd  ·  {s.error_count} err")

            is_bad = s.status in (SwitcherStatus.ERROR, SwitcherStatus.RECONNECTING)
            if is_bad:
                card.classes(add="sw-card-bad")
            else:
                card.classes(remove="sw-card-bad")

            is_conn = s.is_connected
            connect_btn.set_visibility(not is_conn)
            disconnect_btn.set_visibility(is_conn)
            refresh_btn.set_visibility(is_conn)

            now_n = len(s.presets)
            if now_n != _prev["n_presets"]:
                _prev["n_presets"] = now_n
                asyncio.get_event_loop().call_soon(refresh_fn)

        _tick()
        ui.timer(1.5, _tick)

        # ATEM: rescan macros every 30 s (macros can change without reconnecting)
        if sw._config_type() == "atem":
            async def _rescan_macros(s=sw) -> None:
                if s.is_connected:
                    s.presets = await s.get_presets()
            ui.timer(30.0, _rescan_macros)


def _do_connect(sw, manager, refresh_fn):
    async def _run():
        await manager.connect(sw.id)
        refresh_fn()
    asyncio.create_task(_run())


def _do_disconnect(sw, manager, refresh_fn):
    async def _run():
        await manager.disconnect(sw.id)
        refresh_fn()
    asyncio.create_task(_run())


def _do_refresh(sw, manager, refresh_fn):
    async def _run():
        await manager.refresh_presets(sw.id)
        refresh_fn()
    asyncio.create_task(_run())
    ui.notify("Preset list refreshed.", type="positive")


def _do_remove(sw, manager, refresh_fn, dialog_flag=None):
    async def _run():
        await manager.remove(sw.id)
        refresh_fn()

    def _close():
        if dialog_flag:
            dialog_flag["open"] = False
        dlg.close()

    if dialog_flag:
        dialog_flag["open"] = True

    with ui.dialog().props("persistent") as dlg, \
            ui.card().classes("p-6 gap-4").style("background:var(--s2);min-width:320px"):
        ui.label(f"Remove '{sw.name}'?").style("font-size:1rem;font-weight:600;color:var(--text)")
        ui.label("This will disconnect and delete the switcher from config.") \
            .style("font-size:0.8rem;color:var(--text-2)")
        with ui.row().classes("gap-2 mt-2 justify-end w-full"):
            ui.button("Cancel", on_click=_close).props("no-caps").classes("cb-btn-sm cb-btn-ghost")
            ui.button("Remove", icon="delete",
                      on_click=lambda: (asyncio.create_task(_run()), _close())
                      ).props("no-caps").classes("cb-btn-sm cb-btn-danger")
    dlg.open()


def _open_switcher_dialog(
    switcher_id: str | None,
    config: ConfigManager,
    manager: SwitcherManager,
    refresh_fn,
    dialog_flag: dict | None = None,
) -> None:
    existing_cfg = config.get_switcher(switcher_id) if switcher_id else None
    title = "Edit Switcher" if existing_cfg else "Add Switcher"

    if dialog_flag:
        dialog_flag["open"] = True

    def _release():
        if dialog_flag:
            dialog_flag["open"] = False
        refresh_fn()

    with ui.dialog().props("persistent") as dlg, \
            ui.card().classes("p-6 gap-4").style("background:var(--s2);width:360px;max-width:95vw"):

        ui.label(title).style("font-size:1rem;font-weight:600;color:var(--text);margin-bottom:4px")

        name_input = ui.input("Name", value=existing_cfg.get("name", "") if existing_cfg else "") \
            .classes("w-full")
        type_select = ui.select(
            options={
                "atem":     "Blackmagic ATEM",
                "barco":    "Barco Event Master",
                "pixelhue": "PixelHue",
            },
            value=existing_cfg.get("type", "barco") if existing_cfg else "barco",
            label="Type",
        ).classes("w-full")
        ip_input = ui.input("IP Address", value=existing_cfg.get("ip", "") if existing_cfg else "") \
            .classes("w-full")

        ph_user_input = ui.input(
            "Username",
            value=existing_cfg.get("username", "admin") if existing_cfg else "admin",
        ).classes("w-full")
        ph_pass_input = ui.input(
            "Password",
            value=existing_cfg.get("password", "MTIzNDU2") if existing_cfg else "MTIzNDU2",
            password=True,
        ).classes("w-full")
        ph_hint = ui.label("Default password is base64 of '123456'") \
            .style("font-size:0.72rem;color:var(--text-3)")

        # Decode stored target_screen_ids back to a comma string for display
        _stored_ids = existing_cfg.get("target_screen_ids", []) if existing_cfg else []
        _stored_ids_str = ", ".join(str(x) for x in _stored_ids)

        ph_screen_input = ui.input(
            "Target Screen IDs",
            value=_stored_ids_str,
            placeholder="e.g. 1, 2, 5  (leave empty = all screens)",
        ).classes("w-full").tooltip(
            "Comma-separated screen IDs to include in take/cut. "
            "Leave empty to use all discovered screens. "
            "Check the log after connecting to see which IDs your device has."
        )
        ph_screen_hint = ui.label("Empty = use all discovered screens") \
            .style("font-size:0.72rem;color:var(--text-3)")

        atem_me_input = ui.number(
            "M/E Bus (0 = M/E 1)",
            value=existing_cfg.get("me", 0) if existing_cfg else 0,
            min=0, max=7, step=1,
        ).classes("w-full").tooltip("0-indexed M/E bus for cut/take. 0 = M/E 1.")

        def _update_type_fields():
            t = type_select.value
            ph_user_input.set_visibility(t == "pixelhue")
            ph_pass_input.set_visibility(t == "pixelhue")
            ph_hint.set_visibility(t == "pixelhue")
            ph_screen_input.set_visibility(t == "pixelhue")
            ph_screen_hint.set_visibility(t == "pixelhue")
            atem_me_input.set_visibility(t == "atem")

        type_select.on("update:model-value", lambda: _update_type_fields())
        _update_type_fields()

        auto_toggle = ui.switch("Auto-connect on startup",
                                value=existing_cfg.get("auto_connect", True) if existing_cfg else True)

        transition_input = ui.number(
            "Transition Time (ms)",
            value=existing_cfg.get("transition_time", 1000) if existing_cfg else 1000,
            min=0, max=60000, step=100,
        ).classes("w-full").tooltip("Duration for animated recalls and takes. 0 = instant.")

        _DEFAULT_PORTS = {"atem": 9910, "barco": 9999, "pixelhue": 8088}

        async def _save():
            if not name_input.value.strip():
                ui.notify("Name is required.", type="warning")
                return
            if not ip_input.value.strip():
                ui.notify("IP address is required.", type="warning")
                return

            cfg: dict[str, Any] = {
                "name":            name_input.value.strip(),
                "type":            type_select.value,
                "ip":              ip_input.value.strip(),
                "port":            _DEFAULT_PORTS[type_select.value],
                "auto_connect":    auto_toggle.value,
                "transition_time": max(0, int(transition_input.value or 1000)),
            }
            if type_select.value == "pixelhue":
                cfg["username"] = ph_user_input.value.strip() or "admin"
                cfg["password"] = ph_pass_input.value or "MTIzNDU2"
                raw = ph_screen_input.value.strip()
                if raw:
                    try:
                        cfg["target_screen_ids"] = [int(x.strip()) for x in raw.split(",") if x.strip()]
                    except ValueError:
                        ui.notify("Target Screen IDs must be comma-separated integers.", type="warning")
                        return
                else:
                    cfg["target_screen_ids"] = []
            elif type_select.value == "atem":
                cfg["me"] = max(0, int(atem_me_input.value or 0))

            if existing_cfg:
                await manager.update(switcher_id, cfg)
                ui.notify(f"Updated {cfg['name']}", type="positive")
            else:
                sw = await manager.add(cfg)
                if sw:
                    ui.notify(f"Added {sw.name}", type="positive")
                    if sw.auto_connect:
                        asyncio.create_task(manager.connect(sw.id))
            dlg.close()
            _release()

        def _cancel():
            dlg.close()
            _release()

        with ui.row().classes("mt-4 gap-2 w-full justify-end"):
            ui.button("Cancel", on_click=_cancel).props("no-caps").classes("cb-btn-sm cb-btn-ghost")
            ui.button("Save", icon="save", on_click=_save).props("no-caps").classes("cb-btn-sm cb-btn-accent")

    dlg.open()


def _patch_switcher_type():
    from switchers.atem import AtemSwitcher
    from switchers.barco_eventmaster import BarcoEventMaster
    from switchers.pixelhue import PixelHueSwitcher
    AtemSwitcher._config_type     = lambda self: "atem"
    BarcoEventMaster._config_type = lambda self: "barco"
    PixelHueSwitcher._config_type = lambda self: "pixelhue"

_patch_switcher_type()


# ────────────────────���────────────────────────────────��───────────────────────
# OSC Settings Panel
# ────────────────────────────────────────���───────────────────────────────��────

def _build_osc_panel(config: ConfigManager, osc: OSCHandler) -> None:

    with ui.column().classes("w-full p-5 gap-4").style("max-width: 560px"):

        ui.label("OSC Settings").style("font-size:0.75rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-3)")

        # Web UI port
        with ui.element("div").classes("cfg-card flex flex-col gap-3"):
            ui.label("Web UI Server").classes("cb-label")
            web_port_input = ui.number(
                "Web UI Port",
                value=config.web_ui_port,
                min=1024, max=65535,
            ).classes("w-full")
            ui.label("Restart required after changing port.") \
                .style("font-size:0.72rem;color:var(--text-3)")

            def _save_web_port():
                config.web_ui_port = int(web_port_input.value)
                ui.notify(f"Saved — restart CueBridge on port {config.web_ui_port} to apply.", type="info")

            ui.button("Save", on_click=_save_web_port) \
                .props("no-caps").classes("cb-btn-sm cb-btn-ghost mt-1")

        # Status row
        with ui.element("div").classes("cfg-card flex items-center gap-3"):
            status_dot = ui.element("div").classes("cb-dot cb-dot-off")
            status_lbl = ui.label("OSC server stopped").style("font-size:0.85rem;color:var(--text-2)")

        # Listener settings
        with ui.element("div").classes("cfg-card flex flex-col gap-4"):
            ui.label("Listener").classes("cb-label")

            port_input = ui.number(
                "UDP Port",
                value=config.osc_port,
                min=1024, max=65535,
            ).classes("w-full")
            bind_input = ui.input(
                "Bind Address",
                value=config.osc_bind_address,
                placeholder="0.0.0.0 = all interfaces",
            ).classes("w-full")

            async def _apply():
                config.osc_port         = int(port_input.value)
                config.osc_bind_address = bind_input.value.strip() or "0.0.0.0"
                ok = await osc.restart()
                ui.notify(
                    f"Restarted on :{config.osc_port}" if ok
                    else "Failed to start OSC — check the port.",
                    type="positive" if ok else "negative",
                )

            async def _stop():
                await osc.stop()
                ui.notify("OSC server stopped.", type="warning")

            with ui.row().classes("gap-2"):
                ui.button("Apply & Restart", icon="refresh", on_click=_apply) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-accent")
                ui.button("Stop", icon="stop", on_click=_stop) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-ghost")

        # File logging
        with ui.element("div").classes("cfg-card flex flex-col gap-3"):
            ui.label("File Logging").classes("cb-label")
            log_toggle = ui.switch("Write log to file", value=config.log_to_file)
            log_path   = ui.input("Log file path", value=config.log_file_path).classes("w-full")

            def _save_log_settings():
                config.log_to_file   = log_toggle.value
                config.log_file_path = log_path.value.strip() or "cuebridge.log"
                ui.notify("Saved — restart to apply file logging.", type="info")

            ui.button("Save", on_click=_save_log_settings) \
                .props("no-caps").classes("cb-btn-sm cb-btn-ghost mt-1")

        # Config import/export
        with ui.element("div").classes("cfg-card flex flex-col gap-3"):
            ui.label("Configuration").classes("cb-label")
            with ui.row().classes("gap-2"):
                def _export():
                    text = config.export_json()
                    with ui.dialog() as d, \
                            ui.card().classes("p-4 gap-3").style("background:var(--s2);min-width:480px"):
                        ui.label("Export Config").style("font-weight:600;font-size:0.9rem;color:var(--text)")
                        ui.textarea(value=text).classes("w-full").props("rows=16") \
                            .style("font-family:var(--mono);font-size:0.72rem")
                        ui.button("Close", on_click=d.close).props("no-caps").classes("cb-btn-sm cb-btn-ghost")
                    d.open()

                ui.button("Export", icon="download", on_click=_export) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-ghost")

                def _import():
                    with ui.dialog() as d, \
                            ui.card().classes("p-4 gap-3").style("background:var(--s2);min-width:480px"):
                        ui.label("Import Config").style("font-weight:600;font-size:0.9rem;color:var(--text)")
                        ta = ui.textarea().classes("w-full").props("rows=16") \
                            .style("font-family:var(--mono);font-size:0.72rem")
                        def _do_import():
                            ok = config.import_json(ta.value)
                            ui.notify("Imported — restart to apply." if ok else "Invalid JSON.",
                                      type="positive" if ok else "negative")
                            d.close()
                        with ui.row().classes("gap-2 justify-end"):
                            ui.button("Cancel", on_click=d.close).props("no-caps").classes("cb-btn-sm cb-btn-ghost")
                            ui.button("Import", icon="upload", on_click=_do_import).props("no-caps").classes("cb-btn-sm cb-btn-accent")
                    d.open()

                ui.button("Import", icon="upload", on_click=_import) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-ghost")

        # OSC address reference
        with ui.element("div").classes("cfg-card flex flex-col gap-2"):
            ui.label("OSC Address Reference").classes("cb-label mb-1")
            ref = [
                ("── Cue List ───────────────────────────────────────────", ""),
                ("/cue/go",                                    "Fire next cue"),
                ("/cue/back",                                  "Fire previous cue (alias: /cue/prev)"),
                ("/cue/reset",                                 "Reset pointer to before first cue"),
                ("/cue/<number>",                              "Jump to cue by display number and fire"),
                ("── ATEM ──────────────────────────────────────────────", ""),
                ("/atem/<name>/macro/<index>",                 "Run ATEM macro by slot index"),
                ("/atem/all/macro/<index>",                    "Run macro on all connected ATEMs"),
                ("── Barco / PixelHue ───────────────────────────────────", ""),
                ("/switcher/<name>/program <int>",             "Load preset to PROGRAM by number"),
                ("/switcher/<name>/program <string>",          "Load preset to PROGRAM by name"),
                ("/switcher/<name>/recall <int|string>",       "Alias for /program"),
                ("/switcher/<name>/preview <int>",             "Load preset to PREVIEW by number"),
                ("/switcher/<name>/preview <string>",          "Load preset to PREVIEW by name"),
                ("/switcher/<name>/take",                      "Take — transition PVW → PGM"),
                ("/switcher/<name>/take <ms>",                 "Take with one-shot transition override"),
                ("/switcher/<name>/cut",                       "Hard cut PVW → PGM (instant)"),
                ("/switcher/all/<command>",                    "Broadcast to all connected switchers"),
                ("/switcher/<name>/layer/<n>/opacity <0-100>", "Set layer opacity"),
                ("/switcher/<name>/transition <ms>",           "Set transition time (live)"),
            ]
            for addr, desc in ref:
                if addr.startswith("──"):
                    ui.label(addr).style(
                        "font-size:0.6rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;"
                        "color:var(--text-3);padding:8px 0 2px"
                    )
                    continue
                with ui.element("div").classes("flex items-start gap-3 py-1").style(
                    "border-bottom: 1px solid var(--border-s)"
                ):
                    ui.html(f'<code class="osc-cmd">{addr}</code>')
                    ui.label(desc).style("font-size:0.75rem;color:var(--text-2);padding-top:3px")

        # Status poll
        async def _poll_osc_status():
            if osc.is_running:
                status_dot.classes(remove="cb-dot-off cb-dot-warn", add="cb-dot-ok")
                status_lbl.set_text(f"Listening · UDP :{osc.port}")
                status_lbl.style("color:var(--ok)")
            else:
                status_dot.classes(remove="cb-dot-ok cb-dot-warn", add="cb-dot-off")
                status_lbl.set_text("Stopped")
                status_lbl.style("color:var(--text-2)")

        ui.timer(1.5, _poll_osc_status)


# ─────────────────────────────────────────────────────────────────────────────
# Manual Test Panel
# ──────────────────────────────────────────────���──────────────────────────────

def _build_test_panel(manager: SwitcherManager) -> None:

    with ui.column().classes("w-full p-5 gap-4").style("max-width: 560px"):

        ui.label("Manual Test").style("font-size:0.75rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-3)")

        target_opts: dict[str, str] = {"all": "All Connected Switchers"}

        def _refresh_targets():
            target_opts.clear()
            target_opts["all"] = "All Connected Switchers"
            for sw in manager.all_switchers():
                target_opts[sw.id] = f"{sw.name}  {'●' if sw.is_connected else '○'}"
            target_sel.set_options(target_opts)
            tgt2_sel.set_options(target_opts)

        # Preset commands
        with ui.element("div").classes("cfg-card flex flex-col gap-3"):
            ui.label("Preset Commands").classes("cb-label")

            target_sel = ui.select(options=target_opts, value="all", label="Target") \
                .classes("w-full")
            preset_input = ui.input("Preset number or name", placeholder="1  or  Scene 1") \
                .classes("w-full")

            def _resolve_preset() -> int | str | None:
                raw = preset_input.value.strip()
                if not raw:
                    ui.notify("Enter a preset number or name.", type="warning")
                    return None
                try:
                    return int(raw)
                except ValueError:
                    return raw

            def _resolve_name(sel) -> str:
                if sel.value == "all":
                    return "all"
                sw = manager.get(sel.value)
                return sw.name if sw else "all"

            async def _notify_results(label: str, results: dict) -> None:
                if results:
                    msgs = [f"{n}: {'OK' if ok else 'FAIL'}" for n, ok in results.items()]
                    ui.notify(f"{label}  {' | '.join(msgs)}",
                              type="positive" if all(results.values()) else "warning")
                else:
                    ui.notify("No switchers matched.", type="negative")

            async def _do_preview():
                preset = _resolve_preset()
                if preset is None:
                    return
                results = await manager.osc_preview(_resolve_name(target_sel), preset)
                await _notify_results("PVW", results)

            async def _do_program():
                preset = _resolve_preset()
                if preset is None:
                    return
                results = await manager.osc_recall(_resolve_name(target_sel), preset)
                await _notify_results("PGM", results)

            with ui.row().classes("gap-2 flex-wrap"):
                ui.button("Load Preview", icon="visibility", on_click=_do_preview) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-pvw")
                ui.button("Load Program", icon="play_circle", on_click=_do_program) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-pgm")
                ui.button("Refresh", icon="refresh", on_click=_refresh_targets) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-ghost")

        # Transport
        with ui.element("div").classes("cfg-card flex flex-col gap-3"):
            ui.label("Transport").classes("cb-label")

            tgt2_sel = ui.select(options=target_opts, value="all", label="Target") \
                .classes("w-full")

            def _resolve_t2() -> str:
                if tgt2_sel.value == "all":
                    return "all"
                sw = manager.get(tgt2_sel.value)
                return sw.name if sw else "all"

            async def _do_take():
                results = await manager.osc_take(_resolve_t2())
                ui.notify(f"TAKE → {list(results.keys())}", type="positive" if results else "negative")

            async def _do_cut():
                results = await manager.osc_cut(_resolve_t2())
                ui.notify(f"CUT → {list(results.keys())}", type="positive" if results else "negative")

            with ui.row().classes("gap-2"):
                ui.button("Take", icon="play_arrow", on_click=_do_take) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-take")
                ui.button("Cut", icon="flash_on", on_click=_do_cut) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-cut")

        ui.timer(3.0, _refresh_targets)


# ─────────────────────────────────────────────────────────────────────────────
# OSC Monitor Panel
# ───────────────────────────────────────────��───────────────────────────────���─

def _build_osc_monitor_panel() -> None:

    with ui.column().classes("w-full h-full p-5 gap-3"):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.label("OSC Monitor").style("font-size:0.75rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-3)")
            ui.space()
            ui.button("Clear", icon="delete_sweep",
                      on_click=lambda: (clear_osc_traffic(), monitor_container.clear())
                      ).props("no-caps dense").classes("cb-btn-sm cb-btn-ghost")

        # Column headers
        with ui.element("div").classes("flex gap-0 px-3 py-1").style(
            "border-bottom: 1px solid var(--border)"
        ):
            ui.label("Time").classes("cb-label").style("width:80px;flex-shrink:0")
            ui.label("Source").classes("cb-label").style("width:128px;flex-shrink:0")
            ui.label("Address").classes("cb-label").style("width:220px;flex-shrink:0")
            ui.label("Args").classes("cb-label")

        monitor_container = ui.column().classes("monitor-pane w-full gap-0")

        def _render_entry(entry: dict, container) -> None:
            with container:
                with ui.element("div").classes("flex gap-0 log-line w-full"):
                    ui.label(entry.get("ts", "")).style(
                        "font-family:var(--mono);font-size:0.7rem;color:var(--text-3);width:80px;flex-shrink:0"
                    )
                    ui.label(entry.get("src", "")).style(
                        "font-family:var(--mono);font-size:0.7rem;color:var(--text-3);width:128px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                    )
                    ui.label(entry.get("address", "")).style(
                        "font-family:var(--mono);font-size:0.7rem;color:var(--accent);width:220px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                    )
                    args_str = "  ".join(str(a) for a in entry.get("args", []))
                    ui.label(args_str).style(
                        "font-family:var(--mono);font-size:0.7rem;color:var(--text-2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                    )

        with monitor_container:
            for entry in get_osc_traffic():
                _render_entry(entry, monitor_container)

        def _on_osc_message(entry: dict) -> None:
            _render_entry(entry, monitor_container)
            ui.run_javascript(
                "const el = document.querySelector('.monitor-pane'); "
                "if(el) el.scrollTop = el.scrollHeight;"
            )

        register_osc_callback(_on_osc_message)
        app.on_disconnect(lambda: unregister_osc_callback(_on_osc_message))


# ──────────────────────────────���────────────────────────────────────��─────────
# Live Log Panel
# ─────────────────────────────────────────���───────────────────────────────────

def _build_log_panel() -> None:

    with ui.column().classes("w-full h-full p-5 gap-3"):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.label("Live Log").style("font-size:0.75rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-3)")
            ui.space()
            level_filter = ui.select(
                options={"ALL": "All", "INFO": "Info+", "WARNING": "Warnings+", "ERROR": "Errors only"},
                value="ALL",
                label="Level",
            ).classes("w-36").props("dense outlined")
            ui.button("Clear", icon="delete_sweep",
                      on_click=lambda: (clear_log_buffer(), log_container.clear())
                      ).props("no-caps dense").classes("cb-btn-sm cb-btn-ghost")

        log_container = ui.column().classes("log-pane w-full gap-0")

        _LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

        _LEVEL_COLOR = {
            "DEBUG":    "color:var(--text-3)",
            "INFO":     "color:var(--text-2)",
            "WARNING":  "color:var(--warn)",
            "ERROR":    "color:var(--live)",
            "CRITICAL": "color:var(--live)",
        }

        def _render_entry(entry: dict, container) -> None:
            lvl = entry.get("level", "INFO")
            if level_filter.value != "ALL":
                if _LEVEL_ORDER.get(lvl, 0) < _LEVEL_ORDER.get(level_filter.value, 0):
                    return
            with container:
                with ui.element("div").classes("flex gap-3 log-line w-full"):
                    ui.label(entry.get("ts", "")).style(
                        "font-family:var(--mono);font-size:0.7rem;color:var(--text-3);flex-shrink:0;width:76px"
                    )
                    ui.label(entry.get("msg", "")).style(
                        f"font-family:var(--mono);font-size:0.7rem;{_LEVEL_COLOR.get(lvl, 'color:var(--text-2)')};word-break:break-all"
                    )

        with log_container:
            for entry in get_recent_logs():
                _render_entry(entry, log_container)

        def _scroll_bottom():
            ui.run_javascript(
                "const el = document.querySelector('.log-pane'); "
                "if(el) el.scrollTop = el.scrollHeight;"
            )

        def _on_new_entry(entry: dict) -> None:
            _render_entry(entry, log_container)

        register_ui_callback(_on_new_entry)
        app.on_disconnect(lambda: unregister_ui_callback(_on_new_entry))
        ui.timer(1.0, _scroll_bottom)

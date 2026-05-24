"""
ui/cues.py — QLab-style cue list panel.
"""

import asyncio
import base64
import json

from nicegui import ui, app

from config import ConfigManager
from cue_engine import CueEngine
from switcher_manager import SwitcherManager

_ACTS_STANDARD = {"none": "—", "program": "PGM", "preview": "PVW", "take": "Take", "cut": "Cut"}
_ACTS_ATEM     = {"none": "—", "macro": "Macro"}

_GO_ACTIVE = (
    "background:transparent !important;"
    "border:2px solid var(--ok) !important;"
    "box-shadow:0 0 20px rgba(74,222,128,0.45),inset 0 0 16px rgba(74,222,128,0.06) !important;"
    "color:var(--ok) !important;"
    "font-size:1.6rem !important;"
    "font-weight:900 !important;"
    "letter-spacing:0.22em !important;"
    "width:88px !important;height:76px !important;"
    "border-radius:8px !important;flex-shrink:0;"
)
_GO_INACTIVE = (
    "background:transparent !important;"
    "border:2px solid var(--border) !important;"
    "box-shadow:none !important;"
    "color:var(--border) !important;"
    "font-size:1.6rem !important;"
    "font-weight:900 !important;"
    "letter-spacing:0.22em !important;"
    "width:88px !important;height:76px !important;"
    "border-radius:8px !important;flex-shrink:0;"
)


def _action_opts(sw_type: str) -> dict:
    return _ACTS_ATEM if sw_type == "atem" else _ACTS_STANDARD


def _needs_preset(atype: str) -> bool:
    return atype in ("program", "preview", "macro")


def build_cues_panel(
    engine: CueEngine,
    manager: SwitcherManager,
    config: ConfigManager,
) -> None:

    # ── Outer wrapper — full width/height ─────────────────────────────
    outer = ui.element("div").style(
        "width:100%;display:flex;flex-direction:column;"
        "height:calc(100vh - 108px);"
    )

    with outer:

        # ── Control bar ───────────────────────────────────────────────
        with ui.element("div").style(
            "width:100%;display:flex;align-items:center;gap:16px;"
            "background:var(--s1);border-bottom:1px solid var(--border);"
            "padding:14px 20px;flex-shrink:0;"
        ):
            # GO button
            go_btn = ui.button(
                "GO", on_click=lambda: asyncio.create_task(_do_go())
            ).style(_GO_ACTIVE)

            # Cue info (next + current)
            with ui.element("div").style(
                "display:flex;flex-direction:column;justify-content:center;"
                "flex:1;min-width:0;gap:3px;"
            ):
                next_lbl = ui.label("Add cues to begin").style(
                    "font-size:1.05rem;font-weight:600;color:var(--text-2);"
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                )
                cur_lbl = ui.label("—").style(
                    "font-size:0.65rem;color:var(--text-3);"
                    "letter-spacing:0.09em;text-transform:uppercase;"
                )

            # Show name
            show_name_inp = ui.input(
                value=config.show_name,
                placeholder="Show name…",
            ).props("dense borderless").style(
                "width:180px;flex-shrink:0;"
                "font-size:0.78rem;color:var(--text-2);"
                "text-align:center;"
                "border-bottom:1px solid var(--border);"
            )
            show_name_inp.on(
                "blur",
                lambda _: setattr(config, "show_name", show_name_inp.value.strip() or "Untitled Show"),
            )

            # Transport + show controls
            with ui.element("div").style(
                "display:flex;align-items:center;gap:6px;flex-shrink:0;"
            ):
                ui.button(
                    "BACK", icon="skip_previous",
                    on_click=lambda: asyncio.create_task(_do_back()),
                ).props("no-caps").classes("cb-btn-sm cb-btn-ghost").style("min-width:68px")

                ui.button(
                    icon="replay",
                    on_click=lambda: _do_reset(),
                ).props("flat round dense").style(
                    "width:30px;height:30px;color:var(--text-3)"
                ).tooltip("Reset to start")

                ui.element("div").style(
                    "width:1px;height:24px;background:var(--border);margin:0 4px"
                )

                ui.button(
                    icon="save_alt",
                    on_click=lambda: _save_show(),
                ).props("flat round dense").style(
                    "width:30px;height:30px;color:var(--text-3)"
                ).tooltip("Save / export show")

                ui.button(
                    icon="folder_open",
                    on_click=lambda: _load_show(),
                ).props("flat round dense").style(
                    "width:30px;height:30px;color:var(--text-3)"
                ).tooltip("Load / import show")

        # ── Table (scrollable) ────────────────────────────────────────
        table_scroll = ui.element("div").style(
            "width:100%;flex:1;overflow:auto;"
        )
        with table_scroll:
            table = ui.element("div").style("min-width:600px;width:100%;")

        # ── Footer ────────────────────────────────────────────────────
        with ui.element("div").style(
            "width:100%;padding:6px 20px;"
            "border-top:1px solid var(--border);"
            "background:var(--bg);flex-shrink:0;"
        ):
            ui.button("+ Add Cue", on_click=lambda: _add_cue(-1)) \
                .props("no-caps flat").classes("cb-btn-sm cb-btn-ghost")

    # ── Render ────────────────────────────────────────────────────────

    def _render() -> None:
        table.clear()
        switchers = manager.all_switchers()
        cues      = engine.cues
        cur_idx   = engine.current_index

        with table:
            if not cues:
                with ui.element("div").style(
                    "padding:60px 20px;text-align:center;"
                    "color:var(--text-3);font-size:0.85rem;"
                ):
                    ui.label("No cues — click + Add Cue below to begin.")
                return

            _header_row(switchers)
            for i, cue in enumerate(cues):
                _cue_row(i, cue, switchers, cur_idx)

    def _header_row(switchers) -> None:
        with ui.element("div").style(
            "display:flex;align-items:center;"
            "background:var(--s2);border-bottom:2px solid var(--border);"
            "position:sticky;top:0;z-index:10;"
        ):
            _th("", w="28px")
            _th("#", w="56px", right=True)
            _th("Name", grow=True)
            for sw in switchers:
                _th(sw.name, w="230px", center=True)
            _th("Notes", grow=True)
            _th("", w="52px")

    def _th(
        text: str,
        w: str = "",
        grow: bool = False,
        right: bool = False,
        center: bool = False,
    ) -> None:
        align = "right" if right else "center" if center else "left"
        style = (
            f"text-align:{align};"
            f"{'flex:1;' if grow else f'width:{w};flex-shrink:0;'}"
            "padding:5px 8px;"
            "font-size:0.58rem;font-weight:700;letter-spacing:0.14em;"
            "text-transform:uppercase;color:var(--text-3);"
        )
        ui.label(text).style(style)

    def _cue_row(idx: int, cue: dict, switchers, cur_idx: int) -> None:
        cue_id  = cue["id"]
        is_cur  = idx == cur_idx
        is_next = idx == cur_idx + 1

        if is_cur:
            border = "var(--ok)"
            bg     = "rgba(74,222,128,0.07)"
            num_col = "var(--ok)"
        elif is_next:
            border = "var(--accent)"
            bg     = "rgba(224,123,58,0.05)"
            num_col = "var(--accent)"
        else:
            border = "transparent"
            bg     = "transparent" if idx % 2 == 0 else "rgba(255,255,255,0.018)"
            num_col = "var(--text-3)"

        row_el = ui.element("div").style(
            "display:flex;align-items:center;"
            f"border-left:3px solid {border};"
            f"background:{bg};"
            "border-bottom:1px solid var(--border-s);"
            "min-height:38px;"
            "transition:background 0.12s,border-color 0.12s;"
        )
        row_el.props(f'data-cue="{cue_id}"')

        with row_el:
            # State indicator
            with ui.element("div").style(
                "width:28px;flex-shrink:0;text-align:center;padding:0 4px;"
            ):
                if is_cur:
                    ui.html('<span style="color:var(--ok);font-size:0.72rem">▶</span>')
                elif is_next:
                    ui.html('<span style="color:var(--accent);font-size:0.58rem">▶</span>')

            # Cue number
            num = ui.input(value=cue.get("number", str(idx + 1))) \
                .props("dense borderless") \
                .style(
                    f"width:56px;flex-shrink:0;"
                    f"font-family:var(--mono);font-size:0.82rem;font-weight:600;"
                    f"color:{num_col};"
                )
            num.on("blur", lambda _, c=cue_id, n=num: engine.update_cue(c, {"number": n.value}))

            # Label
            lbl = ui.input(
                value=cue.get("label", ""),
                placeholder="Cue name…",
            ).props("dense borderless").style(
                "flex:1;min-width:0;font-size:0.85rem;color:var(--text);padding-left:6px;"
            )
            lbl.on("blur", lambda _, c=cue_id, l=lbl: engine.update_cue(c, {"label": l.value}))

            # Per-switcher action cells
            for sw in switchers:
                _action_cell(cue, sw)

            # Notes
            notes = ui.input(
                value=cue.get("notes", ""),
                placeholder="Notes…",
            ).props("dense borderless").style(
                "flex:1;min-width:80px;font-size:0.75rem;color:var(--text-3);"
            )
            notes.on("blur", lambda _, c=cue_id, n=notes: engine.update_cue(c, {"notes": n.value}))

            # Row buttons
            with ui.element("div").style(
                "width:52px;flex-shrink:0;display:flex;align-items:center;"
                "gap:1px;padding:0 4px;"
            ):
                ui.button(icon="add", on_click=lambda i=idx: _add_cue(i)) \
                    .props("dense flat round") \
                    .style("width:22px;height:22px;color:var(--text-3);font-size:13px") \
                    .tooltip("Insert cue after")
                ui.button(icon="close", on_click=lambda c=cue_id: _delete_cue(c)) \
                    .props("dense flat round") \
                    .style("width:22px;height:22px;color:var(--text-3);font-size:13px")

    def _action_cell(cue: dict, sw) -> None:
        sw_id   = sw.id
        sw_type = sw._config_type()
        action  = cue.get("actions", {}).get(sw_id, {})
        a_type  = action.get("type", "none")
        a_pid   = action.get("preset_id")
        opts    = _action_opts(sw_type)
        cue_id  = cue["id"]

        preset_opts: dict[str, str] = {"": "—"}
        for p in (sw.presets or []):
            preset_opts[str(p.id)] = p.name if p.name else str(p.id)

        cur_pid_str = str(a_pid) if a_pid is not None else ""
        # Saved preset may not be in list yet (device offline / still loading).
        # Add a placeholder so ui.select doesn't reject the value.
        if cur_pid_str and cur_pid_str not in preset_opts:
            preset_opts[cur_pid_str] = f"#{a_pid} (offline)"
        show = _needs_preset(a_type)

        with ui.element("div").style(
            "width:230px;flex-shrink:0;display:flex;align-items:center;gap:4px;"
            "padding:3px 8px;border-left:1px solid var(--border-s);"
        ):
            # Action type — fixed width, enough for "Macro"
            act_sel = ui.select(options=opts, value=a_type) \
                .props("dense outlined") \
                .style(
                    "width:88px;flex-shrink:0;"
                    "font-size:0.75rem;"
                )

            # Preset — fills remaining space
            prs_sel = ui.select(options=preset_opts, value=cur_pid_str) \
                .props("dense outlined") \
                .style("flex:1;min-width:0;font-size:0.75rem;")
            prs_sel.set_visibility(show)

            def _save_action(c=cue_id, s=sw_id, a=act_sel, p=prs_sel):
                needs = _needs_preset(a.value)
                p.set_visibility(needs)
                pid_str = p.value if needs else None
                if pid_str and str(pid_str).lstrip("-").isdigit():
                    pid: int | str | None = int(pid_str)
                else:
                    pid = pid_str if pid_str else None
                engine.set_action(c, s, a.value, pid)

            def _save_preset(c=cue_id, s=sw_id, a=act_sel, p=prs_sel):
                pid_str = p.value
                if pid_str and str(pid_str).lstrip("-").isdigit():
                    pid2: int | str | None = int(pid_str)
                else:
                    pid2 = pid_str if pid_str else None
                engine.set_action(c, s, a.value, pid2)

            act_sel.on("update:model-value", lambda _: _save_action())
            prs_sel.on("update:model-value", lambda _: _save_preset())

    # ── Header state ─────────────────────────────────────────────────

    def _update_header() -> None:
        cur   = engine.current_cue()
        nxt   = engine.next_cue()
        cues  = engine.cues
        at_end = bool(cues) and engine.current_index >= len(cues) - 1

        # Next line
        if nxt:
            num   = nxt.get("number", "")
            label = nxt.get("label", "")
            text  = f"{num}  ·  {label}" if label else f"Cue  {num}"
            next_lbl.set_text(text)
            next_lbl.style(
                "font-size:1.05rem;font-weight:600;color:var(--text);"
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            )
        elif at_end:
            next_lbl.set_text("End of list")
            next_lbl.style(
                "font-size:1.05rem;font-weight:600;color:var(--text-3);"
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            )
        elif not cues:
            next_lbl.set_text("Add cues to begin")
            next_lbl.style(
                "font-size:1.05rem;font-weight:600;color:var(--text-3);"
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            )
        else:
            next_lbl.set_text("Press GO to start")
            next_lbl.style(
                "font-size:1.05rem;font-weight:600;color:var(--text-2);"
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
            )

        # Current line
        if cur:
            num   = cur.get("number", "")
            label = cur.get("label", "")
            text  = f"NOW  {num}  {label}".strip() if label else f"NOW  Cue {num}"
            cur_lbl.set_text(text)
            cur_lbl.style(
                "font-size:0.65rem;color:var(--ok);"
                "letter-spacing:0.09em;text-transform:uppercase;"
            )
        else:
            cur_lbl.set_text("—")
            cur_lbl.style(
                "font-size:0.65rem;color:var(--text-3);"
                "letter-spacing:0.09em;text-transform:uppercase;"
            )

        go_btn.style(_GO_INACTIVE if at_end else _GO_ACTIVE)

    # ── Show save / load ─────────────────────────────────────────────

    def _save_show() -> None:
        raw  = config.export_cues()
        b64  = base64.b64encode(raw.encode()).decode()
        name = (config.show_name or "show").replace(" ", "_").replace("/", "-")
        ui.run_javascript(
            f"(function(){{"
            f"  var a=document.createElement('a');"
            f"  a.href='data:application/json;base64,{b64}';"
            f"  a.download='{name}.json';"
            f"  a.click();"
            f"}})()"
        )

    def _load_show() -> None:
        with ui.dialog().props("persistent") as dlg, \
                ui.card().style(
                    "background:var(--s2);min-width:500px;max-width:95vw;"
                    "padding:20px;gap:12px;"
                ):
            ui.label("Load Show").style(
                "font-size:0.9rem;font-weight:600;color:var(--text)"
            )
            ui.label(
                "Paste exported show JSON below. "
                "This replaces the current cue list."
            ).style("font-size:0.76rem;color:var(--text-3)")

            ta = ui.textarea(placeholder="Paste JSON here…") \
                .classes("w-full") \
                .props("rows=14 outlined") \
                .style("font-family:var(--mono);font-size:0.7rem;")

            def _do_import():
                ok = config.import_cues(ta.value)
                if ok:
                    engine.reset()
                    ui.notify("Show loaded.", type="positive")
                    dlg.close()
                    asyncio.create_task(_safe_render())
                else:
                    ui.notify("Invalid JSON — check format.", type="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dlg.close) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-ghost")
                ui.button("Load", icon="folder_open", on_click=_do_import) \
                    .props("no-caps").classes("cb-btn-sm cb-btn-accent")

        dlg.open()

    # ── Transport ────────────────────────────────────────────────────

    async def _do_go():
        cue, results = await engine.go()
        if cue:
            all_ok = not results or all(results.values())
            ui.notify(
                f"GO  {cue.get('number', '')}  {cue.get('label', '')}".strip(),
                type="positive" if all_ok else "warning",
                timeout=1200,
            )
            cid = cue["id"]
            ui.run_javascript(
                f"(function(){{var el=document.querySelector('[data-cue=\"{cid}\"]');"
                "if(el)el.scrollIntoView({behavior:'smooth',block:'nearest'});}})()"
            )
        else:
            ui.notify("End of cue list", type="info", timeout=900)
        await _safe_render()

    async def _do_back():
        cue, _ = await engine.back()
        if cue:
            ui.notify(
                f"BACK  {cue.get('number', '')}  {cue.get('label', '')}".strip(),
                type="info", timeout=1200,
            )
        await _safe_render()

    def _do_reset():
        engine.reset()
        asyncio.create_task(_safe_render())

    def _add_cue(after_index: int):
        engine.add_cue(after_index)
        asyncio.create_task(_safe_render())

    def _delete_cue(cue_id: str):
        engine.delete_cue(cue_id)
        asyncio.create_task(_safe_render())

    # ── Safe render: yield first so outbox finishes current iteration ──
    async def _safe_render():
        await asyncio.sleep(0)
        _render()
        _update_header()

    # ── Initial render ────────────────────────────────────────────────
    _render()
    _update_header()

    # ── Poll for OSC-triggered changes ────────────────────────────────
    _polled_index  = [engine.current_index]

    def _preset_fingerprint() -> int:
        return sum(len(sw.presets) for sw in manager.all_switchers())

    _polled_presets = [_preset_fingerprint()]

    async def _poll_engine():
        idx = engine.current_index
        fp  = _preset_fingerprint()
        if idx != _polled_index[0] or fp != _polled_presets[0]:
            _polled_index[0]  = idx
            _polled_presets[0] = fp
            await _safe_render()

    ui.timer(0.5, _poll_engine)

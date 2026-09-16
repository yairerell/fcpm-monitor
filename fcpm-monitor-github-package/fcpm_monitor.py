#!/usr/bin/env python3
"""
Clinical Deterioration Monitor — FCPM live simulation (standalone desktop app)

A tkinter port of the HTML/JS "patient monitor" simulation: a tablet-style
UI that animates toward a pulmonary hypertensive crisis, with per-channel
click-to-reveal descriptors, a TIRP recognition meter, and a flickering
PHTN badge. Pure standard library (tkinter) — no third-party packages
required to run it as a script, and it packages cleanly with PyInstaller
into a native double-clickable app (see the bottom of this file / the
README that ships alongside it for the one-line build command).

Run directly with:   python3 fcpm_monitor.py
"""

import tkinter as tk
from tkinter import font as tkfont
import time

# ---------------------------------------------------------------------------
# Palette (matches the HTML version's dark clinical-monitor look)
# ---------------------------------------------------------------------------
COL_SCREEN = "#05080a"
COL_BEZEL_TOP = "#24272a"
COL_BEZEL_BOT = "#101213"
COL_DESK = "#d7dde1"
COL_GRID = "#1c2226"
COL_GRID_STRONG = "#33393e"
COL_INK_BRIGHT = "#e8eef1"
COL_INK = "#8fa2ab"
COL_INK_DIM = "#57676e"
COL_LIVE = "#34e58a"
COL_CHIP_NORMAL_BG = "#14181a"
COL_CHIP_WARN_BG = "#3a2c17"
COL_CHIP_WARN_FG = "#f2b350"
COL_EVENT_MARKER = "#c9d3d8"
COL_DESC_BG = "#0c1417"

PHTN_GREEN = "#34e58a"
PHTN_AMBER = "#f2b350"
PHTN_RED = "#ef5757"

# ---------------------------------------------------------------------------
# Clinical parameters: 6-month-old, postop complete AVSD repair.
# detectT is the moment the stated deviation from baseline is crossed
# (minutes to event; negative = before event). Only detectT drives the
# visuals below — identical model to the HTML version.
#
#            baseline     onset(min)   rate/min      threshold                detectT
#  HR        120 bpm      -18          +8 bpm         144 bpm (+20%)           -15
#  CVP          7 mmHg    -14          +1 mmHg        10 mmHg (+3 pts)         -11
#  SBP        85 mmHg     -12          -1.4 mmHg      81 mmHg (-5%)             -9
#  SpO2       96 %         -8          -1.5 %/min     93 % (-3 pts)             -6
#  EtCO2      38 mmHg      -5          -2.5 mmHg      33 mmHg (-5 pts)          -3
#                                                        + widening PaCO2-EtCO2 gap (+5)
#  Lactate  serial draws, not continuous — abnormal once a value exceeds 2 mmol/L
# ---------------------------------------------------------------------------
CHANNELS = [
    {"id": "hr", "label": "HEART RATE", "arrow": "↑", "color": "#34e58a",
     "detect_t": -15, "row": 0,
     "descriptor": ("HEART RATE ↑", ["20% rise from baseline", "120 → 144 bpm"])},
    {"id": "spo2", "label": "SpO₂", "arrow": "↓", "color": "#4fa8f2",
     "detect_t": -6, "row": 1,
     "descriptor": ("SpO₂ ↓", ["3 point decrease from baseline", "96 → 93%"])},
    {"id": "etco2", "label": "EtCO₂", "arrow": "↓", "color": "#f2d94e",
     "detect_t": -3, "row": 2,
     "descriptor": ("EtCO₂ ↓", ["5 point decrease + gap widening", "38 → 33 mmHg, gap +5"])},
    {"id": "sbp", "label": "SYSTOLIC BP", "arrow": "↓", "color": "#ef5757",
     "detect_t": -9, "row": 3,
     "descriptor": ("SYSTOLIC BP ↓", ["5% decrease from baseline", "85 → 81 mmHg"])},
    {"id": "cvp", "label": "CVP", "arrow": "↑", "color": "#2a5fb0",
     "detect_t": -11, "row": 4,
     "descriptor": ("CVP ↑", ["3 point rise from baseline", "7 → 10 mmHg"])},
]

# Lactate: serial lab draws, not a continuous trend. Abnormal (red) once a
# value exceeds 2 mmol/L.
LACTATE_SAMPLES_T = [-13, -8, -4, -1]
LACTATE_VALUES = [1.0, 1.4, 2.3, 3.1]
LACTATE_ABNORMAL_FROM_INDEX = 2

DOMAIN_START = -19
DOMAIN_END = 2
BASE_SEC_PER_MIN = 2.0  # real seconds per simulated minute at 1x

TIRP_MIN_SOURCES = 2

FRAME_MS = 33  # ~30fps


def clamp01(v):
    return max(0.0, min(1.0, v))


def blend_over_screen(hex_color, alpha=0.18, screen_hex=COL_SCREEN):
    """Approximate the HTML version's color-mix(channel 18%, transparent)
    translucent fill by pre-blending the channel color over the screen
    background — tk has no alpha-blended fills, and an empty fill would
    make the bar's interior click-through (only its outline would be
    hit-testable), so every bar needs a real, opaque fill color."""
    fg = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    bg = tuple(int(screen_hex[i:i + 2], 16) for i in (1, 3, 5))
    blended = tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))
    return "#%02x%02x%02x" % blended


class FCPMMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Clinical Deterioration Monitor")
        self.configure(bg=COL_DESK)
        self.geometry("980x760")
        self.minsize(720, 560)

        self.mono_font = self._pick_mono_font()
        self.mono_bold = self._pick_mono_font(bold=True)
        self.sans_font = self._pick_sans_font()

        # ---- state ----
        self.sim_t = DOMAIN_START
        self.running = False
        self.finished = False
        self.last_tick = None
        self.speed = 1
        self.descriptor_open_for = None  # dict with x,y,color,title,lines
        self._flicker_on = True

        self._build_layout()
        self._layout_geometry_pending = True
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.after(50, self._first_layout)
        self._tick_flicker()

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------
    def _pick_mono_font(self, bold=False):
        families = set(tkfont.families())
        for name in ("Menlo", "Consolas", "DejaVu Sans Mono", "Courier New", "Courier"):
            if name in families:
                return tkfont.Font(family=name, size=10, weight="bold" if bold else "normal")
        return tkfont.Font(family="TkFixedFont", size=10, weight="bold" if bold else "normal")

    def _pick_sans_font(self):
        families = set(tkfont.families())
        for name in ("Helvetica Neue", "Segoe UI", "DejaVu Sans", "Helvetica", "Arial"):
            if name in families:
                return tkfont.Font(family=name, size=11)
        return tkfont.Font(family="TkDefaultFont", size=11)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self):
        outer = tk.Frame(self, bg=COL_DESK)
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        label = tk.Frame(outer, bg=COL_DESK)
        label.pack(fill="x", pady=(0, 12))
        tk.Label(label, text="FCPM · LIVE SIMULATION · STANDALONE",
                  font=self.mono_font, fg="#7a8087", bg=COL_DESK).pack()
        tk.Label(label,
                  text="6-month-old, postop complete AVSD repair · heart rate, "
                       "pulse oximetry, EtCO₂, systolic BP, CVP, lactate",
                  font=self.sans_font, fg="#8b9096", bg=COL_DESK).pack(pady=(2, 0))

        # "tablet" bezel
        bezel = tk.Frame(outer, bg=COL_BEZEL_BOT, bd=0)
        bezel.pack(fill="both", expand=True)
        bezel.configure(highlightthickness=0)
        screen_wrap = tk.Frame(bezel, bg=COL_BEZEL_BOT)
        screen_wrap.pack(fill="both", expand=True, padx=16, pady=16)

        self.screen = tk.Frame(screen_wrap, bg=COL_SCREEN)
        self.screen.pack(fill="both", expand=True)

        # ---- status bar ----
        status = tk.Frame(self.screen, bg=COL_SCREEN, height=52)
        status.pack(fill="x", side="top")
        status.pack_propagate(False)

        left = tk.Frame(status, bg=COL_SCREEN)
        left.pack(side="left", padx=18)
        self.live_dot = tk.Canvas(left, width=10, height=10, bg=COL_SCREEN,
                                    highlightthickness=0)
        self.live_dot.pack(side="left", pady=2)
        self.live_dot_id = self.live_dot.create_oval(2, 2, 9, 9, fill=COL_INK_DIM, outline="")
        tk.Label(left, text="  PATIENT #4821", font=self.mono_font,
                  fg=COL_INK, bg=COL_SCREEN).pack(side="left")

        right = tk.Frame(status, bg=COL_SCREEN)
        right.pack(side="right", padx=18)
        self.chip = tk.Label(right, text=" NORMAL ", font=self.mono_font,
                               fg=COL_INK, bg=COL_CHIP_NORMAL_BG, padx=8, pady=3)
        self.chip.pack(side="left", padx=(0, 14))

        alert_stack = tk.Frame(right, bg=COL_SCREEN)
        alert_stack.pack(side="left")
        self.phtn_label = tk.Label(alert_stack, text="PHTN", font=self.mono_bold,
                                     fg=COL_INK_DIM, bg=COL_SCREEN)
        # not packed yet — _set_phtn_visible() below controls when it appears
        self.phtn_visible = None

        self.tirp_row = tk.Frame(alert_stack, bg=COL_SCREEN)
        self.tirp_label = tk.Label(self.tirp_row, text="TIRP", font=self.mono_font,
                                     fg=COL_INK_DIM, bg=COL_SCREEN)
        self.tirp_label.pack(side="left", padx=(0, 6))
        self.tirp_track = tk.Canvas(self.tirp_row, width=64, height=6, bg="#14181a",
                                      highlightthickness=0)
        self.tirp_track.pack(side="left")
        self.tirp_fill_id = self.tirp_track.create_rectangle(0, 0, 0, 6, width=0, fill=PHTN_GREEN)
        # not packed yet — _set_tirp_visible() below controls when it appears
        self.tirp_meter_visible = None
        self._set_tirp_visible(False)
        self._set_phtn_visible(False)

        # ---- trace canvas ----
        canvas_wrap = tk.Frame(self.screen, bg=COL_SCREEN)
        canvas_wrap.pack(fill="both", expand=True, padx=0, pady=0)
        self.canvas = tk.Canvas(canvas_wrap, bg=COL_SCREEN, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # ---- start overlay ----
        # tk has no alpha-blended widget backgrounds, so we approximate the
        # HTML version's translucent scrim with a plain dark fill instead
        self.overlay = tk.Frame(self.canvas, bg="#060a0c")
        self.start_btn_canvas = tk.Canvas(self.overlay, width=64, height=64,
                                            bg=COL_SCREEN, highlightthickness=0)
        self._draw_start_button()
        self.start_btn_canvas.pack(pady=(30, 10))
        self.start_btn_canvas.bind("<Button-1>", lambda e: self._start_or_toggle())
        tk.Label(self.overlay, text="PRESS PLAY TO BEGIN", font=self.mono_font,
                  fg=COL_INK, bg=COL_SCREEN).pack()
        self.overlay_window = None  # placed lazily once canvas has a size

        # ---- transport bar ----
        transport = tk.Frame(self.screen, bg=COL_SCREEN, height=54)
        transport.pack(fill="x", side="bottom")
        transport.pack_propagate(False)
        center = tk.Frame(transport, bg=COL_SCREEN)
        center.pack(expand=True)

        btn_kwargs = dict(font=self.mono_font, fg=COL_INK_BRIGHT, bg="#1b2226",
                            activebackground="#262f34", activeforeground=COL_INK_BRIGHT,
                            bd=0, relief="flat", padx=14, pady=7, cursor="hand2")
        self.play_btn = tk.Button(center, text="▶ PLAY", command=self._start_or_toggle,
                                    **btn_kwargs)
        self.play_btn.pack(side="left", padx=4)
        self.reset_btn = tk.Button(center, text="↺ RESET", command=self._reset_all,
                                     **btn_kwargs)
        self.reset_btn.pack(side="left", padx=4)

        speed_frame = tk.Frame(center, bg=COL_SCREEN)
        speed_frame.pack(side="left", padx=(14, 4))
        self.speed_buttons = {}
        for s in (1, 2, 4):
            b = tk.Button(speed_frame, text=f"{s}×", font=self.mono_font,
                           fg=COL_INK_DIM, bg="#1b2226", activebackground="#262f34",
                           bd=0, relief="flat", padx=10, pady=7, cursor="hand2",
                           command=lambda s=s: self._set_speed(s))
            b.pack(side="left", padx=2)
            self.speed_buttons[s] = b
        self._highlight_speed()

        # ---- descriptor popover (a Toplevel-less canvas overlay) ----
        self.descriptor_frame = tk.Frame(self.canvas, bg=COL_DESC_BG, bd=0,
                                            highlightthickness=1,
                                            highlightbackground=COL_INK_DIM)
        self.descriptor_title_lbl = tk.Label(self.descriptor_frame, font=self.mono_bold,
                                               bg=COL_DESC_BG, fg=COL_INK_BRIGHT,
                                               anchor="w", justify="left")
        self.descriptor_title_lbl.pack(fill="x", padx=8, pady=(6, 2), anchor="w")
        self.descriptor_body_lbl = tk.Label(self.descriptor_frame, font=self.mono_font,
                                              bg=COL_DESC_BG, fg=COL_INK_BRIGHT,
                                              anchor="w", justify="left")
        self.descriptor_body_lbl.pack(fill="x", padx=8, pady=(0, 8), anchor="w")
        self.descriptor_window = None

        # canvas item registries, populated on first layout
        self.channel_items = {}   # id -> dict of canvas item ids + geometry
        self.lactate_items = []
        self.gridline_ids = []
        self.tick_label_ids = []
        self.event_line_id = None
        self.event_tag_id = None
        self.playhead_id = None
        self.axis_line_id = None
        self.axis_caption_id = None

    def _draw_start_button(self):
        c = self.start_btn_canvas
        c.delete("all")
        c.create_oval(2, 2, 62, 62, fill="#173325", outline="#2f7a52")
        c.create_polygon(24, 18, 24, 46, 48, 32, fill=COL_LIVE, outline="")

    # ------------------------------------------------------------------
    # First layout / resize handling — canvas coordinate system mirrors
    # the HTML version's SVG viewBox (900x360), scaled to fit.
    # ------------------------------------------------------------------
    VB_W, VB_H = 900, 360
    X_LEFT, X_RIGHT = 24, 876
    LACTATE_ROW_CENTER = 290
    LACTATE_HALF_H = 13
    LACTATE_W = 7
    ROW_Y = [40, 90, 140, 190, 240]  # HR, SpO2, EtCO2, SBP, CVP row centers
    ROW_HALF_H = 6.5

    def _first_layout(self):
        self._build_scene()
        self._layout_geometry_pending = False
        self._reset_all()

    def _on_canvas_resize(self, event):
        if self._layout_geometry_pending:
            return
        self._rescale_scene()

    def _scale(self):
        w = max(self.canvas.winfo_width(), 1)
        h = max(self.canvas.winfo_height(), 1)
        return w / self.VB_W, h / self.VB_H, w, h

    def _sx(self, x):
        sx, sy, w, h = self._scale()
        return x * sx

    def _sy(self, y):
        sx, sy, w, h = self._scale()
        return y * sy

    def _x_of_t(self, t):
        ppm = (self.X_RIGHT - self.X_LEFT) / (DOMAIN_END - DOMAIN_START)
        return self.X_LEFT + (t - DOMAIN_START) * ppm

    # ------------------------------------------------------------------
    # Build all canvas items once; positions get rescaled on resize.
    # ------------------------------------------------------------------
    def _build_scene(self):
        c = self.canvas
        c.delete("all")

        grid_ts = [-15, -10, -5, 0]
        self.gridline_ids = []
        self.tick_label_ids = []
        for t in grid_ts:
            strong = (t == 0)
            line_id = c.create_line(0, 0, 0, 0,
                                      fill=COL_GRID_STRONG if strong else COL_GRID,
                                      dash=(4, 4) if strong else None, width=1)
            self.gridline_ids.append((t, line_id, strong))
            txt = "0" if t == 0 else f"−{abs(t)}"
            lbl_id = c.create_text(0, 0, text=txt, fill=COL_INK_DIM,
                                     font=self.mono_font, anchor="n")
            self.tick_label_ids.append((t, lbl_id))

        self.event_tag_id = c.create_text(0, 0, text="EVENT", fill=COL_EVENT_MARKER,
                                            font=self.mono_font, anchor="ne")

        # channel rows
        self.channel_items = {}
        for ch in CHANNELS:
            row_y = self.ROW_Y[ch["row"]]
            line_id = c.create_line(0, 0, 0, 0, fill=ch["color"], width=2)
            fill_color = blend_over_screen(ch["color"])
            bar_id = c.create_rectangle(0, 0, 0, 0, fill=fill_color, outline=ch["color"],
                                          width=1.6, state="hidden")
            dot_id = c.create_oval(0, 0, 0, 0, fill=ch["color"], outline="",
                                     state="hidden")
            self.channel_items[ch["id"]] = {
                "line": line_id, "bar": bar_id, "dot": dot_id,
                "row_y": row_y, "detect_t": ch["detect_t"], "color": ch["color"],
                "fill_color": fill_color,
                "descriptor": ch["descriptor"], "detected": False,
            }
            if ch["descriptor"]:
                c.tag_bind(bar_id, "<Button-1>",
                            lambda e, cid=ch["id"]: self._on_channel_click(cid, e))
                c.tag_bind(bar_id, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(bar_id, "<Leave>", lambda e: c.configure(cursor=""))

        # lactate row
        self.lactate_items = []
        for i, t in enumerate(LACTATE_SAMPLES_T):
            abnormal = i >= LACTATE_ABNORMAL_FROM_INDEX
            color = "#ef5757" if abnormal else "#34e58a"
            bar_id = c.create_rectangle(0, 0, 0, 0, fill=color, outline=color,
                                          width=1.2, state="hidden")
            item = {"bar": bar_id, "t": t, "value": LACTATE_VALUES[i],
                     "abnormal": abnormal, "revealed": False}
            self.lactate_items.append(item)
            if abnormal:
                c.tag_bind(bar_id, "<Button-1>",
                            lambda e, idx=i: self._on_lactate_click(idx, e))
                c.tag_bind(bar_id, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(bar_id, "<Leave>", lambda e: c.configure(cursor=""))

        self.axis_line_id = c.create_line(0, 0, 0, 0, fill=COL_GRID_STRONG, width=1)
        self.axis_caption_id = c.create_text(0, 0, text="MINUTES TO EVENT",
                                                fill=COL_INK_DIM, font=self.mono_font,
                                                anchor="nw")
        self.playhead_id = c.create_line(0, 0, 0, 0, fill="#dfe6e9", width=1.4)

        self._rescale_scene()

        # place overlay + descriptor windows (created once)
        self.overlay_window = c.create_window(0, 0, window=self.overlay, anchor="nw")

    def _rescale_scene(self):
        c = self.canvas
        sx, sy, w, h = self._scale()

        for t, line_id, strong in self.gridline_ids:
            x = self._x_of_t(t) * sx
            c.coords(line_id, x, 8 * sy, x, 320 * sy)
        for t, lbl_id in self.tick_label_ids:
            x = self._x_of_t(t) * sx
            c.coords(lbl_id, x, 328 * sy)

        c.coords(self.event_tag_id, self._x_of_t(0) * sx - 6, 12 * sy)

        for ch_id, item in self.channel_items.items():
            row_y = item["row_y"] * sy
            # geometry recomputed each frame in _render() too; here we just
            # keep the resting (t=DOMAIN_START) look consistent on resize
            c.coords(item["line"], self.X_LEFT * sx, row_y, self.X_LEFT * sx, row_y)

        c.coords(self.axis_line_id, self.X_LEFT * sx, 320 * sy, self.X_RIGHT * sx, 320 * sy)
        c.coords(self.axis_caption_id, self.X_LEFT * sx, 340 * sy)
        c.coords(self.playhead_id, self.X_LEFT * sx, 8 * sy, self.X_LEFT * sx, 320 * sy)

        if self.overlay_window is not None:
            c.coords(self.overlay_window, 0, 0)
            c.itemconfig(self.overlay_window, width=w, height=h)

        self._render()

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------
    def _on_canvas_click(self, event):
        # If the click landed on a clickable bar, that item's own handler
        # (bound via tag_bind) owns opening/closing the descriptor — this
        # generic handler only closes it for clicks elsewhere on the canvas.
        current = self.canvas.find_withtag("current")
        clickable_ids = {info["bar"] for info in self.channel_items.values()
                          if info["descriptor"]}
        clickable_ids |= {li["bar"] for li in self.lactate_items if li["abnormal"]}
        if current and current[0] in clickable_ids:
            return
        if self.descriptor_open_for is not None:
            self._hide_descriptor()

    def _on_channel_click(self, ch_id, event):
        item = self.channel_items[ch_id]
        if self.sim_t < item["detect_t"]:
            return
        if self.descriptor_open_for == ("channel", ch_id):
            self._hide_descriptor()
        else:
            title, lines = item["descriptor"]
            sx, sy, w, h = self._scale()
            x = self._x_of_t(item["detect_t"]) * sx
            y = item["row_y"] * sy
            self._show_descriptor(("channel", ch_id), x, y, item["color"], title, lines)

    def _on_lactate_click(self, idx, event):
        item = self.lactate_items[idx]
        if self.sim_t < item["t"]:
            return
        key = ("lactate", idx)
        if self.descriptor_open_for == key:
            self._hide_descriptor()
        else:
            sx, sy, w, h = self._scale()
            x = self._x_of_t(item["t"]) * sx
            y = self.LACTATE_ROW_CENTER * sy
            title = "LACTATE ↑"
            lines = ["Rise above 2 mmol/L", f"{item['value']:.1f} mmol/L"]
            self._show_descriptor(key, x, y, "#ef5757", title, lines)

    def _show_descriptor(self, key, x, y, color, title, lines):
        self.descriptor_open_for = key
        self.descriptor_frame.configure(highlightbackground=color, highlightcolor=color,
                                          highlightthickness=1)
        self.descriptor_title_lbl.configure(text=title, fg=color)
        self.descriptor_body_lbl.configure(text="\n".join(lines))
        if self.descriptor_window is None:
            self.descriptor_window = self.canvas.create_window(
                x + 12, y, window=self.descriptor_frame, anchor="w")
        else:
            self.canvas.coords(self.descriptor_window, x + 12, y)
            self.canvas.itemconfig(self.descriptor_window, state="normal")
        self.canvas.tag_raise(self.descriptor_window)

    def _hide_descriptor(self):
        self.descriptor_open_for = None
        if self.descriptor_window is not None:
            self.canvas.itemconfig(self.descriptor_window, state="hidden")

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _set_speed(self, s):
        self.speed = s
        self._highlight_speed()

    def _highlight_speed(self):
        for s, b in self.speed_buttons.items():
            if s == self.speed:
                b.configure(fg=COL_LIVE, bg="#12321f")
            else:
                b.configure(fg=COL_INK_DIM, bg="#1b2226")

    def _start_or_toggle(self):
        if self.finished:
            self._reset_all()
            self.running = True
            self.finished = False
            self._hide_overlay()
            self.play_btn.configure(text="⏸ PAUSE")
            return
        self.running = not self.running
        self.play_btn.configure(text="⏸ PAUSE" if self.running else "▶ PLAY")
        self._hide_overlay()

    def _hide_overlay(self):
        if self.overlay_window is not None:
            self.canvas.itemconfig(self.overlay_window, state="hidden")

    def _show_overlay(self):
        if self.overlay_window is not None:
            self.canvas.itemconfig(self.overlay_window, state="normal")

    def _reset_all(self):
        self.sim_t = DOMAIN_START
        self.running = False
        self.finished = False
        self.last_tick = None
        for item in self.channel_items.values():
            item["detected"] = False
            self.canvas.itemconfig(item["dot"], state="hidden")
            self.canvas.itemconfig(item["bar"], state="hidden")
        for item in self.lactate_items:
            item["revealed"] = False
            self.canvas.itemconfig(item["bar"], state="hidden")
        self._set_tirp_visible(False)
        self._set_phtn_visible(False)
        self._hide_descriptor()
        self._show_overlay()
        self.play_btn.configure(text="▶ PLAY")
        self._render()

    def _finish(self):
        self.running = False
        self.finished = True
        self.play_btn.configure(text="↺ REPLAY")
        self._render()

    # ------------------------------------------------------------------
    # TIRP / PHTN visibility helpers
    # ------------------------------------------------------------------
    def _set_tirp_visible(self, visible):
        if visible == self.tirp_meter_visible:
            return
        self.tirp_meter_visible = visible
        if visible:
            self.tirp_row.pack(anchor="e", pady=(2, 0))
        else:
            self.tirp_row.pack_forget()

    def _set_phtn_visible(self, visible):
        if visible == self.phtn_visible:
            return
        self.phtn_visible = visible
        if visible:
            self.phtn_label.pack(anchor="e")
        else:
            self.phtn_label.pack_forget()

    def _tick_flicker(self):
        # hard on/off flicker for the PHTN badge, ~1s period
        self._flicker_on = not self._flicker_on
        if self.phtn_visible:
            color = getattr(self, "_phtn_color", COL_INK_DIM)
            self.phtn_label.configure(fg=color if self._flicker_on else COL_SCREEN)
        self.after(500, self._tick_flicker)

    # ------------------------------------------------------------------
    # Main render — mirrors the HTML version's render() function.
    # ------------------------------------------------------------------
    def _render(self):
        sx, sy, w, h = self._scale()
        c = self.canvas
        px = self._x_of_t(self.sim_t) * sx
        c.coords(self.playhead_id, px, 8 * sy, px, 320 * sy)

        any_detected = False
        for ch_id, item in self.channel_items.items():
            detect_t = item["detect_t"]
            detected = self.sim_t >= detect_t
            item["detected"] = detected
            if detected:
                any_detected = True

            row_y = item["row_y"] * sy
            x_left = self.X_LEFT * sx
            detect_x = self._x_of_t(detect_t) * sx

            line_end = min(px, detect_x)
            c.coords(item["line"], x_left, row_y, max(x_left, line_end), row_y)

            if detected:
                bar_w = max(0.0, px - detect_x)
                half_h = self.ROW_HALF_H * sy
                c.coords(item["bar"], detect_x, row_y - half_h,
                          detect_x + bar_w, row_y + half_h)
                c.itemconfig(item["bar"], state="normal")
                dot_r = 3 * ((sx + sy) / 2)
                c.coords(item["dot"], detect_x - dot_r, row_y - dot_r,
                          detect_x + dot_r, row_y + dot_r)
                c.itemconfig(item["dot"], state="normal")
                if item["descriptor"]:
                    c.itemconfig(item["bar"], width=1.6)
                    c.tag_raise(item["bar"])
            else:
                c.itemconfig(item["bar"], state="hidden")
                c.itemconfig(item["dot"], state="hidden")
                if self.descriptor_open_for == ("channel", ch_id):
                    self._hide_descriptor()

        self.chip.configure(
            text=" TREND DETECTED " if any_detected else " NORMAL ",
            fg=COL_CHIP_WARN_FG if any_detected else COL_INK,
            bg=COL_CHIP_WARN_BG if any_detected else COL_CHIP_NORMAL_BG,
        )

        # lactate bars
        for i, item in enumerate(self.lactate_items):
            revealed = self.sim_t >= item["t"]
            item["revealed"] = revealed
            cx = self._x_of_t(item["t"]) * sx
            half_w = (self.LACTATE_W / 2) * sx
            if revealed:
                half_h = self.LACTATE_HALF_H * sy
                cy = self.LACTATE_ROW_CENTER * sy
                c.coords(item["bar"], cx - half_w, cy - half_h, cx + half_w, cy + half_h)
                c.itemconfig(item["bar"], state="normal")
            else:
                c.itemconfig(item["bar"], state="hidden")
                if self.descriptor_open_for == ("lactate", i):
                    self._hide_descriptor()

        lactate_abnormal_ts = [LACTATE_SAMPLES_T[i] for i in range(len(LACTATE_SAMPLES_T))
                                 if i >= LACTATE_ABNORMAL_FROM_INDEX]
        qualifying_channels = sum(1 for ch in self.channel_items.values()
                                    if self.sim_t >= ch["detect_t"])
        qualifying_lactate = sum(1 for t in lactate_abnormal_ts if self.sim_t >= t)
        qualifying = qualifying_channels + qualifying_lactate

        all_source_ts = sorted([ch["detect_t"] for ch in self.channel_items.values()]
                                 + lactate_abnormal_ts)
        total_sources = len(all_source_ts)
        tirp_active = qualifying >= TIRP_MIN_SOURCES

        self._set_tirp_visible(tirp_active)
        if tirp_active:
            tirp_start_t = all_source_ts[TIRP_MIN_SOURCES - 1]
            channel_frac = clamp01((qualifying - TIRP_MIN_SOURCES) /
                                     max(1e-9, (total_sources - TIRP_MIN_SOURCES)))
            time_frac = clamp01((self.sim_t - tirp_start_t) / max(1e-9, (0 - tirp_start_t)))
            score = clamp01(0.5 * channel_frac + 0.5 * time_frac)
            track_w = 64
            self.tirp_track.coords(self.tirp_fill_id, 0, 0, track_w * score, 6)
            if score < 0.34:
                fill_color = PHTN_GREEN
            elif score < 0.67:
                fill_color = PHTN_AMBER
            else:
                fill_color = PHTN_RED
            self.tirp_track.itemconfig(self.tirp_fill_id, fill=fill_color)
            self._phtn_color = fill_color
            self._set_phtn_visible(True)
        else:
            self.tirp_track.coords(self.tirp_fill_id, 0, 0, 0, 6)
            self._set_phtn_visible(False)

        led_color = COL_LIVE if self.running else COL_INK_DIM
        self.live_dot.itemconfig(self.live_dot_id, fill=led_color)

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------
    def run_loop(self):
        now = time.monotonic()
        if self.running:
            if self.last_tick is None:
                self.last_tick = now
            dt = now - self.last_tick
            self.last_tick = now
            sec_per_min = BASE_SEC_PER_MIN / self.speed
            self.sim_t += dt / sec_per_min
            if self.sim_t >= DOMAIN_END:
                self.sim_t = DOMAIN_END
                self._render()
                self._finish()
                self.after(FRAME_MS, self.run_loop)
                return
            self._render()
        else:
            self.last_tick = None
        self.after(FRAME_MS, self.run_loop)


def main():
    app = FCPMMonitor()
    app.after(60, app.run_loop)
    app.mainloop()


if __name__ == "__main__":
    main()

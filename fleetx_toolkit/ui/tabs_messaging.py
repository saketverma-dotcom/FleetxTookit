import threading
import tkinter as tk
from tkinter import ttk, messagebox

import requests
from ..http import session

from ..config import (MESSAGING_SIMS, MESSAGING_SIM_NAMES, MESSAGING_POLL_SECONDS,
                      sim_id_for_name)
from ..storage import load_sms_token
from .. import messaging as M


class MessagingTabMixin:
    """Two-way SMS console (v3.5). Polls one selected SIM's inbox and lets you
    reply in-thread from that same SIM. Restricted to Airtel Pulse / Voda Pulse."""

    # ── Pulse-inspired palette ──
    C_BG      = "#ffffff"
    C_SIDEBAR = "#fafafa"
    C_SEL     = "#e8eaf6"
    C_DIVIDER = "#eeeeee"
    C_MUTED   = "#9e9e9e"
    C_TEXT    = "#212121"
    C_ACCENT  = "#ff5722"
    C_UNREAD  = "#2196f3"

    def _tab_messaging(self):
        tab = tk.Frame(self.nb, bg=self.C_BG)
        self.nb.add(tab, text="Messaging")

        # runtime state
        self._msg_threads = {}
        self._msg_last_id = 0
        self._msg_active_phone = None
        self._msg_polling = False
        self._msg_device_id = sim_id_for_name(MESSAGING_SIM_NAMES[0])
        self._msg_pending_status = {}
        self._msg_unread = set()          # phones with unread incoming
        self._msg_query = ""
        self._msg_row_index = []          # row order -> phone, for click mapping

        # ── top bar ──
        topbar = tk.Frame(tab, bg=self.C_BG, height=48)
        topbar.pack(fill="x"); topbar.pack_propagate(False)
        tk.Label(topbar, text="  Messaging", bg=self.C_BG, fg=self.C_TEXT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        self.msg_sim = tk.StringVar(value=MESSAGING_SIM_NAMES[0])
        sim_cb = ttk.Combobox(topbar, textvariable=self.msg_sim, width=14,
                              state="readonly", values=MESSAGING_SIM_NAMES)
        sim_cb.pack(side="left", padx=10)
        sim_cb.bind("<<ComboboxSelected>>", lambda e: self._msg_switch_sim())
        self.msg_toggle_btn = tk.Button(topbar, text="▶ Start", relief="flat",
                                        bg=self.C_UNREAD, fg="white", bd=0,
                                        padx=12, pady=4, cursor="hand2",
                                        command=self._msg_toggle)
        self.msg_toggle_btn.pack(side="left", padx=4)
        self.msg_status = tk.Label(topbar, text="Stopped", bg=self.C_BG,
                                   fg=self.C_MUTED, font=("Segoe UI", 9))
        self.msg_status.pack(side="left", padx=8)
        # search box
        self.msg_search = tk.StringVar()
        se = tk.Entry(topbar, textvariable=self.msg_search, width=22,
                      relief="flat", bg="#f1f1f1")
        se.pack(side="right", padx=10, ipady=3)
        se.insert(0, ""); 
        self.msg_search.trace_add("write", lambda *a: self._msg_apply_filter())

        tk.Frame(tab, bg=self.C_DIVIDER, height=1).pack(fill="x")

        # ── body: sidebar | conversation list | thread ──
        body = tk.Frame(tab, bg=self.C_BG)
        body.pack(fill="both", expand=True)

        # sidebar
        side = tk.Frame(body, bg=self.C_SIDEBAR, width=170)
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        conv_lbl = tk.Label(side, text="  ▤  Conversations", bg=self.C_SEL,
                            fg=self.C_TEXT, font=("Segoe UI", 10), anchor="w",
                            padx=12, pady=10)
        conv_lbl.pack(fill="x", pady=(6, 0), padx=6)
        tk.Button(side, text="✚  New message", relief="flat", bg=self.C_ACCENT,
                  fg="white", bd=0, anchor="w", padx=12, pady=8, cursor="hand2",
                  command=self._msg_new_message).pack(fill="x", pady=(10, 0), padx=6)

        # conversation list (scrollable)
        listwrap = tk.Frame(body, bg=self.C_BG, width=300)
        listwrap.pack(side="left", fill="y"); listwrap.pack_propagate(False)
        self.msg_canvas = tk.Canvas(listwrap, bg=self.C_BG, highlightthickness=0,
                                    width=300)
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.msg_canvas.yview)
        self.msg_rows = tk.Frame(self.msg_canvas, bg=self.C_BG)
        self._msg_rows_window = self.msg_canvas.create_window(
            (0, 0), window=self.msg_rows, anchor="nw", width=300)
        self.msg_rows.bind("<Configure>", lambda e: self.msg_canvas.configure(
            scrollregion=self.msg_canvas.bbox("all")))
        self.msg_canvas.configure(yscrollcommand=sb.set)
        self.msg_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # mouse-wheel scrolling (bind on enter so it doesn't hijack the thread box)
        def _wheel(ev):
            self.msg_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")
        self.msg_canvas.bind("<Enter>",
                             lambda e: self.msg_canvas.bind_all("<MouseWheel>", _wheel))
        self.msg_canvas.bind("<Leave>",
                             lambda e: self.msg_canvas.unbind_all("<MouseWheel>"))

        tk.Frame(body, bg=self.C_DIVIDER, width=1).pack(side="left", fill="y")

        # thread pane
        right = tk.Frame(body, bg=self.C_BG)
        right.pack(side="left", fill="both", expand=True)
        self.msg_thread_lbl = tk.Label(right, text="Select a conversation",
                                       bg=self.C_BG, fg=self.C_TEXT,
                                       font=("Segoe UI", 11, "bold"), anchor="w",
                                       padx=12, pady=10)
        self.msg_thread_lbl.pack(fill="x")
        tk.Frame(right, bg=self.C_DIVIDER, height=1).pack(fill="x")

        # DPI FIX: the reply row is packed to the bottom FIRST so Tk reserves
        # its height. Previously the message area (expand=True) was packed
        # first and swallowed everything, pushing the reply box off-screen at
        # Windows display scaling of 125%/150%.
        rr = tk.Frame(right, bg=self.C_BG)
        rr.pack(side="bottom", fill="x", pady=6, padx=8)
        self.msg_reply = tk.StringVar()
        e = tk.Entry(rr, textvariable=self.msg_reply, relief="flat", bg="#f1f1f1")
        e.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        e.bind("<Return>", lambda ev: self._msg_send_reply())
        tk.Button(rr, text="Send", relief="flat", bg=self.C_UNREAD, fg="white",
                  bd=0, padx=16, pady=4, cursor="hand2",
                  command=self._msg_send_reply).pack(side="left")

        # message area fills whatever height is left above the reply row
        self.msg_thread_box = tk.Text(right, state="disabled", wrap="word",
                                      bg=self.C_BG, fg=self.C_TEXT, relief="flat",
                                      padx=12, pady=8, font=("Segoe UI", 10),
                                      height=6)
        self.msg_thread_box.pack(fill="both", expand=True)
        self.msg_thread_box.tag_config("in", foreground="#00695c", spacing3=6)
        self.msg_thread_box.tag_config("out", foreground="#1565c0",
                                       justify="right", spacing3=6)
        self.msg_thread_box.tag_config("meta", foreground=self.C_MUTED,
                                       font=("Segoe UI", 8))

    def _msg_apply_filter(self):
        self._msg_mark_activity()
        self._msg_query = self.msg_search.get()
        self._msg_force_redraw()
        self._msg_refresh_conv_list()

    def _msg_new_message(self):
        """Start a fresh conversation with any number (not just those who
        messaged us). Opens a small compose dialog; on send it creates the
        thread and routes through the normal reply path."""
        from ..sms import normalize_number
        dlg = tk.Toplevel(self)
        dlg.title("New message")
        dlg.transient(self); dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12); frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Send from: {self.msg_sim.get()}",
                  font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="To number:").grid(row=1, column=0, sticky="e", pady=6)
        num_var = tk.StringVar()
        num_e = ttk.Entry(frm, textvariable=num_var, width=28)
        num_e.grid(row=1, column=1, pady=6); num_e.focus_set()
        ttk.Label(frm, text="(with country code, e.g. +9198…)",
                  foreground="gray").grid(row=2, column=1, sticky="w")

        ttk.Label(frm, text="Message:").grid(row=3, column=0, sticky="ne", pady=6)
        msg_txt = tk.Text(frm, height=4, width=32, wrap="word")
        msg_txt.grid(row=3, column=1, pady=6)
        count_lbl = ttk.Label(frm, text="0 chars · 0 SMS", foreground="gray")
        count_lbl.grid(row=4, column=1, sticky="w")

        def upd_count(*_):
            n, seg = M.sms_segments(msg_txt.get("1.0", "end").rstrip("\n"))
            count_lbl.config(text=f"{n} chars · {seg} SMS")
        msg_txt.bind("<KeyRelease>", upd_count)

        btns = ttk.Frame(frm); btns.grid(row=5, column=0, columnspan=2, pady=(8, 0))

        def do_send():
            phone = normalize_number(num_var.get(), "")
            text = msg_txt.get("1.0", "end").strip()
            if not phone:
                messagebox.showerror("New message", "Enter a valid number.", parent=dlg); return
            if not text:
                messagebox.showerror("New message", "Enter a message.", parent=dlg); return
            if not self._current_sms_token():
                messagebox.showerror("New message",
                    "No SemySMS token available (not loaded from login).", parent=dlg); return
            # ensure a thread bucket exists and make it active, then reuse reply path
            self._msg_threads.setdefault(phone, [])
            self._msg_active_phone = phone
            self.msg_reply.set(text)
            dlg.destroy()
            self._msg_refresh_conv_list()
            self._msg_render_thread()
            self._msg_send_reply()

        ttk.Button(btns, text="Send", command=do_send).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left", padx=4)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _msg_switch_sim(self):
        self._msg_mark_activity()
        self._msg_device_id = sim_id_for_name(self.msg_sim.get())
        self._msg_threads = {}
        self._msg_last_id = 0
        self._msg_active_phone = None
        self._msg_pending_status = {}
        self._msg_unread = set()
        self._msg_force_redraw()
        self._msg_refresh_conv_list()
        self._msg_render_thread()
        if self._msg_polling:
            self.msg_status.config(text=f"Watching {self.msg_sim.get()}…", fg="green")

    def _msg_toggle(self):
        if self._msg_polling:
            self._msg_polling = False
            self.msg_toggle_btn.config(text="▶ Start")
            self.msg_status.config(text="Stopped", fg="gray")
            return
        if not self._current_sms_token():
            if True:
                self._ui_error("Messaging",
                    "No SemySMS token. Save it in the SMS Command tab first.")
                return
        self._msg_polling = True
        self._msg_idle_cycles = 0
        self._msg_fail_streak = 0
        self.msg_toggle_btn.config(text="⏸ Stop")
        self.msg_status.config(text=f"Watching {self.msg_sim.get()}…", fg="green")
        self._msg_mark_activity()
        self._msg_schedule_idle_check()
        threading.Thread(target=self._msg_poll_loop, daemon=True).start()

    # ── auto-stop after 2 minutes of no interaction with the tab ──
    MSG_IDLE_TIMEOUT = 120        # seconds

    def _msg_mark_activity(self):
        import time as _t
        self._msg_last_activity = _t.monotonic()

    def _msg_schedule_idle_check(self):
        # single recurring 5s check while polling; cancels itself when stopped
        if getattr(self, "_msg_idle_job", None):
            try:
                self.after_cancel(self._msg_idle_job)
            except Exception:
                pass
            self._msg_idle_job = None
        if not self._msg_polling:
            return
        import time as _t
        idle = _t.monotonic() - getattr(self, "_msg_last_activity", _t.monotonic())
        if idle >= self.MSG_IDLE_TIMEOUT:
            self._msg_polling = False
            self.msg_toggle_btn.config(text="▶ Start")
            self.msg_status.config(
                text="Auto-stopped (idle 2 min) — press Start to resume", fg="#e65100")
            return
        self._msg_idle_job = self.after(5000, self._msg_schedule_idle_check)

    def _msg_poll_loop(self):
        token = self._current_sms_token()
        while self._msg_polling:
            device = self._msg_device_id
            url, params = M.build_inbox_request(token, device, since_id=self._msg_last_id)
            try:
                r = session.get(url, params=params, timeout=30)
                msgs = M.parse_inbox(r.json())
                # recovered — clear any previous warning
                self._msg_fail_streak = 0
                self.after(0, self._msg_clear_error)
            except Exception as e:
                msgs = []
                self._msg_fail_streak = getattr(self, "_msg_fail_streak", 0) + 1
                # A single slow response is normal; only warn if it persists.
                if self._msg_fail_streak >= 2:
                    self.after(0, lambda e=e, n=self._msg_fail_streak:
                               self._msg_show_error(e, n))
            # ignore results if the user switched SIM mid-request
            fresh_count = 0
            if device == self._msg_device_id and msgs:
                fresh = M.new_since(msgs, self._msg_last_id)
                self._msg_last_id = M.max_id(msgs, self._msg_last_id)
                fresh_count = len(fresh)
                if fresh:
                    self.after(0, lambda f=fresh: self._msg_ingest(f))
            # poll delivery status for still-pending sent messages
            if device == self._msg_device_id and self._msg_pending_status:
                ids = list(self._msg_pending_status.keys())
                ourl, oparams = M.build_outbox_request(token, device, ids)
                try:
                    ro = session.get(ourl, params=oparams, timeout=30)
                    statuses = M.parse_outbox_status(ro.json())
                except Exception:
                    statuses = {}
                if statuses:
                    self.after(0, lambda s=statuses: self._msg_apply_status(s))

            # Adaptive backoff: stay fast (7s) while a conversation is live,
            # stretch to 15s/30s when nothing is arriving. Cuts idle API load.
            if fresh_count:
                self._msg_idle_cycles = 0
            else:
                self._msg_idle_cycles = getattr(self, "_msg_idle_cycles", 0) + 1
            wait_s = M.next_poll_interval(self._msg_idle_cycles)
            for _ in range(int(wait_s * 2)):
                if not self._msg_polling:
                    break
                threading.Event().wait(0.5)

    def _msg_show_error(self, exc, streak):
        """Amber warning with a plain-language cause; polling keeps running."""
        msg = M.friendly_error(exc)
        if streak >= 5:
            msg += f" ({streak} failed attempts)"
        self.msg_status.config(text=msg, fg="#e65100")

    def _msg_clear_error(self):
        """Back to normal once a poll succeeds — the old code left a red error
        on screen forever, so users couldn't tell it had recovered."""
        if self._msg_polling:
            self.msg_status.config(text=f"Watching {self.msg_sim.get()}…", fg="green")

    def _msg_ingest(self, fresh):
        M.add_messages(self._msg_threads, fresh, "in")
        for m in fresh:
            ph = m.get("phone", "").strip()
            if ph and ph != self._msg_active_phone:
                self._msg_unread.add(ph)
        self._msg_refresh_conv_list()
        if self._msg_active_phone in self._msg_threads:
            self._msg_render_thread()

    def _msg_apply_status(self, statuses):
        """Update outgoing message status from an outbox poll; stop tracking
        ones that reached a terminal state (delivered/failed/cancelled)."""
        changed = False
        for phone, msgs in self._msg_threads.items():
            for msg in msgs:
                if msg.get("dir") == "out" and msg.get("id") in statuses:
                    new = statuses[msg["id"]]
                    if msg.get("status") != new:
                        msg["status"] = new
                        changed = True
                    if new in M.TERMINAL_STATUSES:
                        self._msg_pending_status.pop(msg["id"], None)
        if changed and self._msg_active_phone:
            self._msg_render_thread()

    # ── conversation list + thread view ──

    def _msg_refresh_conv_list(self):
        """Draw Pulse-style rows only when the visible set/state changed.
        PERF FIX: previously this destroyed and recreated every row on every
        7s poll, which is what made the tool lag with many conversations. Now
        it computes a lightweight signature and skips the redraw when nothing
        relevant changed."""
        phones = M.filter_threads(self._msg_threads, self._msg_query, None)
        # signature: order + unread + selection + last preview/time per row
        sig = tuple((p, p in self._msg_unread, p == self._msg_active_phone,
                     M.last_time(self._msg_threads, p),
                     M.preview_text(self._msg_threads, p)) for p in phones)
        if sig == getattr(self, "_msg_last_sig", None):
            return
        self._msg_last_sig = sig
        for w in self.msg_rows.winfo_children():
            w.destroy()
        self._msg_row_index = phones
        for phone in phones:
            self._msg_draw_row(phone)
        self.msg_canvas.yview_moveto(0.0) if not self._msg_active_phone else None

    def _msg_force_redraw(self):
        self._msg_last_sig = None

    def _msg_draw_row(self, phone):
        is_unread = phone in self._msg_unread
        is_sel = phone == self._msg_active_phone
        bg = self.C_SEL if is_sel else self.C_BG
        row = tk.Frame(self.msg_rows, bg=bg, cursor="hand2")
        row.pack(fill="x")

        # avatar circle (canvas)
        av = tk.Canvas(row, width=44, height=44, bg=bg, highlightthickness=0)
        av.pack(side="left", padx=8, pady=6)
        col = M.avatar_color(phone)
        av.create_oval(4, 4, 40, 40, fill=col, outline=col)
        av.create_text(22, 22, text=M.avatar_initials(phone), fill="white",
                       font=("Segoe UI", 10, "bold"))

        mid = tk.Frame(row, bg=bg); mid.pack(side="left", fill="x", expand=True, pady=6)
        top = tk.Frame(mid, bg=bg); top.pack(fill="x")
        dot = "● " if is_unread else ""
        tk.Label(top, text=dot, bg=bg, fg=self.C_UNREAD,
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Label(top, text=phone, bg=bg, fg=self.C_TEXT, anchor="w",
                 font=("Segoe UI", 10, "bold" if is_unread else "normal")).pack(side="left")
        tk.Label(top, text=M.last_time(self._msg_threads, phone), bg=bg,
                 fg=self.C_MUTED, font=("Segoe UI", 8)).pack(side="right", padx=8)
        tk.Label(mid, text=M.preview_text(self._msg_threads, phone), bg=bg,
                 fg=self.C_MUTED, anchor="w", font=("Segoe UI", 9),
                 justify="left").pack(fill="x")

        # FIX: bind the click on the row AND every descendant, so clicking any
        # part (number, preview, avatar, blank space) always opens the thread.
        handler = lambda e, p=phone: self._msg_open_phone(p)
        row.bind("<Button-1>", handler)
        for child in (av, mid, top, *mid.winfo_children(), *top.winfo_children()):
            child.bind("<Button-1>", handler)

        tk.Frame(self.msg_rows, bg=self.C_DIVIDER, height=1).pack(fill="x")

    def _msg_open_phone(self, phone):
        self._msg_mark_activity()
        self._msg_idle_cycles = 0
        self._msg_active_phone = phone
        self._msg_unread.discard(phone)      # opening marks read
        # render the thread immediately (cheap, feels responsive)
        self._msg_render_thread()
        # FIX: defer the conversation-list rebuild to after this click event
        # finishes — rebuilding destroys the very row we're handling the click
        # on, which was causing missed clicks / hangs.
        self._msg_force_redraw()
        self.after_idle(self._msg_refresh_conv_list)

    def _msg_render_thread(self):
        self.msg_thread_box.config(state="normal")
        self.msg_thread_box.delete("1.0", "end")
        phone = self._msg_active_phone
        if not phone:
            self.msg_thread_lbl.config(text="Select a conversation")
            self.msg_thread_box.config(state="disabled")
            return
        self.msg_thread_lbl.config(text=f"{phone}   ({self.msg_sim.get()})")
        for m in M.conversation(self._msg_threads, phone):
            t = self._msg_short_time(m.get("date", ""))
            if m["dir"] == "in":
                line = f"← {m['msg']}"
                if t:
                    line += f"   [{t}]"
            else:
                badge = M.STATUS_LABEL.get(m.get("status", M.SENT_PENDING), "")
                line = f"→ {m['msg']}"
                meta = "   ".join(x for x in (t, badge) if x)
                if meta:
                    line += f"   [{meta}]"
            self.msg_thread_box.insert("end", line + "\n", m["dir"])
        self.msg_thread_box.see("end")
        self.msg_thread_box.config(state="disabled")

    @staticmethod
    def _msg_short_time(date_str):
        """'2026-01-09 13:05:12.657' -> '13:05'. Best-effort, blank if unknown."""
        s = str(date_str or "").strip()
        if " " in s:
            clock = s.split(" ", 1)[1]
            parts = clock.split(":")
            if len(parts) >= 2:
                return f"{parts[0]}:{parts[1]}"
        return ""

    # ── reply ──

    def _msg_send_reply(self):
        self._msg_mark_activity()
        self._msg_idle_cycles = 0      # expect a reply — poll fast again
        phone = self._msg_active_phone
        if not phone:
            self._ui_error("Messaging", "Open a conversation first.")
            return
        text = self.msg_reply.get().strip()
        if not text:
            return
        token = self._current_sms_token()
        device = self._msg_device_id
        self.msg_reply.set("")

        def worker():
            url, data = M.build_reply_request(token, device, phone, text)
            ok = False
            sms_id = 0
            try:
                r = session.post(url, data=data, timeout=30)
                body = r.json() or {}
                ok = str(body.get("code")) == "0"
                sms_id = int(body.get("id") or 0)
            except Exception:
                ok = False
            import datetime as _dt
            now = f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}"

            def finish():
                entry = {"id": sms_id, "phone": phone, "msg": text,
                         "date": now, "status": M.SENT_SENT if ok else M.SENT_FAILED}
                M.add_messages(self._msg_threads, [entry], "out")
                # track for delivery-status polling if we got a real id
                if ok and sms_id:
                    self._msg_pending_status[sms_id] = phone
                self._msg_render_thread()
                self._msg_refresh_conv_list()
                if not ok:
                    self.msg_status.config(text="Reply may have failed (check SemySMS).",
                                           fg="red")
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

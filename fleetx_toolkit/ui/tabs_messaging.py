import threading
import tkinter as tk
from tkinter import ttk, messagebox

import requests

from ..config import (MESSAGING_SIMS, MESSAGING_SIM_NAMES, MESSAGING_POLL_SECONDS,
                      sim_id_for_name)
from ..storage import load_sms_token
from .. import messaging as M


class MessagingTabMixin:
    """Two-way SMS console (v3.5). Polls one selected SIM's inbox and lets you
    reply in-thread from that same SIM. Restricted to Airtel Pulse / Voda Pulse."""

    def _tab_messaging(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Messaging")

        # runtime state
        self._msg_threads = {}          # phone -> [messages]
        self._msg_last_id = 0           # inbox high-water mark for current SIM
        self._msg_active_phone = None
        self._msg_pending_status = {}   # sms_id -> phone, for outbox status polling
        self._msg_polling = False
        self._msg_device_id = sim_id_for_name(MESSAGING_SIM_NAMES[0])

        # ── top bar: SIM picker + start/stop ──
        top = ttk.Frame(tab); top.pack(fill="x")
        ttk.Label(top, text="SIM:").pack(side="left")
        self.msg_sim = tk.StringVar(value=MESSAGING_SIM_NAMES[0])
        cb = ttk.Combobox(top, textvariable=self.msg_sim, width=16, state="readonly",
                          values=MESSAGING_SIM_NAMES)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._msg_switch_sim())
        self.msg_toggle_btn = ttk.Button(top, text="▶ Start", command=self._msg_toggle)
        self.msg_toggle_btn.pack(side="left", padx=6)
        ttk.Button(top, text="✚ New message", command=self._msg_new_message).pack(side="left", padx=2)
        self.msg_status = ttk.Label(top, text="Stopped", foreground="gray")
        self.msg_status.pack(side="left", padx=8)
        ttk.Label(top, text=f"(polls every {MESSAGING_POLL_SECONDS}s — one SIM at a time)",
                  foreground="gray").pack(side="left")

        # ── split: conversations | thread ──
        body = ttk.Frame(tab); body.pack(fill="both", expand=True, pady=(8, 0))

        left = ttk.LabelFrame(body, text="Conversations", padding=4)
        left.pack(side="left", fill="y")
        self.msg_conv_list = tk.Listbox(left, width=24, height=20,
                                        exportselection=False)
        self.msg_conv_list.pack(fill="y", expand=True)
        self.msg_conv_list.bind("<<ListboxSelect>>", lambda e: self._msg_open_selected())

        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.msg_thread_lbl = ttk.Label(right, text="Select a conversation",
                                        font=("Segoe UI", 10, "bold"))
        self.msg_thread_lbl.pack(anchor="w")
        self.msg_thread_box = tk.Text(right, height=18, width=54, state="disabled",
                                      wrap="word")
        self.msg_thread_box.pack(fill="both", expand=True, pady=4)
        self.msg_thread_box.tag_config("in", foreground="#0a5", spacing3=4)
        self.msg_thread_box.tag_config("out", foreground="#06c", justify="right", spacing3=4)

        rr = ttk.Frame(right); rr.pack(fill="x")
        self.msg_reply = tk.StringVar()
        e = ttk.Entry(rr, textvariable=self.msg_reply, width=44)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._msg_send_reply())
        ttk.Button(rr, text="Send", command=self._msg_send_reply).pack(side="left", padx=4)

    # ── SIM / polling control ──

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
            if not load_sms_token():
                messagebox.showerror("New message",
                    "No SemySMS token. Save it in the SMS Command tab first.", parent=dlg); return
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
        self._msg_device_id = sim_id_for_name(self.msg_sim.get())
        self._msg_threads = {}
        self._msg_last_id = 0
        self._msg_active_phone = None
        self._msg_pending_status = {}
        self.msg_conv_list.delete(0, "end")
        self._msg_render_thread()
        if self._msg_polling:
            self.msg_status.config(text=f"Watching {self.msg_sim.get()}…", foreground="green")

    def _msg_toggle(self):
        if self._msg_polling:
            self._msg_polling = False
            self.msg_toggle_btn.config(text="▶ Start")
            self.msg_status.config(text="Stopped", foreground="gray")
            return
        if not (self.sms_token.get().strip() if hasattr(self, "sms_token") else load_sms_token()):
            if not load_sms_token():
                self._ui_error("Messaging",
                    "No SemySMS token. Save it in the SMS Command tab first.")
                return
        self._msg_polling = True
        self.msg_toggle_btn.config(text="⏸ Stop")
        self.msg_status.config(text=f"Watching {self.msg_sim.get()}…", foreground="green")
        threading.Thread(target=self._msg_poll_loop, daemon=True).start()

    def _msg_poll_loop(self):
        token = load_sms_token()
        while self._msg_polling:
            device = self._msg_device_id
            url, params = M.build_inbox_request(token, device, since_id=self._msg_last_id)
            try:
                r = requests.get(url, params=params, timeout=20)
                msgs = M.parse_inbox(r.json())
            except Exception as e:
                self.after(0, lambda e=e: self.msg_status.config(
                    text=f"Poll error: {e}", foreground="red"))
                msgs = []
            # ignore results if the user switched SIM mid-request
            if device == self._msg_device_id and msgs:
                fresh = M.new_since(msgs, self._msg_last_id)
                self._msg_last_id = M.max_id(msgs, self._msg_last_id)
                if fresh:
                    self.after(0, lambda f=fresh: self._msg_ingest(f))
            # poll delivery status for still-pending sent messages
            if device == self._msg_device_id and self._msg_pending_status:
                ids = list(self._msg_pending_status.keys())
                ourl, oparams = M.build_outbox_request(token, device, ids)
                try:
                    ro = requests.get(ourl, params=oparams, timeout=20)
                    statuses = M.parse_outbox_status(ro.json())
                except Exception:
                    statuses = {}
                if statuses:
                    self.after(0, lambda s=statuses: self._msg_apply_status(s))

            for _ in range(MESSAGING_POLL_SECONDS * 2):
                if not self._msg_polling:
                    break
                threading.Event().wait(0.5)

    def _msg_ingest(self, fresh):
        M.add_messages(self._msg_threads, fresh, "in")
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
        order = M.thread_order(self._msg_threads)
        sel = self._msg_active_phone
        self.msg_conv_list.delete(0, "end")
        for phone in order:
            msgs = self._msg_threads[phone]
            last = msgs[-1]["msg"][:20] if msgs else "(new)"
            self.msg_conv_list.insert("end", f"{phone}  — {last}")
        if sel in order:
            self.msg_conv_list.selection_set(order.index(sel))

    def _msg_open_selected(self):
        idx = self.msg_conv_list.curselection()
        if not idx:
            return
        order = M.thread_order(self._msg_threads)
        if idx[0] < len(order):
            self._msg_active_phone = order[idx[0]]
            self._msg_render_thread()

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
        phone = self._msg_active_phone
        if not phone:
            self._ui_error("Messaging", "Open a conversation first.")
            return
        text = self.msg_reply.get().strip()
        if not text:
            return
        token = load_sms_token()
        device = self._msg_device_id
        self.msg_reply.set("")

        def worker():
            url, data = M.build_reply_request(token, device, phone, text)
            ok = False
            sms_id = 0
            try:
                r = requests.post(url, data=data, timeout=30)
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
                                           foreground="red")
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

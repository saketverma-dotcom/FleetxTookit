import threading
import tkinter as tk
from tkinter import ttk, messagebox

from .. import sms_auth as A
from ..storage import (load_gh_token, save_gh_token,
                       load_sms_login, save_sms_login, clear_sms_login)


class SmsAuthMixin:
    """Separate email+password gate for the SMS Command + Messaging tabs.
    Independent of the FleetX Bearer token. Admin manages users via a Gist."""

    def _build_sms_gate(self, parent):
        """Login gate shown inside the SMS/Messaging area until authenticated.
        Returns the frame that holds the real tab content once logged in."""
        self._sms_authed = False
        self._sms_is_admin = False
        self._sms_user = None
        self._sms_store = None
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        self._sms_gate = ttk.Frame(wrap, padding=30)
        self._sms_content = ttk.Frame(wrap)
        self._sms_gate.pack(expand=True)

        ttk.Label(self._sms_gate, text="SMS / Messaging Login",
                  font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 16))
        _rem_email, _rem_pw = load_sms_login()
        ttk.Label(self._sms_gate, text="Email:").grid(row=1, column=0, sticky="e", pady=4)
        self._sms_email = tk.StringVar(value=_rem_email)
        ttk.Entry(self._sms_gate, textvariable=self._sms_email, width=30).grid(
            row=1, column=1, pady=4)
        ttk.Label(self._sms_gate, text="Password:").grid(row=2, column=0, sticky="e", pady=4)
        self._sms_pass = tk.StringVar(value=_rem_pw)
        pe = ttk.Entry(self._sms_gate, textvariable=self._sms_pass, width=30, show="•")
        pe.grid(row=2, column=1, pady=4)
        pe.bind("<Return>", lambda e: self._sms_do_login())
        self._sms_remember = tk.BooleanVar(value=bool(_rem_email))
        ttk.Checkbutton(self._sms_gate, text="Remember me on this PC",
                        variable=self._sms_remember).grid(row=3, column=1, sticky="w")
        ttk.Button(self._sms_gate, text="Login", command=self._sms_do_login).grid(
            row=4, column=0, columnspan=2, pady=12)
        self._sms_login_status = ttk.Label(self._sms_gate, text="", foreground="red")
        self._sms_login_status.grid(row=5, column=0, columnspan=2)
        ttk.Label(self._sms_gate,
                  text="Access is managed by the admin. Contact saket.verma@fleetx.io.",
                  foreground="gray").grid(row=6, column=0, columnspan=2, pady=(10, 0))
        return self._sms_content

    def _sms_try_auto_auth(self):
        """Skip the second login when the user has already proved their identity
        via the FleetX Bearer login AND is authorised for SMS by either route:

          • "SMS Command" or "Messaging" ticked for them in User Access (Admin), or
          • their email is in the admin-managed SMS user list.

        Anyone else still sees the SMS login screen. Admin rights inside the SMS
        panel come only from the SMS list (admin: true) or ADMIN_EMAILS — a tab
        grant alone never confers user management.
        """
        import threading as _t
        from .. import state as _state
        from ..access_control import allowed_tabs_for
        from ..config import ADMIN_EMAILS
        email = getattr(_state, "user_email", None)
        if not email or self._sms_authed:
            return

        # Route 1: FleetX tab grant (checked locally from the access snapshot)
        try:
            granted = allowed_tabs_for(email) or []
        except Exception:
            granted = []
        by_tab_grant = ("SMS Command" in granted) or ("Messaging" in granted)

        def worker():
            store = A.load_store()
            def finish():
                if not store:
                    return
                by_list = A.user_exists(store, email)
                if not (by_tab_grant or by_list):
                    return                    # not authorised -> normal login
                self._sms_store = store
                self._sms_authed = True
                # admin only via the SMS list flag or a global admin email
                self._sms_is_admin = (A.user_is_admin(store, email)
                                      or A.normalize_email(email) in
                                      {e.lower() for e in ADMIN_EMAILS})
                self._sms_user = A.normalize_email(email)
                self._sms_enter()
                self._sms_ensure_token()
            self.after(0, finish)
        _t.Thread(target=worker, daemon=True).start()

    def _sms_do_login(self):
        email = self._sms_email.get().strip()
        pw = self._sms_pass.get()
        if not email or not pw:
            self._sms_login_status.config(text="Enter email and password.")
            return
        self._sms_login_status.config(text="Checking…", foreground="black")

        def worker():
            store = A.load_store()
            def finish():
                if store is None:
                    self._sms_login_status.config(
                        text="Could not reach the user list. Check your connection.",
                        foreground="red")
                    return
                ok, is_admin, why = A.check_login(store, email, pw)
                if not ok:
                    self._sms_login_status.config(text=why, foreground="red")
                    return
                self._sms_store = store
                self._sms_authed = True
                self._sms_is_admin = is_admin
                self._sms_user = A.normalize_email(email)
                if self._sms_remember.get():
                    save_sms_login(self._sms_user, pw)
                else:
                    clear_sms_login()
                self._sms_pass.set("")
                self._sms_enter()
                self._sms_ensure_token()   # prompt once if no local token yet
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _sms_ensure_token(self):
        """If no SemySMS token is saved locally for this user, ask for it once
        and store it in Windows Credential Manager. Silent if already present."""
        from ..storage import load_sms_token, save_sms_token
        if load_sms_token():
            return
        dlg = tk.Toplevel(self)
        dlg.title("SemySMS token")
        dlg.transient(self); dlg.grab_set(); dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Enter your SemySMS API token",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frm, text="Stored securely on this PC only — asked just once.",
                  foreground="gray").pack(anchor="w", pady=(0, 8))
        var = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=var, width=44, show="•")
        ent.pack(); ent.focus_set()
        status = ttk.Label(frm, text="", foreground="red"); status.pack(anchor="w", pady=4)

        def save():
            t = var.get().strip()
            if not t:
                status.config(text="Enter a token."); return
            if save_sms_token(t):
                dlg.destroy()
            else:
                status.config(text="Could not store securely (keyring unavailable).")
        btns = ttk.Frame(frm); btns.pack(pady=(6, 0))
        ttk.Button(btns, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(btns, text="Later", command=dlg.destroy).pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: save())

    def _sms_enter(self):
        """Reveal the real SMS/Messaging content after successful login."""
        self._sms_gate.pack_forget()
        self._sms_content.pack(fill="both", expand=True)
        # build the inner notebook of the two tabs (+ admin if admin)
        for w in self._sms_content.winfo_children():
            w.destroy()
        bar = ttk.Frame(self._sms_content, padding=(6, 4)); bar.pack(fill="x")
        ttk.Label(bar, text=f"Signed in: {self._sms_user}"
                            + ("  (admin)" if self._sms_is_admin else ""),
                  foreground="green").pack(side="left")
        ttk.Button(bar, text="Sign out", command=self._sms_signout).pack(side="right")
        self._add_update_button(bar)


        # Notebook + Live Log in a draggable split, so the log is RESIZABLE
        # here too (matching FleetX Tools behaviour).
        self._sms_paned = ttk.PanedWindow(self._sms_content, orient="vertical")
        self._sms_paned.pack(fill="both", expand=True)
        nb_holder = ttk.Frame(self._sms_paned)
        inner = ttk.Notebook(nb_holder)
        inner.pack(fill="both", expand=True)
        self._sms_paned.add(nb_holder, weight=4)
        self._sms_inner_nb = inner
        self._sms_build_feature_tabs(inner)

        # build the (resizable) Live Log pane now that the paned window exists
        from tkinter import scrolledtext as _st
        self._sms_log_frame = ttk.LabelFrame(
            self._sms_paned, text="Live Log (drag divider above to resize)", padding=4)
        self._sms_log_box = _st.ScrolledText(self._sms_log_frame, height=7,
                                             state="disabled", font=("Consolas", 9))
        self._sms_log_box.pack(fill="both", expand=True)
        self._sms_log_box.tag_config("ok", foreground="green")
        self._sms_log_box.tag_config("err", foreground="red")
        self._sms_log_box.tag_config("info", foreground="blue")
        if not getattr(self, "log_box", None):
            self.log_box = self._sms_log_box
        if self._sms_is_admin:
            self._sms_build_admin_tab(inner)
        inner.bind("<<NotebookTabChanged>>", self._sms_toggle_log)
        self._sms_toggle_log()

    def _sms_log(self, msg, tag=None):
        """Write to the SMS Command Live Log (its own box). Falls back to the
        main log() if the SMS log isn't built yet."""
        box = getattr(self, "_sms_log_box", None)
        if box is None:
            return self.log(msg, tag)
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self._sms_log(msg, tag))
            return
        box.config(state="normal")
        box.insert("end", msg + "\n", tag)
        self._trim_log(box)
        box.see("end")
        box.config(state="disabled")

    def _sms_toggle_log(self, _event=None):
        """Show the Live Log only when the SMS Command inner tab is active."""
        try:
            current = self._sms_inner_nb.tab(self._sms_inner_nb.select(), "text")
        except Exception:
            return
        if self._sms_log_frame is None:
            return
        panes = self._sms_paned.panes()
        pane_id = str(self._sms_log_frame)
        if current == "SMS Command":
            if pane_id not in panes:
                self._sms_paned.add(self._sms_log_frame, weight=1)
        else:
            if pane_id in panes:
                self._sms_paned.forget(self._sms_log_frame)

    def _sms_signout(self):
        self._sms_authed = False
        self._sms_is_admin = False
        self._sms_user = None
        self._sms_content.pack_forget()
        self._sms_login_status.config(text="")
        self._sms_gate.pack(expand=True)

    # ── admin user-management ──

    def _sms_build_admin_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="⚙ Manage Users (Admin)")

        tf = ttk.LabelFrame(tab, text="GitHub token (gist scope, stored securely)", padding=6)
        tf.pack(fill="x")
        self._sms_gh = tk.StringVar(value=load_gh_token())
        ttk.Entry(tf, textvariable=self._sms_gh, width=44, show="•").pack(side="left", padx=4)
        ttk.Button(tf, text="Save token",
                   command=lambda: (save_gh_token(self._sms_gh.get().strip()),
                                    self._sms_admin_status.config(
                                        text="Token saved.", foreground="green"))
                   ).pack(side="left", padx=4)

        form = ttk.LabelFrame(tab, text="Add / update user", padding=8)
        form.pack(fill="x", pady=8)
        ttk.Label(form, text="Email:").grid(row=0, column=0, sticky="e", pady=3)
        self._sms_new_email = tk.StringVar()
        ttk.Entry(form, textvariable=self._sms_new_email, width=30).grid(row=0, column=1, pady=3)
        ttk.Label(form, text="Password:").grid(row=1, column=0, sticky="e", pady=3)
        self._sms_new_pw = tk.StringVar()
        pwrow = ttk.Frame(form); pwrow.grid(row=1, column=1, pady=3, sticky="w")
        ttk.Entry(pwrow, textvariable=self._sms_new_pw, width=22).pack(side="left")
        ttk.Button(pwrow, text="Generate",
                   command=lambda: self._sms_new_pw.set(A.generate_password())
                   ).pack(side="left", padx=4)
        self._sms_new_admin = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Admin", variable=self._sms_new_admin).grid(
            row=2, column=1, sticky="w")
        ttk.Button(form, text="Save user",
                   command=self._sms_admin_save_user).grid(row=3, column=1, sticky="w", pady=6)

        self._sms_admin_status = ttk.Label(tab, text="", foreground="green")
        self._sms_admin_status.pack(anchor="w")

        lf = ttk.LabelFrame(tab, text="Users", padding=6)
        lf.pack(fill="both", expand=True)
        self._sms_user_list = tk.Listbox(lf, height=10)
        self._sms_user_list.pack(side="left", fill="both", expand=True)
        rb = ttk.Frame(lf); rb.pack(side="left", fill="y", padx=6)
        ttk.Button(rb, text="Reset password", command=self._sms_admin_reset).pack(fill="x", pady=2)
        ttk.Button(rb, text="Delete user", command=self._sms_admin_delete).pack(fill="x", pady=2)
        ttk.Button(rb, text="Refresh", command=self._sms_admin_refresh).pack(fill="x", pady=2)
        self._sms_admin_refresh()

    def _sms_admin_refresh(self):
        self._sms_user_list.delete(0, "end")
        users = (self._sms_store or {}).get("users", {})
        for email, rec in sorted(users.items()):
            tag = "  (admin)" if rec.get("admin") else ""
            if A.is_legacy_hash(rec.get("pw")):
                tag += "  [old hash — reset advised]"
            self._sms_user_list.insert("end", email + tag)

    def _sms_admin_selected_email(self):
        sel = self._sms_user_list.curselection()
        if not sel:
            return None
        raw = self._sms_user_list.get(sel[0])
        return raw.split("  (admin)")[0].split("  [old hash")[0].strip()

    def _sms_admin_push(self, new_store, ok_msg, on_success=None):
        tok = self._sms_gh.get().strip() or load_gh_token()
        if not tok:
            self._sms_admin_status.config(
                text="Enter and save a GitHub token first.", foreground="red")
            return
        def worker():
            ok, msg = A.push_store(new_store, tok)
            def finish():
                if ok:
                    self._sms_store = new_store
                    self._sms_admin_status.config(text=ok_msg, foreground="green")
                    self._sms_admin_refresh()
                    if on_success:
                        on_success()
                else:
                    self._sms_admin_status.config(text=msg, foreground="red")
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _sms_show_credentials_once(self, email, password):
        """Display the new credentials ONCE so the admin can pass them on.
        Passwords are hashed on save and cannot be shown again afterwards."""
        dlg = tk.Toplevel(self)
        dlg.title("New credentials")
        dlg.transient(self); dlg.grab_set(); dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Share these with the user now —",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frm, text="the password is hashed and cannot be shown again.",
                  foreground="gray").pack(anchor="w", pady=(0, 8))
        text = f"Email:    {email}\nPassword: {password}"
        box = tk.Text(frm, height=2, width=42, relief="solid", borderwidth=1)
        box.insert("1.0", text); box.config(state="disabled")
        box.pack(pady=4)

        def copy():
            self.clipboard_clear(); self.clipboard_append(text)
            copied.config(text="Copied to clipboard.")
        row = ttk.Frame(frm); row.pack(pady=(6, 0))
        ttk.Button(row, text="Copy", command=copy).pack(side="left", padx=4)
        ttk.Button(row, text="Done", command=dlg.destroy).pack(side="left", padx=4)
        copied = ttk.Label(frm, text="", foreground="green"); copied.pack()

    def _sms_admin_save_user(self):
        email = self._sms_new_email.get().strip()
        pw = self._sms_new_pw.get()
        if not email or not pw:
            self._sms_admin_status.config(text="Email and password required.", foreground="red")
            return
        new = A.apply_user_change(self._sms_store, "set", email, password=pw,
                                  admin=self._sms_new_admin.get())
        shown_email, shown_pw = A.normalize_email(email), pw
        self._sms_new_email.set(""); self._sms_new_pw.set(""); self._sms_new_admin.set(False)
        self._sms_admin_push(new, f"Saved {shown_email}.",
                             on_success=lambda: self._sms_show_credentials_once(
                                 shown_email, shown_pw))

    def _sms_admin_reset(self):
        email = self._sms_admin_selected_email()
        if not email:
            return
        pw = self._sms_new_pw.get()
        if not pw:
            self._sms_admin_status.config(
                text="Type the new password in the Password field, then Reset.",
                foreground="red")
            return
        new = A.apply_user_change(self._sms_store, "set", email, password=pw)
        self._sms_new_pw.set("")
        self._sms_admin_push(new, f"Password reset for {email}.",
                             on_success=lambda: self._sms_show_credentials_once(email, pw))

    def _sms_admin_delete(self):
        email = self._sms_admin_selected_email()
        if not email:
            return
        if email == self._sms_user:
            self._sms_admin_status.config(text="You can't delete yourself.", foreground="red")
            return
        if not messagebox.askyesno("Delete user", f"Remove {email}?"):
            return
        new = A.apply_user_change(self._sms_store, "delete", email)
        self._sms_admin_push(new, f"Deleted {email}.")

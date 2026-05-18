import threading
import tkinter as tk
import customtkinter as ctk
from citation_hallucination_detector import HallucinationDetector, VerificationResult

# ─────────────────────────────────────────────
# THEME
# ─────────────────────────────────────────────

BG_TOP     = "#fcf7df"
BORDER     = "#a79a8a"
ACCENT     = "#3e3630"
TEXT       = "#000000"
MUTED      = "#52526e"
REAL_COL   = "#027E44"
HALLUC_COL = "#ff4f6b"
UNVER_COL  = "#6c3f01"
UNKN_COL   = "#8888aa"
INPUT_BG   = "#c7d3db"

VERDICT_COLORS = {
    "REAL":         REAL_COL,
    "HALLUCINATED": HALLUC_COL,
    "UNVERIFIABLE": UNVER_COL,
    "UNKNOWN":      UNKN_COL,
}

VERDICT_ICONS = {
    "REAL":         "✅  REAL",
    "HALLUCINATED": "❌  HALLUCINATED",
    "UNVERIFIABLE": "⚠️   UNVERIFIABLE",
    "UNKNOWN":      "❓  UNKNOWN",
}

# Configure global appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VeriCite")
        self.geometry("1000x600")
        self.minsize(700, 620)
        self.configure(fg_color=BG_TOP)
        self.detector = HallucinationDetector()
        self._build()

    # ── UI ────────────────────────────────────

    def _build(self):
        # Scrollable content container
        canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=BG_TOP)
        canvas.pack(side="left", fill="both", expand=True, padx=(28, 0), pady=28)

        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y", pady=28)

        canvas.configure(yscrollcommand=scrollbar.set)

        scroll_frame = tk.Frame(canvas, bg=BG_TOP)
        self._scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(self._scroll_window, width=event.width)

        scroll_frame.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        outer = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # ── header ──
        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 18))

        ctk.CTkLabel(
            hdr, text="REFERENCE AUTHENTICATION TOOL",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            text_color=ACCENT, anchor="w",
        ).pack(anchor="center")

        ctk.CTkLabel(
            hdr, text="VeriCite",
            font=ctk.CTkFont(family="Georgia", size=22, weight="bold"),
            text_color=TEXT, anchor="w",
        ).pack(anchor="center", pady=(4, 0))

        ctk.CTkLabel(
            hdr,
            text="Verify whether a citation actually exists in its claimed source.",
            font=ctk.CTkFont(family="Courier New", size=13),
            text_color=MUTED, anchor="w", wraplength=720, justify="left",
        ).pack(anchor="center", pady=(4, 0))

        # divider
        ctk.CTkFrame(outer, height=1, fg_color=BORDER).pack(fill="x", pady=(10, 20))

        # ── form ──
        form = ctk.CTkFrame(outer, fg_color="transparent")
        form.pack(fill="x")

        self._label(form, "SOURCE — URL, DOI, arXiv ID, or PMID")
        self._source_placeholder = "Input source here..."
        self.source_var = ctk.StringVar(value=self._source_placeholder)
        self.source_entry = ctk.CTkEntry(
            form,
            textvariable=self.source_var,
            font=ctk.CTkFont(family="Courier New", size=12),
            fg_color=INPUT_BG,
            text_color=MUTED,
            border_color=BORDER,
            border_width=1,
        )
        self.source_entry.pack(fill="x", ipady=4, pady=(0, 16))
        self._source_placeholder_active = True
        self.source_entry.bind("<FocusIn>", lambda e: self._clear_entry())
        self.source_entry.bind("<FocusOut>", lambda e: self._restore_entry_placeholder())

        self._label(form, "CITATION TEXT TO VERIFY")
        self._citation_placeholder = "Input citation here..."
        self.citation_text = ctk.CTkTextbox(
            form,
            font=ctk.CTkFont(family="Courier New", size=12),
            height=90,
            fg_color=INPUT_BG,
            text_color=MUTED,
            border_color=BORDER,
            border_width=1,
            wrap="word",
        )
        self.citation_text.pack(fill="x", pady=(0, 20))
        self.citation_text.insert("1.0", self._citation_placeholder)
        self._citation_placeholder_active = True
        self.citation_text.bind("<FocusIn>", lambda e: self._clear_textbox())
        self.citation_text.bind("<FocusOut>", lambda e: self._restore_textbox_placeholder())

        # button
        self.btn = ctk.CTkButton(
            form,
            text="VERIFY CITATION",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            fg_color=ACCENT,
            text_color=BG_TOP,
            hover_color="#a79a8a",
            corner_radius=4,
            height=44,
            command=self._start_verify,
        )
        self.btn.pack(fill="x")

        # status label
        self.status_var = ctk.StringVar(value="Ready.")
        ctk.CTkLabel(
            form,
            textvariable=self.status_var,
            font=ctk.CTkFont(family="Courier New", size=12),
            text_color=MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(8, 0))

        # divider
        ctk.CTkFrame(outer, height=1, fg_color=BORDER).pack(fill="x", pady=(16, 16))

        # ── results ──
        result_frame = ctk.CTkFrame(outer, fg_color="transparent")
        result_frame.pack(fill="both", expand=True)

        self.verdict_var    = ctk.StringVar()
        self.confidence_var = ctk.StringVar()

        vrow = ctk.CTkFrame(result_frame, fg_color="transparent")
        vrow.pack(fill="x", pady=(0, 12))

        self.verdict_label = ctk.CTkLabel(
            vrow,
            textvariable=self.verdict_var,
            font=ctk.CTkFont(family="Georgia", size=16, weight="bold"),
            text_color=UNKN_COL,
            anchor="w",
        )
        self.verdict_label.pack(side="left")

        ctk.CTkLabel(
            vrow,
            textvariable=self.confidence_var,
            font=ctk.CTkFont(family="Courier New", size=12),
            text_color=MUTED,
            anchor="w",
        ).pack(side="left", padx=(14, 0))

        self._label(result_frame, "EVIDENCE")

        self.evidence_box = ctk.CTkTextbox(
            result_frame,
            font=ctk.CTkFont(family="Courier New", size=12),
            height=160,
            fg_color=INPUT_BG,
            text_color=TEXT,
            border_color=BORDER,
            border_width=1,
            wrap="word",
            state="disabled",
        )
        self.evidence_box.pack(fill="both", expand=True)

        # Configure tags on the underlying tk.Text widget
        inner_text = self.evidence_box._textbox
        inner_text.tag_config("pass", foreground=REAL_COL)
        inner_text.tag_config("fail", foreground=HALLUC_COL)
        inner_text.tag_config("warn", foreground=UNVER_COL)
        inner_text.tag_config("info", foreground=MUTED)
        inner_text.tag_config(
            "head", foreground=ACCENT,
            font=("Courier New", 12, "bold"),
        )

    # ── helpers ──────────────────────────────

    def _label(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            text_color=ACCENT, anchor="w",
        ).pack(anchor="w", pady=(0, 5))

    def _clear_entry(self):
        if self._source_placeholder_active and self.source_var.get() == self._source_placeholder:
            self.source_var.set("")
            self._source_placeholder_active = False
            self.source_entry.configure(text_color=TEXT)

    def _restore_entry_placeholder(self):
        if self.source_var.get().strip() == "":
            self.source_var.set(self._source_placeholder)
            self._source_placeholder_active = True
            self.source_entry.configure(text_color=MUTED)

    def _clear_textbox(self):
        current = self.citation_text.get("1.0", "end-1c")
        if self._citation_placeholder_active and current == self._citation_placeholder:
            self.citation_text.configure(state="normal", text_color=TEXT)
            self.citation_text.delete("1.0", "end")
            self._citation_placeholder_active = False

    def _restore_textbox_placeholder(self):
        text = self.citation_text.get("1.0", "end-1c").strip()
        if text == "":
            self.citation_text.configure(state="normal", text_color=MUTED)
            self.citation_text.delete("1.0", "end")
            self.citation_text.insert("1.0", self._citation_placeholder)
            self._citation_placeholder_active = True

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _write_evidence(self, lines):
        box = self.evidence_box
        inner = box._textbox
        box.configure(state="normal")
        box.delete("1.0", "end")
        for line in lines:
            if line.startswith("✔"):
                inner.insert("end", line + "\n", "pass")
            elif line.startswith("✘"):
                inner.insert("end", line + "\n", "fail")
            elif line.startswith("~"):
                inner.insert("end", line + "\n", "warn")
            elif line.startswith("Citation verified") or line.startswith("Citation NOT"):
                inner.insert("end", line + "\n", "head")
            else:
                inner.insert("end", line + "\n", "info")
        box.configure(state="disabled")

    # ── verify ────────────────────────────────

    def _start_verify(self):
        source   = self.source_var.get().strip()
        if self._source_placeholder_active or source == self._source_placeholder:
            source = ""
        citation = self.citation_text.get("1.0", "end-1c").strip()
        if self._citation_placeholder_active or citation == self._citation_placeholder:
            citation = ""

        if not citation:
            self._set_status("⚠  Citation text is required.")
            return
        if not source:
            self._set_status("⚠  Source is required.")
            return

        self.btn.configure(state="disabled", text="VERIFYING…")
        self._set_status("Fetching source and analysing signals…")
        self.verdict_var.set("")
        self.confidence_var.set("")
        self._write_evidence(["Checking…"])

        threading.Thread(
            target=self._run_verify, args=(citation, source), daemon=True
        ).start()

    def _run_verify(self, citation, source):
        result = self.detector.verify(citation, source=source)
        self.after(0, lambda: self._show_result(result))

    def _show_result(self, r: VerificationResult):
        verdict = r.verdict
        color   = VERDICT_COLORS.get(verdict, UNKN_COL)
        icon    = VERDICT_ICONS.get(verdict, "❓")

        self.verdict_var.set(icon)
        self.verdict_label.configure(text_color=color)
        self.confidence_var.set(f"{r.confidence:.0%} confidence")

        checked = ", ".join(r.checked_via) if r.checked_via else "none"
        lines = [f"Checked via: {checked}", "─" * 50] + r.evidence

        self._write_evidence(lines)
        self.btn.configure(state="normal", text="VERIFY CITATION")
        self._set_status(f"Done. {len(r.evidence)} evidence point(s) collected.")


# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
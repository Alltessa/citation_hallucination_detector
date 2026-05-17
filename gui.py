"""
Citation Hallucination Detector — Tkinter Desktop GUI
======================================================
Run:  python gui.py
No extra installs needed — tkinter is built into Python.
Place this file in the same folder as citation_hallucination_detector.py
"""

import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from citation_hallucination_detector import HallucinationDetector, VerificationResult

# ─────────────────────────────────────────────
# THEME
# ─────────────────────────────────────────────

BG         = "#0a0a0f"
SURFACE    = "#111118"
BORDER     = "#1e1e2e"
ACCENT     = "#c8ff47"
TEXT       = "#e4e4f0"
MUTED      = "#52526e"
REAL_COL   = "#3dffaa"
HALLUC_COL = "#ff4f6b"
UNVER_COL  = "#ffb347"
UNKN_COL   = "#8888aa"
INPUT_BG   = "#0d0d14"

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


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Citation Hallucination Detector")
        self.geometry("820x700")
        self.minsize(700, 580)
        self.configure(bg=BG)
        self.detector = HallucinationDetector()
        self._build()

    # ── UI construction ───────────────────────

    def _build(self):
        # outer padding frame
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=28, pady=28)

        # ── header ──
        hdr = tk.Frame(outer, bg=BG)
        hdr.pack(fill="x", pady=(0, 22))

        tk.Label(
            hdr, text="RESEARCH INTEGRITY TOOL",
            font=("Courier New", 9, "bold"),
            fg=ACCENT, bg=BG, anchor="w",
        ).pack(anchor="w")

        tk.Label(
            hdr, text="Citation Hallucination Detector",
            font=("Georgia", 22, "bold"),
            fg=TEXT, bg=BG, anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            hdr,
            text="Verify whether an AI-generated citation actually exists in its claimed source.",
            font=("Courier New", 10),
            fg=MUTED, bg=BG, anchor="w", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # divider
        tk.Frame(outer, height=1, bg=BORDER).pack(fill="x", pady=(10, 20))

        # ── input fields ──
        form = tk.Frame(outer, bg=BG)
        form.pack(fill="x")

        self._make_label(form, "SOURCE — URL, DOI, arXiv ID, or PMID")
        self.source_var = tk.StringVar()
        source_entry = tk.Entry(
            form, textvariable=self.source_var,
            font=("Courier New", 11),
            bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        source_entry.pack(fill="x", ipady=8, pady=(0, 16))
        source_entry.insert(0, "https://arxiv.org/abs/1706.03762")
        source_entry.bind("<FocusIn>",  lambda e: self._clear_placeholder(source_entry, "https://arxiv.org/abs/1706.03762"))

        self._make_label(form, "CITATION TEXT TO VERIFY")
        self.citation_text = tk.Text(
            form, font=("Courier New", 11), height=4,
            bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT,
            wrap="word",
        )
        self.citation_text.pack(fill="x", pady=(0, 20))
        placeholder = "Vaswani, A. et al. (2017). Attention Is All You Need. Advances in Neural Information Processing Systems, 30."
        self.citation_text.insert("1.0", placeholder)
        self.citation_text.bind("<FocusIn>",  lambda e: self._clear_text_placeholder(placeholder))

        # ── verify button ──
        self.btn = tk.Button(
            form, text="VERIFY CITATION",
            font=("Courier New", 12, "bold"),
            bg=ACCENT, fg=BG, activebackground="#a8e020", activeforeground=BG,
            relief="flat", bd=0, cursor="hand2", pady=12,
            command=self._start_verify,
        )
        self.btn.pack(fill="x")

        # ── status bar ──
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(
            form, textvariable=self.status_var,
            font=("Courier New", 10), fg=MUTED, bg=BG, anchor="w",
        ).pack(anchor="w", pady=(8, 0))

        # divider
        tk.Frame(outer, height=1, bg=BORDER).pack(fill="x", pady=(16, 16))

        # ── result area ──
        self.result_frame = tk.Frame(outer, bg=BG)
        self.result_frame.pack(fill="both", expand=True)

        # verdict row
        self.verdict_var   = tk.StringVar()
        self.confidence_var = tk.StringVar()

        vrow = tk.Frame(self.result_frame, bg=BG)
        vrow.pack(fill="x", pady=(0, 12))

        self.verdict_label = tk.Label(
            vrow, textvariable=self.verdict_var,
            font=("Georgia", 16, "bold"),
            fg=UNKN_COL, bg=BG, anchor="w",
        )
        self.verdict_label.pack(side="left")

        self.conf_label = tk.Label(
            vrow, textvariable=self.confidence_var,
            font=("Courier New", 11),
            fg=MUTED, bg=BG, anchor="w",
        )
        self.conf_label.pack(side="left", padx=(14, 0))

        # evidence box
        self._make_label(self.result_frame, "EVIDENCE")
        self.evidence_box = scrolledtext.ScrolledText(
            self.result_frame,
            font=("Courier New", 10), height=10,
            bg=INPUT_BG, fg=TEXT,
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
            state="disabled", wrap="word",
        )
        self.evidence_box.pack(fill="both", expand=True)

        # configure text tags for coloring
        self.evidence_box.tag_config("pass",  foreground=REAL_COL)
        self.evidence_box.tag_config("fail",  foreground=HALLUC_COL)
        self.evidence_box.tag_config("warn",  foreground=UNVER_COL)
        self.evidence_box.tag_config("info",  foreground=MUTED)
        self.evidence_box.tag_config("head",  foreground=ACCENT, font=("Courier New", 10, "bold"))

    # ── helpers ──────────────────────────────

    def _make_label(self, parent, text):
        tk.Label(
            parent, text=text,
            font=("Courier New", 9, "bold"),
            fg=ACCENT, bg=BG, anchor="w",
        ).pack(anchor="w", pady=(0, 5))

    def _clear_placeholder(self, entry, placeholder):
        if entry.get() == placeholder:
            entry.delete(0, "end")

    def _clear_text_placeholder(self, placeholder):
        if self.citation_text.get("1.0", "end-1c") == placeholder:
            self.citation_text.delete("1.0", "end")

    def _set_status(self, msg, color=MUTED):
        self.status_var.set(msg)

    def _write_evidence(self, lines: list[str]):
        box = self.evidence_box
        box.config(state="normal")
        box.delete("1.0", "end")

        for line in lines:
            if line.startswith("✔"):
                box.insert("end", line + "\n", "pass")
            elif line.startswith("✘"):
                box.insert("end", line + "\n", "fail")
            elif line.startswith("~"):
                box.insert("end", line + "\n", "warn")
            elif line.startswith("Citation verified") or line.startswith("Citation NOT"):
                box.insert("end", line + "\n", "head")
            else:
                box.insert("end", line + "\n", "info")

        box.config(state="disabled")

    # ── verify flow ──────────────────────────

    def _start_verify(self):
        source   = self.source_var.get().strip()
        citation = self.citation_text.get("1.0", "end-1c").strip()

        if not citation:
            self._set_status("⚠  Citation text is required.", UNVER_COL)
            return
        if not source:
            self._set_status("⚠  Source is required.", UNVER_COL)
            return

        self.btn.config(state="disabled", text="VERIFYING…")
        self._set_status("Fetching source and analysing signals…", ACCENT)
        self.verdict_var.set("")
        self.confidence_var.set("")
        self._write_evidence(["Checking…"])

        thread = threading.Thread(
            target=self._run_verify,
            args=(citation, source),
            daemon=True,
        )
        thread.start()

    def _run_verify(self, citation: str, source: str):
        result = self.detector.verify(citation, source=source)
        # update UI from main thread
        self.after(0, lambda: self._show_result(result))

    def _show_result(self, r: VerificationResult):
        verdict = r.verdict
        color   = VERDICT_COLORS.get(verdict, UNKN_COL)
        icon    = VERDICT_ICONS.get(verdict, "❓")

        self.verdict_var.set(icon)
        self.verdict_label.config(fg=color)
        self.confidence_var.set(f"{r.confidence:.0%} confidence")

        checked = ", ".join(r.checked_via) if r.checked_via else "none"
        lines = [
            f"Checked via: {checked}",
            "─" * 50,
        ] + r.evidence

        self._write_evidence(lines)
        self.btn.config(state="normal", text="VERIFY CITATION")
        self._set_status(f"Done. {len(r.evidence)} evidence point(s) collected.", MUTED)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
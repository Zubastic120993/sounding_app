
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path
from typing import Dict, Optional, List

from PySide6.QtCore import Qt, QProcess
from PySide6.QtGui import QFont, QPixmap, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QGroupBox, QMessageBox, QSpacerItem, QSizePolicy, QStyle, QScrollArea,
    QFrame, QLineEdit, QGraphicsDropShadowEffect
)

# --- Version flag so we can see it's the right file
LAUNCHER_VERSION = "Dark v1 + Open/Stop All"

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "app"
ASSETS = ROOT / "assets"

FRIENDLY: Dict[str, str] = {
    "ui_fuel_sheet.py": "Fuel Sheet",
    "ui_lube_oils.py": "Lube Oils",
    "ui_other_tanks.py": "Other Tanks",
    "ui_sludge.py": "Sludge & Waste Oil",
    "ui_summary_all.py": "Daily Summary",
}

# ---------------- Dark Theme ----------------
APP_STYLES = """
QWidget { background: #f2f2f2; color:#000; }
QGroupBox.card {
  border:1px solid #ccc; border-radius:10px; background:#ffffff;
  margin-top:8px; padding:12px;
}
QGroupBox.card::title {
  subcontrol-origin: margin; left:14px; padding:0 4px;
  font-weight:600; font-size:14px; color:#000;
}
QPushButton {
  border:1px solid #aaa; background:#eaeaea; border-radius:6px;
  padding:6px 12px; font-weight:500;
}
QPushButton:hover { background:#f5f5f5; }
QPushButton:pressed { background:#ddd; }
QPushButton:disabled { color:#888; background:#f0f0f0; }
"""

def pill(text: str, kind: str) -> str:
    colors = {
    "idle":   ("#cfe0ff", "#122353"),
    "run":    ("#d4f0ff", "#0f2e66"),   
    "exited": ("#b9c9ff", "#0e244c"),
    "error":  ("#ffd0d6", "#3a1230"),
    }
    fg, bg = colors.get(kind, ("#c7cbd6", "#1b2130"))
    return f'<span style="color:{fg}; background:{bg}; padding:2px 8px; border-radius:8px; font-weight:600">{text}</span>'

class ShadowCard(QGroupBox):
    def __init__(self, title: str):
        super().__init__(title)
        self.setObjectName("card")
        self.setStyleSheet(APP_STYLES)

        # --- make title larger & bold
        f = self.font()
        f.setPointSize(16)   # increase size (try 14 if you want even bigger)
        f.setBold(False)
        self.setFont(f)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

class AppCard(ShadowCard):
    def __init__(self, script_path: Path, parent=None):
        title = FRIENDLY.get(
            script_path.name,
            script_path.stem.replace("ui_", "").replace("_", " ").title()
        )
        super().__init__(title)
        self.script_path = script_path
        self.proc: Optional[QProcess] = None

        v = QVBoxLayout(self); v.setSpacing(8)

        #path = QLabel(script_path.as_posix()); path.setObjectName("path"); path.setWordWrap(True)
        self.status_lbl = QLabel(); self.set_status("idle")

        btns = QHBoxLayout()
        self.run_btn = QPushButton("Open"); self.run_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.run_btn.clicked.connect(self.launch)

        self.stop_btn = QPushButton("Stop"); self.stop_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_btn.setEnabled(False); self.stop_btn.clicked.connect(self.stop)

        btns.addWidget(self.run_btn); btns.addWidget(self.stop_btn)
        btns.addItem(QSpacerItem(20, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))

        #v.addWidget(path); 
        v.addWidget(self.status_lbl); 
        v.addLayout(btns)

    def set_status(self, mode: str):
        text = {"idle":"Idle","run":"Running…","exited":"Exited","error":"Error"}.get(mode, "Idle")
        kind = {"idle":"idle","run":"run","exited":"exited","error":"error"}.get(mode, "idle")
        self.status_lbl.setText(f'Status: {pill(text, kind)}'); self.status_lbl.setObjectName("status")

    def launch(self):
        if self.proc is not None:
            QMessageBox.information(self, "Already running", "This UI is already running."); return
        self.proc = QProcess(self); self.proc.setWorkingDirectory(str(ROOT))
        self.proc.finished.connect(self._on_finished)
        self.proc.start(sys.executable, [str(self.script_path)])
        if not self.proc.waitForStarted(3000):
            self.set_status("error"); self.proc = None
        else:
            self.set_status("run"); self.run_btn.setEnabled(False); self.stop_btn.setEnabled(True)

    def stop(self):
        if not self.proc: return
        self.proc.terminate()
        if not self.proc.waitForFinished(2000): self.proc.kill()
        self._on_finished()

    def _on_finished(self):
        self.proc = None; self.set_status("exited")
        self.run_btn.setEnabled(True); self.stop_btn.setEnabled(False)

    def is_running(self) -> bool:
        return self.proc is not None

    def start_if_idle(self):
        if not self.is_running():
            self.launch()

    def stop_if_running(self):
        if self.is_running():
            self.stop()

class Launcher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Sounding App Launcher — {LAUNCHER_VERSION}")
        self.resize(820, 660)
        self.cards: List[AppCard] = []
        self.setStyleSheet(APP_STYLES)

        main = QVBoxLayout(self); main.setContentsMargins(16,16,16,16); main.setSpacing(12)

        # --- Header (logo + title + search + bulk buttons)
        head_row = QHBoxLayout()
        logo = QLabel(); logo.setFixedSize(44, 44); logo.setScaledContents(True)

        candidates = [ASSETS / "logo.png"] + list(ASSETS.glob("*.png")) + list(ASSETS.glob("*.jpg"))
        for p in candidates:
            if p.exists():
                pix = QPixmap(str(p))
                if not pix.isNull():
                    logo.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    break

        title_box = QVBoxLayout()
        title = QLabel("GasChem Africa — Sounding & Fuel Tools")
        title.setFont(QFont(title.font().family(), 17, QFont.Bold))
        subtitle = QLabel("Dark mode • Each module opens in its own process."); subtitle.setObjectName("subtitle")
        title_box.addWidget(title); title_box.addWidget(subtitle)

        self.search = QLineEdit(); self.search.setPlaceholderText("Search… (fuel, sludge, summary)")
        self.search.setObjectName("search"); self.search.textChanged.connect(self._apply_filter)

        self.open_all_btn = QPushButton("Open All"); self.open_all_btn.setToolTip("Start all visible modules")
        self.stop_all_btn = QPushButton("Stop All"); self.stop_all_btn.setToolTip("Stop all running modules")
        self.open_all_btn.clicked.connect(self.open_all_visible); self.stop_all_btn.clicked.connect(self.stop_all)

        head_row.addWidget(logo)
        head_row.addLayout(title_box)
        head_row.addStretch()
        head_row.addWidget(self.search)
        head_row.addWidget(self.open_all_btn)
        head_row.addWidget(self.stop_all_btn)

        line = QFrame(); line.setObjectName("line"); line.setFrameShape(QFrame.HLine)

        # --- Scroll with cards
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); self.list_layout = QVBoxLayout(content); self.list_layout.setSpacing(12)

        scripts = sorted(APP_DIR.glob("ui_*.py"))
        if not scripts:
            warn = QLabel(f"⚠️ No UI scripts found in {APP_DIR.as_posix()}"); warn.setStyleSheet("color:#ff9b9b; font-size:14px;")
            self.list_layout.addWidget(warn)
        else:
            for s in scripts:
                card = AppCard(s); self.cards.append(card); self.list_layout.addWidget(card)
        self.list_layout.addStretch(); scroll.setWidget(content)

        main.addLayout(head_row); main.addWidget(line); main.addWidget(scroll)

    # bulk actions
    def open_all_visible(self):
        for c in self.cards:
            if c.isVisible():
                c.start_if_idle()

    def stop_all(self):
        for c in self.cards:
            c.stop_if_running()

    # filtering
    def _apply_filter(self, text: str):
        t = text.strip().lower()
        for card in self.cards:
            name = card.title().lower(); path = card.script_path.name.lower()
            card.setVisible((t in name) or (t in path) or (t == ""))

def main():
    app = QApplication(sys.argv)
    w = Launcher()
    w.setWindowIcon(QIcon.fromTheme("applications-system"))
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
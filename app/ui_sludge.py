
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import sys
import types
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional
import sqlite3
import inspect

from PySide6.QtCore import Qt, QDate, QEvent
from PySide6.QtGui import QDoubleValidator, QFont,QColor, QBrush, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QAbstractItemView,
    QHeaderView, QDateEdit, QFileDialog, QMessageBox, QDialog, QListWidget, QListWidgetItem,
    QSplitter
)

# ------------------------ paths & import prep ------------------------
ROOT = Path(__file__).resolve().parents[1]          # .../sounding_app
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure Python sees "app" as a package even in script mode (python app/ui_sludge.py)
if "app" not in sys.modules:
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(APP_DIR)]
    sys.modules["app"] = pkg

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
OPS_DB_PATH = DATA_DIR / "ops.db"
CAP_DB_PATH = DATA_DIR / "sounding.db"   # capacity DB with table `readings`

# ------------------------ import _compute_volumes robustly ------------------------
try:
    # Preferred: module mode (`python -m app.ui_sludge`)
    from app.ops_cli import _compute_volumes  # type: ignore
except Exception:
    # Fallback: import by file path so script mode works (`python app/ui_sludge.py`)
    ops_path = APP_DIR / "ops_cli.py"
    spec = importlib.util.spec_from_file_location("app.ops_cli", str(ops_path))
    if not spec or not spec.loader:
        raise ImportError("Unable to import app.ops_cli._compute_volumes")
    ops_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops_cli)  # type: ignore
    _compute_volumes = ops_cli._compute_volumes  # type: ignore

# ------------------------ helpers ------------------------
GREEN = "background-color:#eaffea;"

# SLUDGE TANKS (go into Total Sludge)
SLUDGE_TANKS = [
    ("Waste Oil Tank",      "Waste Oil Tank",          0),
    ("Sludge Tank",         "Sludge Tank",             1),
    ("Incinerator Sludge",  "Incinerator Sludge Tank", 2),
    ("HFO Drain Tank",      "FO Drain",                3),
]

# OTHER TANKS (computed, but NOT included in Total Sludge)
OTHER_TANKS = [
    (6,  "Bilge Water Holding (m³)", "BLG_Bilge_Tank"),
    (7,  "ME Cond. Tk (m³)",         "ME cond tank"),
    (8,  "ME L.O. Sump (m³)",        "ME LO Sump Tank"),
    (9,  "Stuffing Box Drain (m³)",  "Stuffing_Box_Drain_Tk"),
    (10, "Under Piston Box (m³)",    "Under_Piston_Box_Drain"),
    (11, "UREA Drain Tk (m³)",       "Urea_Drain_Tank"),
    (12, "ER Cofferdam (m³)",        "CD2_N2_Cofferdam"),
]

# Fixed total sludge capacity from your sheet
TOTAL_SLUDGE_CAP = 59.20

def msg_warn(text: str):
    m = QMessageBox()
    m.setIcon(QMessageBox.Warning)
    m.setText(text)
    m.exec()

# ------------------------ DB (ops.db) ------------------------
DDL = """
CREATE TABLE IF NOT EXISTS daily_tanks (
  id INTEGER PRIMARY KEY,
  ddate TEXT NOT NULL UNIQUE,
  trim REAL,
  heel_token TEXT,
  vessel_status TEXT,

  -- inputs (cm) only for the 4 sludge tanks
  waste_oil_cm REAL,
  sludge_cm REAL,
  incin_sludge_cm REAL,
  hfo_drain_cm REAL,

  -- computed (m3)
  waste_oil_m3 REAL,
  sludge_m3 REAL,
  incin_sludge_m3 REAL,
  hfo_drain_m3 REAL,
  total_sludge_m3 REAL,
  free_space_sludge_m3 REAL,

  -- other tanks (m3)
  bilge_hold_m3 REAL,
  me_cond_m3 REAL,
  me_lo_sump_m3 REAL,
  stuffing_box_m3 REAL,
  under_piston_m3 REAL,
  urea_drain_m3 REAL,
  er_cofferdam_m3 REAL,

  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_daily_tanks_date ON daily_tanks (ddate DESC);
"""

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(OPS_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with db() as conn:
        conn.executescript(DDL)

def upsert_entry(row: Dict):
    cols = list(row.keys())
    placeholders = ", ".join([":" + c for c in cols])
    collist = ", ".join(cols)
    sql = f"""
        INSERT INTO daily_tanks ({collist})
        VALUES ({placeholders})
        ON CONFLICT(ddate) DO UPDATE SET
        {", ".join([f"{c}=excluded.{c}" for c in cols if c != "ddate"])}
    """
    with db() as conn:
        conn.execute(sql, row)

def fetch_last_n(n=30) -> List[Dict]:
    with db() as conn:
        cur = conn.execute(
            "SELECT * FROM daily_tanks ORDER BY ddate DESC LIMIT ?",
            (n,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

def fetch_all_dates() -> List[str]:
    with db() as conn:
        cur = conn.execute("SELECT ddate FROM daily_tanks ORDER BY ddate DESC")
        return [r[0] for r in cur.fetchall()]

def fetch_by_date(ddate: str) -> Optional[Dict]:
    with db() as conn:
        cur = conn.execute("SELECT * FROM daily_tanks WHERE ddate=?", (ddate,))
        r = cur.fetchone()
        if not r:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, r))

# ------------------------ capacity + ullage helpers (robust) ------------------------
def get_max_capacity(tank_name: str) -> Optional[float]:
    """
    Returns the maximum recorded volume for a tank using whatever 'volume' column exists.
    Tries volume_m3, then volume, then obs_vol_m3.
    """
    if not CAP_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(CAP_DB_PATH))
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(readings)")
        cols = {row[1] for row in cur.fetchall()}
        vol_col = next((c for c in ("volume_m3", "volume", "obs_vol_m3") if c in cols), None)
        if not vol_col:
            conn.close()
            return None

        cur.execute(f"SELECT MAX({vol_col}) FROM readings WHERE name=?", (tank_name,))
        r = cur.fetchone()
        conn.close()
        return float(r[0]) if r and r[0] is not None else None
    except Exception as e:
        print("Capacity DB error:", e)
        return None

def _guess_ullage_from_db(tank_name: str, cm: float) -> Optional[float]:
    """
    Estimate ullage when UI only has sounding:
    H ≈ max(sounding + ullage) across rows, then ullage = max(0, H - cm).
    Detects column names flexibly.
    """
    if not CAP_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(CAP_DB_PATH))
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(readings)")
        all_cols = {row[1].lower(): row[1] for row in cur.fetchall()}

        def pick(candidates):
            for c in candidates:
                if c in all_cols:
                    return all_cols[c]
            return None

        sound_col = pick(["sounding_cm", "sounding", "sound", "level"])
        ull_col   = pick(["ullage_cm", "ullage", "ull"])

        if not sound_col or not ull_col:
            conn.close()
            return None

        cur.execute(
            f"""SELECT {sound_col}, {ull_col}
                FROM readings
                WHERE name=? AND {sound_col} IS NOT NULL AND {ull_col} IS NOT NULL""",
            (tank_name,)
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None

        H = None
        for s, u in rows:
            try:
                su = float(s) + float(u)
                H = su if (H is None or su > H) else H
            except Exception:
                continue
        if H is None:
            return None

        ul = H - float(cm)
        return ul if ul > 0 else 0.0
    except Exception:
        return None

def compute_obs_vol_m3(cap_name: str, trim, heel_token: str, cm: float) -> float:
    """
    Call app.ops_cli._compute_volumes using keyword names (so arg order doesn't matter).
    If the CLI requires 'ullage', we supply it from the DB automatically.
    """
    fn = _compute_volumes
    sig = inspect.signature(fn)

    kwargs = {}
    needs_ullage = False

    for pname in sig.parameters:
        lp = pname.lower()
        if any(k in lp for k in ("tank", "cap", "name")):
            kwargs[pname] = cap_name
        elif "trim" in lp:
            kwargs[pname] = trim
        elif "heel" in lp:
            kwargs[pname] = heel_token  # e.g., "0", "0.5P", "0.5S"
        elif "ullage" in lp:
            needs_ullage = True
            kwargs[pname] = None
        elif ("cm" in lp) or ("sound" in lp) or ("level" in lp):
            kwargs[pname] = cm
        elif "metric" in lp:
            kwargs[pname] = True  # harmless default if present

    if needs_ullage:
        for pname in sig.parameters:
            if "ullage" in pname.lower():
                ul = _guess_ullage_from_db(cap_name, float(cm))
                kwargs[pname] = 0.0 if ul is None else float(ul)

    result = fn(**kwargs)

    # Normalize to float
    if isinstance(result, dict):
        for k in ("obs_vol_m3", "volume_m3", "vol_m3", "volume"):
            if k in result and result[k] is not None:
                return float(result[k])
        return 0.0
    if isinstance(result, (int, float)):
        return float(result)
    if isinstance(result, (list, tuple)) and result:
        for x in result:
            try:
                return float(x)
            except Exception:
                continue
    return 0.0

# ------------------------ UI ------------------------
class DailyTanksWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Daily Tanks Update")
        self.resize(1280, 860)

        # ----- top bar -----
        top = QHBoxLayout()
        top.addWidget(QLabel("Date:"))
        self.deDate = QDateEdit(QDate.currentDate())
        self.deDate.setCalendarPopup(True)
        self.deDate.setDisplayFormat("dd.MM.yyyy")
        self.deDate.setFixedWidth(120)
        top.addWidget(self.deDate)

        top.addSpacing(12)
        top.addWidget(QLabel("Trim:"))
        self.leTrim = QLineEdit("")
        self.leTrim.setFixedWidth(80)
        self.leTrim.setValidator(QDoubleValidator(-20.0, 20.0, 2))
        self.leTrim.setPlaceholderText("e.g. -1.9")
        top.addWidget(self.leTrim)

        top.addSpacing(12)
        top.addWidget(QLabel("Heel:"))
        self.leHeel = QLineEdit("")
        self.leHeel.setFixedWidth(100)
        self.leHeel.setValidator(QDoubleValidator(-20.0, 20.0, 2))
        self.leHeel.setPlaceholderText("0,5P / 0,5S / 0")
        top.addWidget(self.leHeel)

        top.addSpacing(12)
        top.addWidget(QLabel("Vessel status:"))
        self.cbStatus = QComboBox()
        self.cbStatus.addItems(["anch", "sea", "manoeuv", "port"])
        self.cbStatus.setCurrentIndex(0)
        top.addWidget(self.cbStatus)

        top.addStretch(1)

        # Recalc when trim/heel edited
        self.leTrim.editingFinished.connect(self._recalc_volumes)
        self.leHeel.editingFinished.connect(self._recalc_volumes)

        # ----- Today's Input table -----
        gEdit = QGroupBox("Today's Input")
        vEdit = QVBoxLayout(gEdit)

        headers_full = [
            "Waste Oil Tank", "Sludge Tank", "Incinerator Sludge Tank", "HFO Drain Tank",
            "Total Sludge (m³)", "Free Space (m³)",
            "Bilge Water Holding (m³)", "ME Cond. Tk (m³)", "ME L.O. Sump (m³)",
            "Stuffing Box Drain (m³)", "Under Piston Box (m³)", "UREA Drain Tk (m³)", "ER Cofferdam (m³)",
            "Notes"
        ]
        headers_disp = [
            "Waste Oil\nTank", "Sludge\nTank", "Incinerator\nSludge Tank", "HFO Drain\nTank",
            "Total\nSludge (m³)", "Free\nSpace (m³)",
            "Bilge Water\nHolding (m³)", "ME Cond.\nTk (m³)", "ME L.O.\nSump (m³)",
            "Stuffing Box\nDrain (m³)", "Under Piston\nBox (m³)", "UREA Drain\nTk (m³)", "ER\nCofferdam (m³)",
            "Notes"
        ]

        self.tbl = QTableWidget(3, len(headers_full))
        self.tbl.setHorizontalHeaderLabels(headers_disp)
        for c, full in enumerate(headers_full):
            it = self.tbl.horizontalHeaderItem(c)
            if it:
                it.setToolTip(full)

        self.tbl.verticalHeader().setVisible(True)
        self.tbl.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.tbl.setAlternatingRowColors(True)

        # Header styling to match
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setMinimumSectionSize(80)
        hh.setStretchLastSection(False)
        hh_font = QFont(); hh_font.setPointSize(10); hh_font.setBold(True)
        hh.setFont(hh_font)
        hh.setFixedHeight(44)

        # Column widths tuned for readability (feel free to tweak)
        col_widths = [110, 110, 140, 110, 95, 95, 150, 115, 125, 145, 140, 125, 125, 220]
        for i, w in enumerate(col_widths):
            self.tbl.setColumnWidth(i, w)

        # Vertical rows
        self.tbl.setVerticalHeaderLabels(["Max Capacity (m³)", "Sounding (cm)", "Volume (m³)"])
        self.tbl.setRowHeight(0, 28)
        self.tbl.setRowHeight(1, 34)
        self.tbl.setRowHeight(2, 34)

        # column indexes
        total_col = 4
        free_col  = 5

        # Row 0: fixed Total Sludge capacity (59.20 m³)
        it_total_cap = QTableWidgetItem(f"{TOTAL_SLUDGE_CAP:.2f}")
        it_total_cap.setFlags(Qt.ItemIsEnabled)
        it_total_cap.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(0, total_col, it_total_cap)

        # Row 1 placeholders (percent for Total, m³ for Free)
        it_total_pct = QTableWidgetItem("0.0%")
        it_total_pct.setFlags(Qt.ItemIsEnabled)
        it_total_pct.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(1, total_col, it_total_pct)

        it_free_row1 = QTableWidgetItem(f"{TOTAL_SLUDGE_CAP:.2f}")
        it_free_row1.setFlags(Qt.ItemIsEnabled)
        it_free_row1.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(1, free_col, it_free_row1)

       # Row 0: max capacities (sludge tanks)
        for _label, cap_name, col in SLUDGE_TANKS:
            max_vol = get_max_capacity(cap_name)
            txt = f"{max_vol:.2f}" if max_vol is not None else "—"
            it = QTableWidgetItem(txt)
            it.setFlags(Qt.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QBrush(QColor("red")))
            self.tbl.setItem(0, col, it)

        # Row 0: max capacities (other tanks)
        for col, _label, cap_name in OTHER_TANKS:
            max_vol = get_max_capacity(cap_name)
            txt = f"{max_vol:.2f}" if max_vol is not None else "—"
            it = QTableWidgetItem(txt)
            it.setFlags(Qt.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QBrush(QColor("red")))
            self.tbl.setItem(0, col, it)

        # Row 1: sounding inputs (sludge tanks)
        self._sound_inputs: Dict[str, QLineEdit] = {}
        for label, _cap, col in SLUDGE_TANKS:
            le = QLineEdit("")
            le.setPlaceholderText("cm")
            le.setStyleSheet(GREEN)
            le.setValidator(QDoubleValidator(0.0, 5000.0, 2))
            le.setAlignment(Qt.AlignCenter)
            self.tbl.setCellWidget(1, col, le)
            self._sound_inputs[label] = le
            le.textChanged.connect(self._recalc_volumes)

        # Row 1: sounding inputs (other tanks)
        for col, label, _cap in OTHER_TANKS:
            le = QLineEdit("")
            le.setPlaceholderText("cm")
            le.setStyleSheet(GREEN)
            le.setValidator(QDoubleValidator(0.0, 5000.0, 2))
            le.setAlignment(Qt.AlignCenter)
            self.tbl.setCellWidget(1, col, le)
            self._sound_inputs[label] = le
            le.textChanged.connect(self._recalc_volumes)

        
            # Row 1: mirror of computed sludge totals (read-only)
            it_total = QTableWidgetItem("—")
            it_total.setFlags(Qt.ItemIsEnabled)
            it_total.setTextAlignment(Qt.AlignCenter)
            font_total = QFont()
            font_total.setBold(True)
            it_total.setFont(font_total)
            it_total.setForeground(QBrush(QColor("red")))   # 🔴 Bold Red
            self.tbl.setItem(1, 4, it_total)  # Total Sludge

            it_free = QTableWidgetItem("—")
            it_free.setFlags(Qt.ItemIsEnabled)
            it_free.setTextAlignment(Qt.AlignCenter)
            font_free = QFont()
            font_free.setBold(True)
            it_free.setFont(font_free)
            it_free.setForeground(QBrush(QColor("blue")))   # 🔵 Bold Blue
            self.tbl.setItem(1, 5, it_free)   # Free Space

        # Notes (row 1, last column)
        self.leNotes = QLineEdit("")
        self.tbl.setCellWidget(1, len(headers_full) - 1, self.leNotes)

        # Row 2: computed volumes (sludge tanks)
        self._vol_cells: Dict[str, QTableWidgetItem] = {}
        for label, _cap, col in SLUDGE_TANKS:
            it = QTableWidgetItem("0.00")
            it.setFlags(Qt.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(2, col, it)
            self._vol_cells[label] = it

        # Row 2: computed volumes (other tanks)
        for col, label, _cap in OTHER_TANKS:
            it = QTableWidgetItem("0.00")
            it.setFlags(Qt.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(2, col, it)
            self._vol_cells[label] = it

        # Row 2: totals
        self.itTotalVol = QTableWidgetItem("0.00")
        self.itTotalVol.setFlags(Qt.ItemIsEnabled)
        self.itTotalVol.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(2, 4, self.itTotalVol)

        self.itFree = QTableWidgetItem(f"{TOTAL_SLUDGE_CAP:.2f}")
        self.itFree.setFlags(Qt.ItemIsEnabled)
        self.itFree.setTextAlignment(Qt.AlignCenter)
        self.tbl.setItem(2, 5, self.itFree)

        vEdit.addWidget(self.tbl)
        self._fit_input_table_height()

        # buttons
        hbEdit = QHBoxLayout()
        self.btnSave   = QPushButton("Save (store in history)")
        self.btnDup    = QPushButton("Duplicate Yesterday")
        self.btnClear  = QPushButton("Clear")
        self.btnExport = QPushButton("Export CSV")
        hbEdit.addWidget(self.btnSave); hbEdit.addWidget(self.btnDup)
        hbEdit.addWidget(self.btnClear); hbEdit.addWidget(self.btnExport)
        vEdit.addLayout(hbEdit)

        # ----- History table -----
        gHist = QGroupBox("History — last 30 entries")
        vHist = QVBoxLayout(gHist)

        hist_headers_full = [
            "Date", "Trim", "Heel", "Vessel",
            "Waste Oil (m³)", "Sludge (m³)", "Incinerator Sludge (m³)", "HFO Drain (m³)",
            "Total Sludge (m³)", "Free Space (m³)",
            "Bilge Water Holding (m³)", "ME Condenser Tank (m³)", "ME L.O. Sump (m³)",
            "Stuffing Box Drain (m³)", "Under Piston Box (m³)", "UREA Drain Tank (m³)", "ER Cofferdam (m³)",
            "Notes"
        ]
        hist_headers_disp = [
            "Date", "Trim", "Heel", "Vessel",
            "Waste Oil\n(m³)", "Sludge\n(m³)", "Incin. Sludge\n(m³)", "HFO Drain\n(m³)",
            "Total Sludge\n(m³)", "Free Space\n(m³)",
            "Bilge Water\nHolding (m³)", "ME Cond.\nTk (m³)", "ME L.O.\nSump (m³)",
            "Stuff. Box\nDrain (m³)", "Under Piston\nBox (m³)", "UREA Drain\nTk (m³)", "ER\nCofferdam (m³)",
            "Notes"
        ]

        self.tblHist = QTableWidget(0, len(hist_headers_disp))
        self.tblHist.setHorizontalHeaderLabels(hist_headers_disp)
        self.tblHist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tblHist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tblHist.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tblHist.verticalHeader().setVisible(False)

        hh2 = self.tblHist.horizontalHeader()
        hh2_font = QFont(); hh2_font.setPointSize(10); hh2_font.setBold(True)
        hh2.setFont(hh2_font)
        hh2.setFixedHeight(44)
        hh2.setSectionResizeMode(QHeaderView.Fixed)

        # tooltips with full names
        for c, full in enumerate(hist_headers_full):
            it = self.tblHist.horizontalHeaderItem(c)
            if it:
                it.setToolTip(full)

        # Tuned widths
        #            Date Trim Heel Vessel  W.Oil Sludge Incin HFO  Total Free Bilge ME C ME LO Stuff Under Urea  ER   Notes
        col_w =    [  92,  65,  66,   65,   100,  100,   120, 100, 110,  110,  130, 110,  120,  120,  120, 120, 120, 180]
        for i, w in enumerate(col_w):
            self.tblHist.setColumnWidth(i, w)

        self.tblHist.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tblHist.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tblHist.setWordWrap(False)

        vHist.addWidget(self.tblHist)

        # ----- root layout with splitter -----
        root = QVBoxLayout(self)
        root.addLayout(top)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        split.addWidget(gEdit)
        split.addWidget(gHist)
        split.setSizes([280, 9999])  # more space to history
        root.addWidget(split)

        # wire
        init_db()
        self.btnSave.clicked.connect(self.on_save)
        self.btnDup.clicked.connect(self.on_duplicate)
        self.btnClear.clicked.connect(self.on_clear)
        self.btnExport.clicked.connect(self.on_export_csv)
        self.btnRetrieve = QPushButton("Retrieve…")
        vHist.addWidget(self.btnRetrieve)
        self.btnRetrieve.clicked.connect(self.on_retrieve)

        # initial compute & history
        self._recalc_volumes()
        self.refresh_history()

    # ----- sizing helpers -----
    def _fit_input_table_height(self):
        # Make the green table only as tall as its rows + header
        hh = self.tbl.horizontalHeader().height()
        rows_h = sum(self.tbl.rowHeight(r) for r in range(self.tbl.rowCount()))
        margins = 2 * self.tbl.frameWidth()
        self.tbl.setFixedHeight(hh + rows_h + margins + 2)

    # ------------------ helpers ------------------
    @staticmethod
    def _pf(s: str) -> float:
        try:
            return float(str(s).replace(",", "."))
        except Exception:
            return 0.0

    def _heel_token(self) -> str:
        s = (self.leHeel.text() or "").strip().upper().replace(" ", "")
        if not s:
            return "0"
        import re
        m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([PS])\s*$", s, re.I)
        if m:
            val = m.group(1).replace(",", "."); side = m.group(2).upper()
            try:
                float(val)
            except ValueError:
                return "0"
            return f"{val}{side}"
        try:
            val = float(s.replace(",", "."))
        except ValueError:
            if s in ("P", "S"):
                return "0" + s
            return "0"
        if val == 0:
            return "0"
        return f"{abs(val)}{'P' if val > 0 else 'S'}"

    def _recalc_volumes(self):
        trim = self._pf(self.leTrim.text() or "0")
        heel = self._heel_token()

        sludge_total = 0.0

        # Sludge tanks (count toward total)
        for label, cap_name, _col in SLUDGE_TANKS:
            cm_txt = self._sound_inputs[label].text().strip()
            cm = self._pf(cm_txt) if cm_txt else None
            vol = compute_obs_vol_m3(cap_name, trim, heel, cm) if cm is not None else 0.0
            self._vol_cells[label].setText(f"{vol:.2f}")
            sludge_total += vol

        # Other tanks (computed, but NOT added to sludge total)
        for _col, label, cap_name in OTHER_TANKS:
            cm_txt = self._sound_inputs[label].text().strip()
            cm = self._pf(cm_txt) if cm_txt else None
            vol = compute_obs_vol_m3(cap_name, trim, heel, cm) if cm is not None else 0.0
            self._vol_cells[label].setText(f"{vol:.2f}")

        # Row 2 (computed m³)
        self.itTotalVol.setText(f"{sludge_total:.2f}")
        free = max(0.0, TOTAL_SLUDGE_CAP - sludge_total)
        self.itFree.setText(f"{free:.2f}")

        # Ensure Row 0, col 4 shows fixed capacity 59.20 m³
        cap_item = self.tbl.item(0, 4)
        if not cap_item:
            cap_item = QTableWidgetItem()
            cap_item.setFlags(Qt.ItemIsEnabled)
            cap_item.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(0, 4, cap_item)
        cap_item.setText(f"{TOTAL_SLUDGE_CAP:.2f}")

        # Row 1 mirrors:
        pct = (sludge_total / TOTAL_SLUDGE_CAP * 100.0) if TOTAL_SLUDGE_CAP > 0 else 0.0

        it_total_pct = self.tbl.item(1, 4)
        if not it_total_pct:
            it_total_pct = QTableWidgetItem()
            it_total_pct.setFlags(Qt.ItemIsEnabled)
            it_total_pct.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(1, 4, it_total_pct)
        it_total_pct.setText(f"{pct:.1f}%")

        it_free_row1 = self.tbl.item(1, 5)
        if not it_free_row1:
            it_free_row1 = QTableWidgetItem()
            it_free_row1.setFlags(Qt.ItemIsEnabled)
            it_free_row1.setTextAlignment(Qt.AlignCenter)
            self.tbl.setItem(1, 5, it_free_row1)
        it_free_row1.setText(f"{free:.2f}")

    def _collect_row(self) -> Dict:
        def get_m3(label: str) -> float:
            try:
                return float(self._vol_cells[label].text())
            except Exception:
                return 0.0

        row = dict(
            ddate=self.deDate.date().toString("yyyy-MM-dd"),
            trim=self._pf(self.leTrim.text() or "0"),
            heel_token=self._heel_token(),
            vessel_status=self.cbStatus.currentText(),

            # store cm only for the 4 sludge tanks
            waste_oil_cm=self._pf(self._sound_inputs["Waste Oil Tank"].text() or "0"),
            sludge_cm=self._pf(self._sound_inputs["Sludge Tank"].text() or "0"),
            incin_sludge_cm=self._pf(self._sound_inputs["Incinerator Sludge"].text() or "0"),
            hfo_drain_cm=self._pf(self._sound_inputs["HFO Drain Tank"].text() or "0"),

            # computed m³
            waste_oil_m3=get_m3("Waste Oil Tank"),
            sludge_m3=get_m3("Sludge Tank"),
            incin_sludge_m3=get_m3("Incinerator Sludge"),
            hfo_drain_m3=get_m3("HFO Drain Tank"),
            total_sludge_m3=self._pf(self.itTotalVol.text()),
            free_space_sludge_m3=self._pf(self.itFree.text()),

            # other computed m³
            bilge_hold_m3=get_m3("Bilge Water Holding (m³)"),
            me_cond_m3=get_m3("ME Cond. Tk (m³)"),
            me_lo_sump_m3=get_m3("ME L.O. Sump (m³)"),
            stuffing_box_m3=get_m3("Stuffing Box Drain (m³)"),
            under_piston_m3=get_m3("Under Piston Box (m³)"),
            urea_drain_m3=get_m3("UREA Drain Tk (m³)"),
            er_cofferdam_m3=get_m3("ER Cofferdam (m³)"),

            notes=(self.leNotes.text() or "").strip(),
        )
        return row

    # ------------------ actions ------------------
    def on_save(self):
        self._recalc_volumes()
        row = self._collect_row()
        try:
            upsert_entry(row)
            self.refresh_history()
        except Exception as e:
            msg_warn(f"Save failed: {e}")

    def on_duplicate(self):
        rows = fetch_last_n(1)
        if not rows:
            msg_warn("No previous entry to duplicate.")
            return
        last = rows[0]
        self.deDate.setDate(QDate.currentDate())

        def set_cm(label, key):
            val = last.get(key)
            self._sound_inputs[label].setText("" if not val else f"{float(val):.0f}")

        # Only the 4 sludge cm values are stored historically
        set_cm("Waste Oil Tank", "waste_oil_cm")
        set_cm("Sludge Tank", "sludge_cm")
        set_cm("Incinerator Sludge", "incin_sludge_cm")
        set_cm("HFO Drain Tank", "hfo_drain_cm")

        self.leTrim.setText("" if last.get("trim") is None else f"{last.get('trim', 0.0):.2f}")
        self.leHeel.setText(last.get("heel_token", "") or "")
        self.cbStatus.setCurrentIndex(max(0, self.cbStatus.findText(last.get("vessel_status", "anch"))))

        # Notes can be carried over
        self.leNotes.setText(last.get("notes", ""))
        self._recalc_volumes()

    def on_clear(self):
        for le in self._sound_inputs.values():
            le.setText("")
        self.leNotes.setText("")
        self.itTotalVol.setText("0.00")
        self.itFree.setText(f"{TOTAL_SLUDGE_CAP:.2f}")

    def on_export_csv(self):
        self._recalc_volumes()
        row = self._collect_row()
        suggested = f"sludge_{row['ddate']}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", suggested, "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["field", "value"])
                for k, v in row.items():
                    writer.writerow([k, v])
        except Exception as e:
            msg_warn(f"Export failed: {e}")

    def on_retrieve(self):
        dates = fetch_all_dates()
        if not dates:
            msg_warn("No saved entries yet.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Retrieve entry")
        dlg.resize(360, 420)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        v.addWidget(lst)
        for d in dates:
            QListWidgetItem(d, lst)

        hb = QHBoxLayout()
        okb = QPushButton("Load")
        cb = QPushButton("Cancel")
        hb.addStretch(1)
        hb.addWidget(okb)
        hb.addWidget(cb)
        v.addLayout(hb)

        okb.clicked.connect(dlg.accept)
        cb.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.Accepted:
            return
        it = lst.currentItem()
        if not it:
            return

        data = fetch_by_date(it.text())
        if not data:
            msg_warn("Selected date not found.")
            return

        # restore data into UI
        self.deDate.setDate(QDate.fromString(data["ddate"], "yyyy-MM-dd"))
        self.leTrim.setText(f'{data.get("trim", 0.0):.2f}')
        self.leHeel.setText(data.get("heel_token", ""))
        self.cbStatus.setCurrentIndex(max(0, self.cbStatus.findText(data.get("vessel_status", "anch"))))

        def s(txt): return "" if txt in (None, 0.0) else f"{float(txt):.0f}"
        self._sound_inputs["Waste Oil Tank"].setText(s(data.get("waste_oil_cm")))
        self._sound_inputs["Sludge Tank"].setText(s(data.get("sludge_cm")))
        self._sound_inputs["Incinerator Sludge"].setText(s(data.get("incin_sludge_cm")))
        self._sound_inputs["HFO Drain Tank"].setText(s(data.get("hfo_drain_cm")))

        self.leNotes.setText(data.get("notes", ""))
        self._recalc_volumes()

    def refresh_history(self):
        rows = fetch_last_n(30)
        self.tblHist.setRowCount(len(rows))
        for r, item in enumerate(rows):
            def fmt(x, nd=2):
                try:
                    v = float(x)
                    return f"{v:.{nd}f}"
                except Exception:
                    return "0.00"

            self.tblHist.setItem(r, 0, QTableWidgetItem(item.get("ddate", "")))
            self.tblHist.setItem(r, 1, QTableWidgetItem(fmt(item.get("trim"), 2)))
            self.tblHist.setItem(r, 2, QTableWidgetItem(item.get("heel_token", "")))
            self.tblHist.setItem(r, 3, QTableWidgetItem(item.get("vessel_status", "")))
            self.tblHist.setItem(r, 4, QTableWidgetItem(fmt(item.get("waste_oil_m3"))))
            self.tblHist.setItem(r, 5, QTableWidgetItem(fmt(item.get("sludge_m3"))))
            self.tblHist.setItem(r, 6, QTableWidgetItem(fmt(item.get("incin_sludge_m3"))))
            self.tblHist.setItem(r, 7, QTableWidgetItem(fmt(item.get("hfo_drain_m3"))))
            self.tblHist.setItem(r, 8, QTableWidgetItem(fmt(item.get("total_sludge_m3"))))
            self.tblHist.setItem(r, 9, QTableWidgetItem(fmt(item.get("free_space_sludge_m3"))))
            self.tblHist.setItem(r,10, QTableWidgetItem(fmt(item.get("bilge_hold_m3"))))
            self.tblHist.setItem(r,11, QTableWidgetItem(fmt(item.get("me_cond_m3"))))
            self.tblHist.setItem(r,12, QTableWidgetItem(fmt(item.get("me_lo_sump_m3"))))
            self.tblHist.setItem(r,13, QTableWidgetItem(fmt(item.get("stuffing_box_m3"))))
            self.tblHist.setItem(r,14, QTableWidgetItem(fmt(item.get("under_piston_m3"))))
            self.tblHist.setItem(r,15, QTableWidgetItem(fmt(item.get("urea_drain_m3"))))
            self.tblHist.setItem(r,16, QTableWidgetItem(fmt(item.get("er_cofferdam_m3"))))
            self.tblHist.setItem(r,17, QTableWidgetItem(item.get("notes", "")))

            # Right-align numeric columns for readability
            numeric_cols = [1,4,5,6,7,8,9,10,11,12,13,14,15,16]
            for c in numeric_cols:
                it = self.tblHist.item(r, c)
                if it:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

# ------------------------ main ------------------------
def main():
    if not CAP_DB_PATH.exists():
        msg_warn(f"Capacity DB not found:\n{CAP_DB_PATH}\n(Load or point to the correct DB.)")
    app = QApplication(sys.argv or [])
    w = DailyTanksWindow()
    w.show()
    app.exec()

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import re
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QDoubleValidator, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QGroupBox, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QLabel, QLineEdit, QSpinBox, QRadioButton, QPushButton, QDateEdit,
    QMessageBox, QInputDialog, QWidget as QW, QSizePolicy
)

# ----- import path so "from app..." works when run as script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ops_cli import _compute_volumes  # base+heel from capacity DB

DB_CAP_PATH = ROOT / "data" / "sounding.db"
DB_OPS_PATH = ROOT / "data" / "ops.db"

YELLOW = "background-color:#fff6cc;"

# ----------------------- GROUP SPECS -----------------------
FRESH_WATER_ROWS = [
    ("FW1S_FWT_S_1", "F.W.T. 1 (S)", 45.6),
    ("FWT_S_2",      "F.W.T. 2 (S)", 54.1),
    ("FWT_Tech_W",   "TECH. W.T. (S)", 84.6),
]

UREA_ROWS = [
    ("UREA_P", "UREA Storage Tank Port", 36.8),
    ("UREA_S", "UREA Storage Tank Stbd", 28.6),
]

MISC_ROWS = [
    ("gray_holding_tank",     "Gray Water Hold Tk",     23.3),
    ("Sewage_Holding_Tank",   "Sewage Hold. Tk",        16.3),
    ("ER_Sewage_Hold_Tk",     "ER Sewage Hold Tk",      11.3),
    ("ME cond tank",          "ME Condensate Tk",       23.5),
    ("BLG_Bilge_Tank",        "Bilge Water Holding Tk", 53.2),
    ("FO OVERFLOW",           "F.O. Overflow Tk",       23.1),
    ("FO Drain",              "F.O. Drain Tk",          21.2),
    ("Urea_Drain_Tank",       "UREA Drain Tk",           6.8),
]

OTHER_ROWS = [
    ("Incinerator_MGO_Tank_clean", "Incinerator MGO Tk", 0.0),
    ("EGMGO_E-G_MGO_Tank",         "E/G MGO Tk",         0.0),
    ("CD2_N2_Cofferdam",           "C.D. N2 Cofferdam",  0.0),
]


def msg(text: str):
    m = QMessageBox()
    m.setIcon(QMessageBox.Warning)
    m.setText(text)
    m.exec()


def info(text: str):
    m = QMessageBox()
    m.setIcon(QMessageBox.Information)
    m.setText(text)
    m.exec()


def short_code(full_code: str) -> str:
    tok = re.split(r"[ _]", full_code)[0]
    return tok[:6]


# ---------------- Table Widget ----------------
class SimpleTankTable(QTableWidget):
    COLS = [
        "Tk", "Description", "100% Full (m³)", "At % (m³)",
        "Mode", "Level (cm)", "Observed Vol (m³)", "Calc"
    ]

    def __init__(self, rows_spec: List[Tuple[str, str, float]], parent=None):
        super().__init__(parent)
        self.rows_spec = rows_spec
        self.setColumnCount(len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.setRowCount(len(rows_spec))

        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setAlternatingRowColors(True)

        # Compact, but slightly taller for better fill
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(False)
        self.horizontalHeader().setFont(header_font)
        self.horizontalHeader().setFixedHeight(28)
        self.verticalHeader().setDefaultSectionSize(28)  # row height bumped from 24 → 28

        # Tight padding + non-stretch vertical policy
        self.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("QTableWidget { padding: 0px; }")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._row_widgets: List[Dict[str, Any]] = []

        self.setColumnWidth(0, 64)   # Tk
        self.setColumnWidth(5, 110)  # Level
        self.setColumnWidth(7, 64)   # Calc button

        for r, spec in enumerate(rows_spec):
            self._build_row(r, spec)

        # Height: set a MINIMUM so it can grow if needed, but never leave big gaps
        self._adjust_height()

    def _adjust_height(self):
        header_h = self.horizontalHeader().height()
        row_h = self.verticalHeader().defaultSectionSize()
        rows = max(1, self.rowCount())
        padding = 8  # frame + grid
        total = header_h + rows * row_h + padding
        self.setMinimumHeight(total)

    def _build_row(self, r: int, spec):
        widgets: Dict[str, Any] = {}
        code, desc, full100 = spec

        it_code = QTableWidgetItem(short_code(code))
        it_code.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 0, it_code)

        it_desc = QTableWidgetItem(desc)
        it_desc.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 1, it_desc)

        it_full = QTableWidgetItem(f"{float(full100):.1f}")
        it_full.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 2, it_full)

        it_at = QTableWidgetItem("-")
        it_at.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 3, it_at)

        # Mode: Sounding / Ullage
        w_mode = QW()
        hb = QHBoxLayout(w_mode)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(4)
        rb_s = QRadioButton("So")
        rb_u = QRadioButton("Ull")
        rb_s.setChecked(True)
        hb.addWidget(rb_s)
        hb.addWidget(rb_u)
        hb.addStretch(1)
        self.setCellWidget(r, 4, w_mode)
        widgets["rb_s"], widgets["rb_u"] = rb_s, rb_u

        sp_level = QSpinBox()
        sp_level.setRange(0, 3000)
        sp_level.setValue(0)
        sp_level.setStyleSheet(YELLOW)
        self.setCellWidget(r, 5, sp_level)
        widgets["level"] = sp_level

        it_obs = QTableWidgetItem("-")
        it_obs.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 6, it_obs)
        widgets["vobs"] = it_obs

        btn = QPushButton("Calc")
        btn.setMinimumWidth(52)
        btn.clicked.connect(lambda _=False, row=r: self.calc_one(row))
        self.setCellWidget(r, 7, btn)

        widgets["code_full"] = code
        widgets["full100"] = float(full100)
        widgets["fill_col_item"] = it_at
        self._row_widgets.append(widgets)

    def get_row_inputs(self, row: int):
        w = self._row_widgets[row]
        code = w["code_full"]
        is_sounding = w["rb_s"].isChecked()
        level_cm = float(w["level"].value())
        return code, is_sounding, level_cm

    def calc_one(self, row: int, trim: float = 0.0, heel: Optional[str] = None):
        code, is_sounding, level_cm = self.get_row_inputs(row)
        try:
            _, _, v_obs = _compute_volumes(
                name=code,
                trim=trim,
                sounding=level_cm if is_sounding else None,
                ullage=level_cm if not is_sounding else None,
                heel=heel
            )
        except Exception:
            self._row_widgets[row]["vobs"].setText("-")
            raise
        v_obs = max(0.0, v_obs)
        self._row_widgets[row]["vobs"].setText(f"{v_obs:.3f}")

    def update_fill_column(self, fill_pct: float):
        scale = max(0.0, min(100.0, fill_pct)) / 100.0
        for r, w in enumerate(self._row_widgets):
            val = w["full100"] * scale
            w["fill_col_item"].setText(f"{val:.1f}")
        self.setHorizontalHeaderItem(3, QTableWidgetItem(f"At {fill_pct:.0f}% (m³)"))

    def iter_rows(self):
        for r, w in enumerate(self._row_widgets):
            mode = "So" if w["rb_s"].isChecked() else "Ull"
            level = float(w["level"].value())
            vobs_txt = w["vobs"].text()
            at_txt = w["fill_col_item"].text()
            yield {
                "code": w["code_full"],
                "desc": self.item(r, 1).text() if self.item(r, 1) else "",
                "full100": w["full100"],
                "at_fill_m3": float(at_txt) if at_txt not in ("-", "") else None,
                "mode": mode,
                "level_cm": level,
                "v_obs": float(vobs_txt) if vobs_txt not in ("-", "") else None,
            }

    def apply_rows(self, rows: List[Dict[str, Any]]):
        idx_by_code = {w["code_full"]: i for i, w in enumerate(self._row_widgets)}
        for row in rows:
            code = row["code"]
            if code not in idx_by_code:
                continue
            i = idx_by_code[code]
            w = self._row_widgets[i]
            if row.get("mode", "So") == "So":
                w["rb_s"].setChecked(True)
            else:
                w["rb_u"].setChecked(True)
            w["level"].setValue(int(row.get("level_cm") or 0))
            if row.get("v_obs") is not None:
                w["vobs"].setText(f"{row['v_obs']:.3f}")
            if row.get("at_fill_m3") is not None:
                w["fill_col_item"].setText(f"{row['at_fill_m3']:.1f}")

    def sum_observed(self) -> float:
        total = 0.0
        for r, w in enumerate(self._row_widgets):
            txt = w["vobs"].text()
            if txt not in ("-", ""):
                try:
                    total += float(txt)
                except Exception:
                    pass
        return total


# ---------------- Main Window ----------------
class MainWindow(QWidget):
    _HEEL_TOKEN_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([PS])\s*$", re.I)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Other Tanks – Sheet")
        self.resize(1220, 720)  # a bit shorter so content naturally fills

        # Top bar (compact)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 2)
        top.setSpacing(8)

        top.addWidget(QLabel("Date:"))
        self.deDate = QDateEdit(QDate.currentDate())
        self.deDate.setCalendarPopup(True)
        self.deDate.setDisplayFormat("dd.MM.yyyy")
        self.deDate.setFixedWidth(110)
        top.addWidget(self.deDate)

        top.addSpacing(8)
        top.addWidget(QLabel("Trim:"))
        self.leTrim = QLineEdit("0.00")
        self.leTrim.setFixedWidth(70)
        self.leTrim.setValidator(QDoubleValidator(-10.0, 10.0, 2))
        top.addWidget(self.leTrim)

        top.addSpacing(8)
        top.addWidget(QLabel("Heel:"))
        self.leHeel = QLineEdit("0")
        self.leHeel.setFixedWidth(90)
        self.leHeel.setValidator(QDoubleValidator(-10.0, 10.0, 2))
        self.leHeel.setPlaceholderText("0,5P / 0,5S / -1.2")
        top.addWidget(self.leHeel)

        def add_fill(label: str) -> QLineEdit:
            top.addSpacing(10)
            top.addWidget(QLabel(label))
            le = QLineEdit("0.00")
            le.setFixedWidth(70)
            le.setValidator(QDoubleValidator(0.0, 100.0, 2))
            top.addWidget(le)
            return le

        self.leFillFW = add_fill("Fill % (FW):")
        self.leFillUrea = add_fill("Fill % (UREA):")
        self.leFillMisc = add_fill("Fill % (Misc):")
        self.leFillOther = add_fill("Fill % (Other):")

        top.addStretch(1)

        # Group title font only (bold)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)

        def compact_group(title: str) -> Tuple[QGroupBox, QVBoxLayout]:
            g = QGroupBox(title)
            g.setFont(title_font)
            g.setFlat(True)
            g.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)  # stop vertical stretch
            v = QVBoxLayout(g)
            v.setContentsMargins(4, 4, 4, 4)
            v.setSpacing(2)
            return g, v

        # Build groups in order: FW, UREA, Misc, Other
        self.gFW, vFW = compact_group("Fresh Water Tanks")
        self.tblFW = SimpleTankTable(FRESH_WATER_ROWS)
        vFW.addWidget(self.tblFW)

        self.gUrea, vUr = compact_group("UREA Tanks")
        self.tblUrea = SimpleTankTable(UREA_ROWS)
        vUr.addWidget(self.tblUrea)

        self.gMisc, vMi = compact_group("Miscellaneous Tanks")
        self.tblMisc = SimpleTankTable(MISC_ROWS)
        vMi.addWidget(self.tblMisc)

        self.gOther, vOt = compact_group("Other Tanks")
        self.tblOther = SimpleTankTable(OTHER_ROWS)
        vOt.addWidget(self.tblOther)

        # Bottom bar
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 6, 0, 0)
        bottom.setSpacing(8)

        self.btnSave = QPushButton("Save")
        self.btnSave.clicked.connect(self.save_to_ops)
        bottom.addWidget(self.btnSave)

        self.btnLoad = QPushButton("Retrieve")
        self.btnLoad.clicked.connect(self.retrieve_from_ops)
        bottom.addWidget(self.btnLoad)

        self.btnCalc = QPushButton("Calculate")
        self.btnCalc.clicked.connect(self.calc_all)
        bottom.addWidget(self.btnCalc)

        bottom.addStretch(1)
        self.lblGrand = QLabel("FW: 0   UREA: 0   Misc: 0   Other: 0   Grand Total: 0 (m³)")
        bottom.addWidget(self.lblGrand)
        bottom.addStretch(1)
        bottom.addWidget(QLabel(f"DB: {DB_CAP_PATH}"))

        # Compose layout (compact, no extra vertical gaps)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.gFW)
        layout.addWidget(self.gUrea)
        layout.addWidget(self.gMisc)
        layout.addWidget(self.gOther)
        layout.addLayout(bottom)

        # Wiring — update “At %” headers only, no recalc
        self.leFillFW.editingFinished.connect(
            lambda: self.tblFW.update_fill_column(self._current_fill(self.leFillFW))
        )
        self.leFillUrea.editingFinished.connect(
            lambda: self.tblUrea.update_fill_column(self._current_fill(self.leFillUrea))
        )
        self.leFillMisc.editingFinished.connect(
            lambda: self.tblMisc.update_fill_column(self._current_fill(self.leFillMisc))
        )
        self.leFillOther.editingFinished.connect(
            lambda: self.tblOther.update_fill_column(self._current_fill(self.leFillOther))
        )

        # init headers
        self.tblFW.update_fill_column(0.0)
        self.tblUrea.update_fill_column(0.0)
        self.tblMisc.update_fill_column(0.0)
        self.tblOther.update_fill_column(0.0)

        # Ensure ops schema
        self._ensure_ops_schema()

    # ---- helpers
    def _current_fill(self, le: QLineEdit) -> float:
        try:
            return max(0.0, min(100.0, float(le.text().replace(",", "."))))
        except Exception:
            return 0.0

    def _parse_heel_token(self, raw: str) -> str:
        s = raw.strip().upper().replace(" ", "")
        m = self._HEEL_TOKEN_RE.match(s)
        if m:
            val = m.group(1).replace(",", ".")
            side = m.group(2).upper()
            try:
                float(val)
            except ValueError:
                return "0"
            return f"{val}{side}"
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            if s in ("P", "S"):
                return "0" + s
            return "0"
        if v == 0:
            return "0"
        return f"{abs(v)}{'P' if v > 0 else 'S'}"

    def _current_trim_heel(self) -> Tuple[float, str]:
        try:
            trim = float(self.leTrim.text().replace(",", "."))
        except ValueError:
            msg("Trim must be a number.")
            raise
        heel = self._parse_heel_token(self.leHeel.text())
        return trim, heel

    # ---- actions
    def calc_all(self):
        try:
            trim, heel = self._current_trim_heel()
        except Exception:
            return
        for t in (self.tblFW, self.tblUrea, self.tblMisc, self.tblOther):
            for r in range(t.rowCount()):
                try:
                    t.calc_one(r, trim=trim, heel=heel)
                except Exception:
                    pass
        self._refresh_grand_label()

    def _refresh_grand_label(self):
        fw = self.tblFW.sum_observed()
        ur = self.tblUrea.sum_observed()
        mi = self.tblMisc.sum_observed()
        ot = self.tblOther.sum_observed()
        self.lblGrand.setText(
            f"FW: {fw:.0f}   UREA: {ur:.0f}   Misc: {mi:.0f}   Other: {ot:.0f}   Grand Total: {fw+ur+mi+ot:.0f} (m³)"
        )

    # -------- DB (ops.db)
    def _ensure_ops_schema(self):
        DB_OPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS misc_header (
            date_text TEXT PRIMARY KEY,
            trim REAL,
            heel TEXT,
            fill_fw REAL,
            fill_misc REAL,
            fill_urea REAL,
            fill_other REAL,
            total_fw REAL,
            total_misc REAL,
            total_urea REAL,
            total_other REAL,
            grand_total REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS misc_rows (
            date_text TEXT,
            group_name TEXT,   -- FW / UREA / MISC / OTHER
            code TEXT,
            desc TEXT,
            full100 REAL,
            at_fill_m3 REAL,
            mode TEXT,         -- So / Ull
            level_cm REAL,
            v_obs REAL,
            PRIMARY KEY (date_text, group_name, code)
        )
        """
        )
        con.commit()
        con.close()

    def _collect_state(self) -> Dict[str, Any]:
        date_text = self.deDate.date().toString("yyyy-MM-dd")
        fw = self.tblFW.sum_observed()
        ur = self.tblUrea.sum_observed()
        mi = self.tblMisc.sum_observed()
        ot = self.tblOther.sum_observed()
        return {
            "header": {
                "date_text": date_text,
                "trim": float(self.leTrim.text().replace(",", ".")),
                "heel": self._parse_heel_token(self.leHeel.text()),
                "fill_fw": self._current_fill(self.leFillFW),
                "fill_misc": self._current_fill(self.leFillMisc),
                "fill_urea": self._current_fill(self.leFillUrea),
                "fill_other": self._current_fill(self.leFillOther),
                "total_fw": fw,
                "total_misc": mi,
                "total_urea": ur,
                "total_other": ot,
                "grand_total": fw + mi + ur + ot,
            },
            "rows_fw": list(self.tblFW.iter_rows()),
            "rows_urea": list(self.tblUrea.iter_rows()),
            "rows_misc": list(self.tblMisc.iter_rows()),
            "rows_other": list(self.tblOther.iter_rows()),
        }

    def save_to_ops(self):
        state = self._collect_state()
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        h = state["header"]
        cur.execute(
            """
        INSERT INTO misc_header (date_text, trim, heel, fill_fw, fill_misc, fill_urea, fill_other,
                                 total_fw, total_misc, total_urea, total_other, grand_total)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date_text) DO UPDATE SET
            trim=excluded.trim, heel=excluded.heel,
            fill_fw=excluded.fill_fw, fill_misc=excluded.fill_misc,
            fill_urea=excluded.fill_urea, fill_other=excluded.fill_other,
            total_fw=excluded.total_fw, total_misc=excluded.total_misc,
            total_urea=excluded.total_urea, total_other=excluded.total_other,
            grand_total=excluded.grand_total
        """,
            (
                h["date_text"],
                h["trim"],
                h["heel"],
                h["fill_fw"],
                h["fill_misc"],
                h["fill_urea"],
                h["fill_other"],
                h["total_fw"],
                h["total_misc"],
                h["total_urea"],
                h["total_other"],
                h["grand_total"],
            ),
        )
        cur.execute("DELETE FROM misc_rows WHERE date_text=?", (h["date_text"],))

        for group_name, rows in (
            ("FW", state["rows_fw"]),
            ("UREA", state["rows_urea"]),
            ("MISC", state["rows_misc"]),
            ("OTHER", state["rows_other"]),
        ):
            for r in rows:
                cur.execute(
                    """
                INSERT INTO misc_rows (date_text, group_name, code, desc, full100, at_fill_m3, mode, level_cm, v_obs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        h["date_text"],
                        group_name,
                        r["code"],
                        r["desc"],
                        r["full100"],
                        r["at_fill_m3"],
                        r["mode"],
                        r["level_cm"],
                        r["v_obs"],
                    ),
                )
        con.commit()
        con.close()
        info(f"Saved to {DB_OPS_PATH}\nDate: {h['date_text']}")

    def retrieve_from_ops(self):
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        try:
            cur.execute("SELECT date_text FROM misc_header ORDER BY date_text DESC")
            dates = [r[0] for r in cur.fetchall()]
        except sqlite3.Error:
            dates = []
        if not dates:
            con.close()
            msg("No Other-Tanks entries found in ops.db.")
            return

        date_text, ok = QInputDialog.getItem(
            self, "Retrieve saved data", "Available dates:", dates, 0, False
        )
        if not ok or not date_text:
            con.close()
            return

        cur.execute(
            """SELECT trim, heel, fill_fw, fill_misc, fill_urea, fill_other
                       FROM misc_header WHERE date_text=?""",
            (date_text,),
        )
        hdr = cur.fetchone()
        if not hdr:
            con.close()
            msg("Selected date not found.")
            return
        trim, heel, fill_fw, fill_misc, fill_urea, fill_other = hdr

        cur.execute(
            """SELECT group_name, code, desc, full100, at_fill_m3, mode, level_cm, v_obs
                       FROM misc_rows WHERE date_text=?""",
            (date_text,),
        )
        rows = cur.fetchall()
        con.close()

        d = QDate.fromString(date_text, "yyyy-MM-dd")
        if d.isValid():
            self.deDate.setDate(d)
        self.leTrim.setText(f"{trim:.2f}")
        self.leHeel.setText(heel)
        self.leFillFW.setText(f"{(fill_fw or 0):.2f}")
        self.leFillMisc.setText(f"{(fill_misc or 0):.2f}")
        self.leFillUrea.setText(f"{(fill_urea or 0):.2f}")
        self.leFillOther.setText(f"{(fill_other or 0):.2f}")

        m_fw: List[Dict[str, Any]] = []
        m_mi: List[Dict[str, Any]] = []
        m_ur: List[Dict[str, Any]] = []
        m_ot: List[Dict[str, Any]] = []
        for (grp, code, desc, full100, at_fill_m3, mode, level_cm, v_obs) in rows:
            rd = {
                "code": code,
                "desc": desc,
                "full100": full100,
                "at_fill_m3": at_fill_m3,
                "mode": mode,
                "level_cm": level_cm,
                "v_obs": v_obs,
            }
            (m_fw if grp == "FW" else m_ur if grp == "UREA" else m_mi if grp == "MISC" else m_ot).append(
                rd
            )

        self.tblFW.apply_rows(m_fw)
        self.tblUrea.apply_rows(m_ur)
        self.tblMisc.apply_rows(m_mi)
        self.tblOther.apply_rows(m_ot)

        self.tblFW.update_fill_column(fill_fw or 0.0)
        self.tblUrea.update_fill_column(fill_urea or 0.0)
        self.tblMisc.update_fill_column(fill_misc or 0.0)
        self.tblOther.update_fill_column(fill_other or 0.0)
        self._refresh_grand_label()
        info(f"Loaded saved entry for {date_text} from {DB_OPS_PATH}")


# ------------ main ------------
def main():
    if not DB_CAP_PATH.exists():
        msg(f"Capacity DB not found:\n{DB_CAP_PATH}\n(Load or point to the correct DB.)")
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
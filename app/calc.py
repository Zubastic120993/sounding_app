
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QApplication, QWidget, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QDateEdit, QLineEdit, QPushButton, QHBoxLayout,
    QRadioButton, QMessageBox, QAbstractItemView
)

# --- our project imports (tested logic reused) ---
from app.ops_cli import _compute_volumes                # base + heel from capacity DB
from app.vcf54b import vcf_54b                          # <-- correct import & name

# --------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DB_CAP_PATH = ROOT / "data" / "sounding.db"

# Fixed tank lists (fill with exactly the tanks you want to see in the sheet)
HFO_TANKS = [
    ("FO1P_NO1_HFO_TK_P", "HFO Tk 1 P"),
    ("FO1S_NO1_HFO_TK_S", "HFO Tk 1 S"),
    ("FO3C_NO3_HFO_TK",   "HFO Tk 3 Central"),
    ("FOL1_HFO_SETTL_1",  "HFO Settl. Tk 1"),
    ("FOL2_HFO_SETTL_2",  "HFO Settl. Tk 2"),
    ("FOV1_HFO_SERV_1",   "HFO Serv. Tk 1"),
    ("HFO_SERV_2",        "HFO Serv. Tk 2"),
]

MGO_TANKS = [
    ("GO2C_NO2_MGO_TK",   "MGO Tk 2 Central"),
    ("GOV1_MGO_SERV_1",   "MGO Serv. Tk 1"),
    ("GOV2_MGO_SERV_2",   "MGO Serv. Tk 2"),
]

GREEN = "background-color: #e9f8ea;"  # light green for inputs

# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------

def msg(text: str, title="Info"):
    m = QMessageBox(QMessageBox.Icon.Information, title, text)
    m.exec()

def safe_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "."))
    except Exception:
        return None

# --------------------------------------------------------------------
# table widget per side
# --------------------------------------------------------------------

COLS = [
    ("tk",        "TK No"),
    ("desc",      "Description"),
    ("full100",   "100% Full (m³)"),
    ("full95",    "95% Full (m³)"),
    ("mode",      "Mode"),
    ("level",     "Level (cm)"),
    ("temp",      "Temp (°C)"),
    ("d15",       "Density@15 (kg/m³)"),
    ("vcf",       "VCF"),
    ("v15",       "Vol@15 (m³)"),
    ("mass",      "Mass (t)"),
    ("calc",      "Calc"),
]

class FuelTable(QTableWidget):
    def __init__(self, tanks: list[tuple[str, str]], parent=None):
        super().__init__(len(tanks), len(COLS), parent)
        self.tanks = tanks
        self.setHorizontalHeaderLabels([c[1] for c in COLS])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for r, (tk, desc) in enumerate(tanks):
            self._build_row(r, tk, desc)

    # widgets access helpers
    def _w(self, r: int, key: str):
        col_index = [k for k, _ in COLS].index(key)
        return self.cellWidget(r, col_index)

    def _set_item(self, r: int, key: str, text: str, editable=False, green=False, align_right=True):
        col_index = [k for k, _ in COLS].index(key)
        it = QTableWidgetItem(text)
        if align_right:
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if not editable:
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.setItem(r, col_index, it)
        if green:
            it.setBackground(Qt.GlobalColor.transparent)
            it.setForeground(Qt.GlobalColor.black)

    def _build_row(self, r: int, tk: str, desc: str):
        # tk, desc, full values (read-only now, you can wire them to meta later)
        self._set_item(r, "tk", tk)
        self._set_item(r, "desc", desc)
        self._set_item(r, "full100", "-", editable=False)
        self._set_item(r, "full95",  "-", editable=False)

        # mode: sounding/ullage
        wrap = QWidget()
        lay = QHBoxLayout(wrap); lay.setContentsMargins(0,0,0,0)
        rbS = QRadioButton("So"); rbU = QRadioButton("Ull")
        rbS.setChecked(True)
        lay.addWidget(rbS); lay.addWidget(rbU); lay.addStretch(1)
        self.setCellWidget(r, [k for k,_ in COLS].index("mode"), wrap)

        # level (cm)
        level = QLineEdit("0")
        level.setValidator(QDoubleValidator(bottom=-9999, top=9999, decimals=3))
        level.setStyleSheet(GREEN)
        self.setCellWidget(r, [k for k,_ in COLS].index("level"), level)

        # temperature
        temp = QLineEdit("25")
        temp.setValidator(QDoubleValidator(-50, 200, 2))
        temp.setStyleSheet(GREEN)
        self.setCellWidget(r, [k for k,_ in COLS].index("temp"), temp)

        # d15
        d15 = QLineEdit("953.6" if tk.startswith("FO") or tk.startswith("FOL") or tk.startswith("FOV") else "850")
        d15.setValidator(QDoubleValidator(400, 1200, 3))
        d15.setStyleSheet(GREEN)
        self.setCellWidget(r, [k for k,_ in COLS].index("d15"), d15)

        # computed fields
        self._set_item(r, "vcf", "-", editable=False)
        self._set_item(r, "v15", "-", editable=False)
        self._set_item(r, "mass", "-", editable=False)

        # calc button
        btn = QPushButton("Calc")
        btn.clicked.connect(lambda *_: self.compute_row(r))
        self.setCellWidget(r, [k for k,_ in COLS].index("calc"), btn)

    # external inputs (top controls)
    def get_trim(self) -> float:
        return self.parent().trim_value()

    def get_heel_str(self) -> Optional[str]:
        return self.parent().heel_value()

    def compute_row(self, r: int):
        tk = self.item(r, [k for k,_ in COLS].index("tk")).text()
        rbwrap = self._w(r, "mode")
        rbS = rbwrap.findChildren(QRadioButton)[0]
        mode_sounding = rbS.isChecked()

        level = safe_float(self._w(r, "level").text())
        temp  = safe_float(self._w(r, "temp").text())
        d15   = safe_float(self._w(r, "d15").text())
        trim  = self.get_trim()
        heel  = self.get_heel_str()

        # validation
        if level is None or temp is None or d15 is None:
            msg(f"{tk}: please enter numeric level/temperature/density.")
            return

        # 1) volume from capacity DB (tested routine)
        try:
            base, corr, v_obs = _compute_volumes(
                name=tk,
                trim=trim,
                sounding=level if mode_sounding else None,
                ullage=None if mode_sounding else level,
                heel=heel
            )
        except Exception as e:
            msg(f"{tk}: {e}")
            return

        # 2) VCF using corrected call SIGNATURE
        try:
            vcf = vcf_54b(density15_kg_m3=d15, temperature_c=temp)  # <-- fixed keywords
        except Exception as e:
            msg(f"{tk}: VCF error: {e}")
            return

        v15 = v_obs * vcf
        mass_t = (v15 * d15) / 1000.0  # kg/m3 * m3 -> kg; /1000 -> t

        # put results
        self.item(r, [k for k,_ in COLS].index("vcf")).setText(f"{vcf:.6f}")
        self.item(r, [k for k,_ in COLS].index("v15")).setText(f"{v15:.3f}")
        self.item(r, [k for k,_ in COLS].index("mass")).setText(f"{mass_t:.3f}")

        # update footer totals
        self.parent().update_totals()

    def compute_all(self):
        for r in range(self.rowCount()):
            self.compute_row(r)

    def total_mass_t(self) -> float:
        s = 0.0
        for r in range(self.rowCount()):
            it = self.item(r, [k for k,_ in COLS].index("mass"))
            if it:
                v = safe_float(it.text())
                if v is not None:
                    s += v
        return s

# --------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------

class FuelSheet(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fuel Sheet")
        self.resize(1280, 720)

        g = QGridLayout(self)

        # ----- top controls -----
        g.addWidget(QLabel("Date:"), 0, 0)
        self.dt = QDateEdit(QDate.currentDate()); self.dt.setCalendarPopup(True)
        g.addWidget(self.dt, 0, 1)

        g.addWidget(QLabel("Trim:"), 0, 2)
        self.edTrim = QLineEdit("0.0"); self.edTrim.setFixedWidth(80); self.edTrim.setValidator(QDoubleValidator(-20, 20, 3))
        g.addWidget(self.edTrim, 0, 3)

        g.addWidget(QLabel("Heel:"), 0, 4)
        self.edHeel = QLineEdit("0"); self.edHeel.setPlaceholderText("e.g., 0, P1, P2, S-1, S-2, 0.75S"); self.edHeel.setFixedWidth(160)
        g.addWidget(self.edHeel, 0, 5)

        # fill % fields (optional broadcast % if you later wire max vols)
        g.addWidget(QLabel("Fill % (HFO):"), 0, 6)
        self.edFillHFO = QLineEdit("0.00"); self.edFillHFO.setFixedWidth(80); self.edFillHFO.setValidator(QDoubleValidator(0, 100, 2))
        g.addWidget(self.edFillHFO, 0, 7)

        g.addWidget(QLabel("Fill % (MGO):"), 0, 8)
        self.edFillMGO = QLineEdit("0.00"); self.edFillMGO.setFixedWidth(80); self.edFillMGO.setValidator(QDoubleValidator(0, 100, 2))
        g.addWidget(self.edFillMGO, 0, 9)

        # ----- tables -----
        self.tblHFO = FuelTable(HFO_TANKS, self)
        self.tblMGO = FuelTable(MGO_TANKS, self)

        g.addWidget(QLabel("HFO Tanks"), 1, 0, 1, 10)
        g.addWidget(self.tblHFO,        2, 0, 1, 10)
        g.addWidget(QLabel("MGO Tanks"),3, 0, 1, 10)
        g.addWidget(self.tblMGO,        4, 0, 1, 10)

        # ----- footer -----
        self.btnCalcAll = QPushButton("Calculate")
        self.btnCalcAll.clicked.connect(self.calc_all)
        g.addWidget(self.btnCalcAll, 5, 0, 1, 1)

        self.lblTotHFO = QLabel("Total HFO (t): 0.000")
        self.lblTotMGO = QLabel("Total MGO (t): 0.000")
        self.lblGrand  = QLabel("Grand Total (t): 0.000")
        g.addWidget(self.lblTotHFO, 5, 1, 1, 3)
        g.addWidget(self.lblTotMGO, 5, 4, 1, 3)
        g.addWidget(self.lblGrand,  5, 7, 1, 3)

        # DB path note
        self.dbNote = QLabel(f"DB: {DB_CAP_PATH}")
        self.dbNote.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.dbNote.setStyleSheet("color: #888;")
        g.addWidget(self.dbNote, 6, 0, 1, 10)

    # exposed for child tables
    def trim_value(self) -> float:
        v = safe_float(self.edTrim.text())
        if v is None:
            raise ValueError("Trim must be a number (e.g., -3, 0, 1.5).")
        return v

    def heel_value(self) -> Optional[str]:
        s = self.edHeel.text().strip()
        return s if s else None

    # totals
    def update_totals(self):
        tot_hfo = self.tblHFO.total_mass_t()
        tot_mgo = self.tblMGO.total_mass_t()
        self.lblTotHFO.setText(f"Total HFO (t): {tot_hfo:.3f}")
        self.lblTotMGO.setText(f"Total MGO (t): {tot_mgo:.3f}")
        self.lblGrand.setText(f"Grand Total (t): {tot_hfo + tot_mgo:.3f}")

    def calc_all(self):
        errs = []
        try:
            _ = self.trim_value()
        except Exception as e:
            errs.append(str(e))
        if errs:
            msg("\n".join(errs), "Input error")
            return

        self.tblHFO.compute_all()
        self.tblMGO.compute_all()
        self.update_totals()

# --------------------------------------------------------------------
# main
# --------------------------------------------------------------------

def main():
    # sanity for DB existence (capacity DB)
    if not DB_CAP_PATH.exists():
        msg(f"Capacity DB not found:\n{DB_CAP_PATH}", "Error")
        return
    app = QApplication([])
    w = FuelSheet()
    w.show()
    app.exec()

if __name__ == "__main__":
    main()
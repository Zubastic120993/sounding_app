
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import sqlite3
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QDoubleValidator, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QMessageBox, QHeaderView, QAbstractItemView, QDateEdit,
    QInputDialog
)

# ----- import path so "from app..." works when run as script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ops_cli import _compute_volumes  # base+heel from capacity DB
from app.vcf54b import vcf_54b

DB_CAP_PATH = ROOT / "data" / "sounding.db"
DB_OPS_PATH = ROOT / "data" / "ops.db"

# ---------------- fixed tank lists ----------------
HFO_ROWS = [
    ("FO1P_NO1_HFO_TK_P", "HFO Tk 1 P", 254.2, 241.5),
    ("FO1S_NO1_HFO_TK_S", "HFO Tk 1 S", 254.2, 241.5),
    ("FO3C_NO3_HFO_TK",   "HFO Tk 3 Central", 234.5, 222.8),
    ("FOL1_HFO_SETTL_1",  "HFO Settl. Tk 1", 39.0,  37.0),
    ("FOL2_HFO_SETTL_2",  "HFO Settl. Tk 2", 42.9,  40.8),
    ("FOV1_HFO_SERV_1",   "HFO Serv. Tk 1",  37.7,  35.8),
    ("FOV2_HFO_SERV_2",   "HFO Serv. Tk 2",  45.3,  43.0),
]

MGO_ROWS = [
    ("GO2C_NO2_MGO_TK", "MGO Tk 2 Central", 234.50, 222.78),
    ("GOV1_MGO_SERV_1", "MGO Serv. Tk 1",    74.50,  70.78),
    ("GOV2_MGO_SERV_2", "MGO Serv. Tk 2",    28.20,  26.79),
]

GREEN = "background-color:#eaffea;"

DEFAULT_ULL_CODES = {
    "FO1P_NO1_HFO_TK_P",
    "FO1S_NO1_HFO_TK_S",
    "FO3C_NO3_HFO_TK",
}

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
    return full_code.split("_", 1)[0] if "_" in full_code else full_code

# ---------- NEW: read min/max sounding/ullage per tank from capacity DB ----------
CANDIDATE_COLS = ("sounding", "ullage", "sound", "ull", "level")

def tank_level_bounds(code: str) -> Tuple[int, int, bool]:
    """
    Return (min_cm, max_cm, from_db).
    Tries to read MIN/MAX over any of CANDIDATE_COLS in table `readings` for the given name.
    If nothing works, falls back to (0, 4000, False).
    """
    min_cm: Optional[float] = None
    max_cm: Optional[float] = None
    try:
        con = sqlite3.connect(DB_CAP_PATH)
        cur = con.cursor()
        # ensure table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='readings'")
        if cur.fetchone():
            for col in CANDIDATE_COLS:
                try:
                    cur.execute(f"SELECT MIN({col}), MAX({col}) FROM readings WHERE name=?", (code,))
                    row = cur.fetchone()
                    if row and row[0] is not None and row[1] is not None:
                        cmin, cmax = float(row[0]), float(row[1])
                        # combine across cols: we want the widest safe range
                        min_cm = cmin if min_cm is None else min(min_cm, cmin)
                        max_cm = cmax if max_cm is None else max(max_cm, cmax)
                except sqlite3.Error:
                    # column may not exist; try next
                    continue
        con.close()
    except Exception:
        pass

    if min_cm is None or max_cm is None or min_cm >= max_cm:
        return (0, 4000, False)   # safe wide fallback
    return (max(0, int(min_cm)), int(max_cm), True)

class FuelTable(QTableWidget):
    COLS = [
        "Tk", "Description", "100% Full (m³)", "At Fill % (m³)",
        "Mode", "Level (cm)", "Temp (°C)", "Density@15 (kg/m³)",
        "Observed Vol (m³)", "VCF", "Vol@15 (m³)", "Mass (t)", "Calc"
    ]

    def __init__(self, rows_spec, is_hfo: bool, parent=None):
        super().__init__(parent)
        self.is_hfo = is_hfo
        self.rows_spec = rows_spec
        self.setColumnCount(len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.setRowCount(len(rows_spec))
        self._row_widgets: List[Dict[str, Any]] = []

        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setBold(True)
        self.horizontalHeader().setFont(header_font)
        self.horizontalHeader().setFixedHeight(40)

        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setAlternatingRowColors(True)

        self.setColumnWidth(0, 55)

        for r, spec in enumerate(rows_spec):
            self._build_row(r, spec)

    def _build_row(self, r: int, spec):
        widgets: Dict[str, Any] = {}
        code, desc, full100, full95 = spec

        it_code = QTableWidgetItem(short_code(code)); it_code.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 0, it_code)

        it_desc = QTableWidgetItem(desc); it_desc.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 1, it_desc)

        it_100 = QTableWidgetItem(f"{float(full100):.1f}"); it_100.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 2, it_100)

        it_fill = QTableWidgetItem("-"); it_fill.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 3, it_fill)

        w_mode = QWidget()
        hb = QHBoxLayout(w_mode); hb.setContentsMargins(0,0,0,0)
        rb_s = QRadioButton("So")
        rb_u = QRadioButton("Ull")
        default_is_ull = (code in DEFAULT_ULL_CODES) and self.is_hfo
        rb_s.setChecked(default_is_ull) if False else rb_s.setChecked(not default_is_ull)  # keep line length sane
        rb_u.setChecked(default_is_ull)
        hb.addWidget(rb_s); hb.addWidget(rb_u); hb.addStretch(1)
        self.setCellWidget(r, 4, w_mode)
        widgets["rb_s"], widgets["rb_u"] = rb_s, rb_u

        # --------- NEW: per-tank limits from DB (fallback 0–4000) ----------
        min_cm, max_cm, from_db = tank_level_bounds(code)

        sp_level = QSpinBox()
        sp_level.setRange(min_cm, max_cm)
        sp_level.setValue(min_cm)
        sp_level.setStyleSheet(GREEN)
        tip_src = "capacity DB" if from_db else "fallback"
        sp_level.setToolTip(f"Allowed range: {min_cm}–{max_cm} cm ({tip_src}).")
        self.setCellWidget(r, 5, sp_level); widgets["level"] = sp_level

        sp_temp = QSpinBox(); sp_temp.setRange(-20, 120); sp_temp.setValue(25)
        sp_temp.setStyleSheet(GREEN)
        self.setCellWidget(r, 6, sp_temp); widgets["temp"] = sp_temp

        default_dens = 953.6 if self.is_hfo else 850.0
        de = QLineEdit(f"{default_dens}")
        de.setValidator(QDoubleValidator(500.0, 1200.0, 3))
        de.setStyleSheet(GREEN)
        self.setCellWidget(r, 7, de); widgets["dens15"] = de

        it_vobs = QTableWidgetItem("-"); it_vobs.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 8, it_vobs); widgets["vobs"] = it_vobs

        it_vcf = QTableWidgetItem("-"); it_vcf.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 9, it_vcf); widgets["vcf"] = it_vcf

        it_v15 = QTableWidgetItem("-"); it_v15.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 10, it_v15); widgets["v15"] = it_v15

        it_mass = QTableWidgetItem("-"); it_mass.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 11, it_mass); widgets["mass"] = it_mass

        btn = QPushButton("Calc")
        btn.clicked.connect(lambda _=False, row=r: self.calc_one(row))
        self.setCellWidget(r, 12, btn)

        widgets["code_full"] = code
        widgets["full100"] = float(full100)
        widgets["full95"] = float(full95)
        widgets["fill_col_item"] = it_fill
        self._row_widgets.append(widgets)

    # ------------ calculation helpers ------------
    def get_row_inputs(self, row: int):
        w = self._row_widgets[row]
        code = w["code_full"]
        is_sounding = w["rb_s"].isChecked()
        level_cm = float(w["level"].value())
        temp_c = float(w["temp"].value())
        try:
            dens15 = float(w["dens15"].text().replace(",", "."))
        except Exception:
            dens15 = 0.0
        return code, is_sounding, level_cm, temp_c, dens15

    def set_row_outputs(self, row: int,
                        v_obs: Optional[float],
                        vcf: Optional[float],
                        v15: Optional[float],
                        mass_t: Optional[float]):
        w = self._row_widgets[row]
        w["vobs"].setText("-" if v_obs is None else f"{v_obs:.3f}")
        w["vcf"].setText("-" if vcf is None else f"{vcf:.6f}")
        w["v15"].setText("-" if v15 is None else f"{v15:.3f}")
        w["mass"].setText("-" if mass_t is None else f"{mass_t:.2f}")

    def calc_one(self, row: int, trim: float = 0.0, heel: Optional[str] = None):
        code, is_sounding, level_cm, temp_c, dens15 = self.get_row_inputs(row)

        if level_cm == 0 and not is_sounding:
            self.set_row_outputs(row, 0.0, 1.0, 0.0, 0.0)
            return

        try:
            _, _, v_obs = _compute_volumes(
                name=code,
                trim=trim,
                sounding=level_cm if is_sounding else None,
                ullage=level_cm if not is_sounding else None,
                heel=heel
            )
        except Exception:
            self.set_row_outputs(row, None, None, None, None)
            raise

        vcf = vcf_54b(dens15, temp_c)
        v_obs = max(0.0, v_obs)
        v15 = max(0.0, v_obs * vcf)
        mass_t = max(0.0, (v15 * dens15) / 1000.0)
        self.set_row_outputs(row, v_obs, vcf, v15, mass_t)

    def sum_mass_t(self) -> float:
        total = 0.0
        for r in range(self.rowCount()):
            txt = self._row_widgets[r]["mass"].text()
            if txt not in ("-", ""):
                try:
                    total += float(txt)
                except ValueError:
                    pass
        return total

    def update_fill_column(self, fill_pct: float):
        scale = max(0.0, min(100.0, fill_pct)) / 100.0
        for r in range(self.rowCount()):
            full100 = self._row_widgets[r]["full100"]
            val = full100 * scale
            self._row_widgets[r]["fill_col_item"].setText(f"{val:.1f}")
        self.setHorizontalHeaderItem(3, QTableWidgetItem(f"At {fill_pct:.0f}% (m³)"))

    def iter_rows(self):
        for r, w in enumerate(self._row_widgets):
            mode = "So" if w["rb_s"].isChecked() else "Ull"
            level = float(w["level"].value())
            temp = float(w["temp"].value())
            try:
                dens = float(w["dens15"].text().replace(",", "."))
            except Exception:
                dens = 0.0
            vobs_txt = w["vobs"].text()
            vcf_txt = w["vcf"].text()
            v15_txt = w["v15"].text()
            mass_txt = w["mass"].text()
            at_fill_txt = w["fill_col_item"].text()
            yield {
                "code": w["code_full"],
                "desc": self.item(r, 1).text() if self.item(r, 1) else "",
                "full100": w["full100"],
                "full95": w["full95"],
                "at_fill_m3": float(at_fill_txt) if at_fill_txt not in ("-", "") else None,
                "mode": mode,
                "level_cm": level,
                "temp_c": temp,
                "dens15": dens,
                "v_obs": float(vobs_txt) if vobs_txt not in ("-", "") else None,
                "vcf": float(vcf_txt) if vcf_txt not in ("-", "") else None,
                "v15": float(v15_txt) if v15_txt not in ("-", "") else None,
                "mass_t": float(mass_txt) if mass_txt not in ("-", "") else None,
            }

    def apply_rows(self, rows: List[Dict[str, Any]]):
        idx_by_code = {w["code_full"]: i for i, w in enumerate(self._row_widgets)}
        for row in rows:
            code = row["code"]
            if code not in idx_by_code:
                continue
            r = idx_by_code[code]
            w = self._row_widgets[r]
            if row.get("mode", "So") == "So":
                w["rb_s"].setChecked(True)
            else:
                w["rb_u"].setChecked(True)
            w["level"].setValue(int(row.get("level_cm") or 0))
            w["temp"].setValue(int(row.get("temp_c") or 25))
            w["dens15"].setText("" if row.get("dens15") is None else f"{row['dens15']}")
            vobs = row.get("v_obs")
            w["vobs"].setText("-" if vobs is None else f"{float(vobs):.3f}")
            w["vcf"].setText("-" if row.get("vcf") is None else f"{row['vcf']:.6f}")
            w["v15"].setText("-" if row.get("v15") is None else f"{row['v15']:.3f}")
            w["mass"].setText("-" if row.get("mass_t") is None else f"{row['mass_t']:.2f}")
            if row.get("at_fill_m3") is not None:
                w["fill_col_item"].setText(f"{row['at_fill_m3']:.1f}")

class MainWindow(QWidget):
    _HEEL_TOKEN_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([PS])\s*$", re.I)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fuel Input – Sheet")
        self.resize(1400, 800)

        # Top controls
        top = QHBoxLayout()
        top.addWidget(QLabel("Date:"))
        self.deDate = QDateEdit(QDate.currentDate())
        self.deDate.setCalendarPopup(True)
        self.deDate.setDisplayFormat("dd.MM.yyyy")
        self.deDate.setFixedWidth(120)
        top.addWidget(self.deDate)

        top.addSpacing(12)
        top.addWidget(QLabel("Trim:"))
        self.leTrim = QLineEdit("0.00")
        self.leTrim.setFixedWidth(80)
        self.leTrim.setValidator(QDoubleValidator(-10.0, 10.0, 2))
        self.leTrim.setToolTip("Trim, meters (e.g., -2.5, 0, 1.0)")
        top.addWidget(self.leTrim)

        top.addSpacing(12)
        top.addWidget(QLabel("Heel:"))
        self.leHeel = QLineEdit("0")
        self.leHeel.setFixedWidth(100)
        self.leHeel.setValidator(QDoubleValidator(-10.0, 10.0, 2))
        self.leHeel.setPlaceholderText("e.g., 1 (Port), -0.5 (Stbd), 0,5P, 0,5S")
        self.leHeel.setToolTip(
            "Heel rule:\n• Port → enter >0 or token like 0,5P\n• Starboard → enter <0 or token like 0,5S\nCommas or dots allowed."
        )
        top.addWidget(self.leHeel)

        top.addSpacing(20)
        top.addWidget(QLabel("Fill % (HFO):"))
        self.leFillHFO = QLineEdit("0.00")
        self.leFillHFO.setFixedWidth(80)
        self.leFillHFO.setValidator(QDoubleValidator(0.0, 100.0, 2))
        top.addWidget(self.leFillHFO)

        top.addSpacing(12)
        top.addWidget(QLabel("Fill % (MGO):"))
        self.leFillMGO = QLineEdit("0.00")
        self.leFillMGO.setFixedWidth(80)
        self.leFillMGO.setValidator(QDoubleValidator(0.0, 100.0, 2))
        top.addWidget(self.leFillMGO)

        top.addStretch(1)

        foot_bold = QFont(); foot_bold.setBold(True)

        gH = QGroupBox("HFO Tanks")
        fH = gH.font(); fH.setPointSize(14); gH.setFont(fH)
        vH = QVBoxLayout(gH)
        self.tblHFO = FuelTable(HFO_ROWS, is_hfo=True)
        vH.addWidget(self.tblHFO)

        hfoFooter = QHBoxLayout()
        self.lblTotalHFO = QLabel("TOTAL 0.00 (t)")
        self.lblTotalHFO.setAlignment(Qt.AlignRight)
        self.lblTotalHFO.setFont(foot_bold)
        hfoFooter.addWidget(self.lblTotalHFO)
        hfoFooter.addStretch(1)
        lblLogH = QLabel("Log Book (t):"); lblLogH.setFont(foot_bold); hfoFooter.addWidget(lblLogH)
        self.leLogHFO = QLineEdit("0.00"); self.leLogHFO.setFixedWidth(90)
        self.leLogHFO.setValidator(QDoubleValidator(0.0, 1e6, 2))
        self.leLogHFO.setFont(foot_bold); hfoFooter.addWidget(self.leLogHFO)
        hfoFooter.addSpacing(12); lblDiffH = QLabel("Diff:"); lblDiffH.setFont(foot_bold); hfoFooter.addWidget(lblDiffH)
        self.lblDiffHFO = QLabel("0.00"); self.lblDiffHFO.setMinimumWidth(70); self.lblDiffHFO.setFont(foot_bold)
        hfoFooter.addWidget(self.lblDiffHFO)
        vH.addLayout(hfoFooter)

        gM = QGroupBox("MGO Tanks")
        fM = gM.font(); fM.setPointSize(14); gM.setFont(fM)
        vM = QVBoxLayout(gM)
        self.tblMGO = FuelTable(MGO_ROWS, is_hfo=False)
        vM.addWidget(self.tblMGO)

        mgoFooter = QHBoxLayout()
        self.lblTotalMGO = QLabel("TOTAL 0.00 (t)")
        self.lblTotalMGO.setAlignment(Qt.AlignRight)
        self.lblTotalMGO.setFont(foot_bold)
        mgoFooter.addWidget(self.lblTotalMGO)
        mgoFooter.addStretch(1)
        lblLogM = QLabel("Log Book (t):"); lblLogM.setFont(foot_bold); mgoFooter.addWidget(lblLogM)
        self.leLogMGO = QLineEdit("0.00"); self.leLogMGO.setFixedWidth(90)
        self.leLogMGO.setValidator(QDoubleValidator(0.0, 1e6, 2))
        self.leLogMGO.setFont(foot_bold); mgoFooter.addWidget(self.leLogMGO)
        mgoFooter.addSpacing(12); lblDiffM = QLabel("Diff:"); lblDiffM.setFont(foot_bold); mgoFooter.addWidget(lblDiffM)
        self.lblDiffMGO = QLabel("0.00"); self.lblDiffMGO.setMinimumWidth(70); self.lblDiffMGO.setFont(foot_bold)
        mgoFooter.addWidget(self.lblDiffMGO)
        vM.addLayout(mgoFooter)

        bottom = QHBoxLayout()
        self.btnSave = QPushButton("Save")
        self.btnSave.clicked.connect(self.save_to_ops)
        bottom.addWidget(self.btnSave)

        self.btnLoad = QPushButton("Retrieve")
        self.btnLoad.clicked.connect(self.retrieve_from_ops)
        bottom.addWidget(self.btnLoad)

        self.btnCalcAll = QPushButton("Calculate")
        self.btnCalcAll.clicked.connect(self.calc_all)
        bottom.addWidget(self.btnCalcAll)

        bottom.addStretch(1)
        self.lblGrand = QLabel("Total HFO (t): 0.00    Total MGO (t): 0.00    Grand Total (t): 0.00")
        bottom.addWidget(self.lblGrand)
        bottom.addStretch(1)
        self.lblDB = QLabel(f"DB: {DB_CAP_PATH}")
        bottom.addWidget(self.lblDB)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(gH)
        layout.addWidget(gM)
        layout.addLayout(bottom)

        self.install_row_handlers(self.tblHFO, is_hfo=True)
        self.install_row_handlers(self.tblMGO, is_hfo=False)

        self.leFillHFO.editingFinished.connect(self._on_fill_hfo_changed)
        self.leFillMGO.editingFinished.connect(self._on_fill_mgo_changed)

        self.leLogHFO.editingFinished.connect(self.update_totals)
        self.leLogMGO.editingFinished.connect(self.update_totals)

        self.tblHFO.update_fill_column(0.0)
        self.tblMGO.update_fill_column(0.0)

        self._ensure_ops_schema()

    def _parse_heel(self, raw: str) -> str:
        s = raw.strip().upper().replace(" ", "")
        if not s:
            return "0"
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
            val = float(s.replace(",", "."))
        except ValueError:
            if s in ("P", "S"):
                return "0" + s
            return "0"
        if val == 0:
            return "0"
        side = "P" if val > 0 else "S"
        return f"{abs(val)}{side}"

    def _current_trim_heel(self) -> Tuple[float, str]:
        try:
            trim = float(self.leTrim.text().replace(",", "."))
        except ValueError:
            msg("Trim must be a number (e.g., -3, 0, 1.5).")
            raise
        heel_token = self._parse_heel(self.leHeel.text())
        return trim, heel_token

    def _current_fill(self, is_hfo: bool) -> float:
        le = self.leFillHFO if is_hfo else self.leFillMGO
        try:
            v = float(le.text().replace(",", "."))
        except ValueError:
            v = 0.0
        return max(0.0, min(100.0, v))

    def install_row_handlers(self, table: FuelTable, is_hfo: bool):
        for r in range(table.rowCount()):
            btn = table.cellWidget(r, 12)
            try:
                btn.clicked.disconnect()
            except Exception:
                pass
            btn.clicked.connect(lambda _=False, row=r, t=table, hf=is_hfo: self.calc_one_row(t, row, hf))

    def _on_fill_hfo_changed(self):
        pct = self._current_fill(True)
        self.tblHFO.update_fill_column(pct)

    def _on_fill_mgo_changed(self):
        pct = self._current_fill(False)
        self.tblMGO.update_fill_column(pct)

    def calc_one_row(self, table: FuelTable, row: int, is_hfo: bool):
        try:
            trim, heel = self._current_trim_heel()
        except Exception:
            return
        try:
            table.calc_one(row, trim=trim, heel=heel)
        except Exception:
            code = table._row_widgets[row]["code_full"]
            msg(f"{code}: No base volume found (check tank name, trim/heel, and sounding/ullage range).")
            return
        self.update_totals()

    def calc_all(self):
        try:
            trim, heel = self._current_trim_heel()
        except Exception:
            return
        for r in range(self.tblHFO.rowCount()):
            try:
                self.tblHFO.calc_one(r, trim=trim, heel=heel)
            except Exception:
                pass
        for r in range(self.tblMGO.rowCount()):
            try:
                self.tblMGO.calc_one(r, trim=trim, heel=heel)
            except Exception:
                pass
        self.update_totals()

    def _parse_float(self, s: str) -> float:
        try:
            return float(s.replace(",", "."))
        except Exception:
            return 0.0

    def _set_diff_style(self, label: QLabel, diff: float):
        if diff > 0:
            label.setStyleSheet("color:#0a7a0a; font-weight:600;")
        elif diff < 0:
            label.setStyleSheet("color:#c21807; font-weight:600;")
        else:
            label.setStyleSheet("color:inherit; font-weight:600;")

    def update_totals(self):
        hfo = self.tblHFO.sum_mass_t()
        mgo = self.tblMGO.sum_mass_t()
        self.lblTotalHFO.setText(f"TOTAL {hfo:.2f} (t)")
        self.lblTotalMGO.setText(f"TOTAL {mgo:.2f} (t)")
        log_hfo = self._parse_float(self.leLogHFO.text())
        log_mgo = self._parse_float(self.leLogMGO.text())
        diff_hfo = hfo - log_hfo
        diff_mgo = mgo - log_mgo
        self.lblDiffHFO.setText(f"{diff_hfo:.2f}")
        self.lblDiffMGO.setText(f"{diff_mgo:.2f}")
        self._set_diff_style(self.lblDiffHFO, diff_hfo)
        self._set_diff_style(self.lblDiffMGO, diff_mgo)
        self.lblGrand.setText(
            f"Total HFO (t): {hfo:.2f}    Total MGO (t): {mgo:.2f}    Grand Total (t): {hfo+mgo:.2f}"
        )

    def _ensure_ops_schema(self):
        DB_OPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ops_header (
                date_text   TEXT PRIMARY KEY,
                trim        REAL,
                heel        TEXT,
                fill_hfo    REAL,
                fill_mgo    REAL,
                log_hfo     REAL,
                log_mgo     REAL,
                total_hfo   REAL,
                total_mgo   REAL,
                grand_total REAL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ops_rows (
                date_text TEXT,
                fuel      TEXT,
                code      TEXT,
                desc      TEXT,
                full100   REAL,
                full95    REAL,
                at_fill_m3 REAL,
                mode      TEXT,
                level_cm  REAL,
                temp_c    REAL,
                dens15    REAL,
                vcf       REAL,
                v15       REAL,
                mass_t    REAL,
                PRIMARY KEY (date_text, fuel, code)
            )
        """)
        con.commit()
        con.close()

    def _collect_state(self) -> Dict[str, Any]:
        date_text = self.deDate.date().toString("yyyy-MM-dd")
        trim = self._parse_float(self.leTrim.text())
        heel = self._parse_heel(self.leHeel.text())
        fill_hfo = self._parse_float(self.leFillHFO.text())
        fill_mgo = self._parse_float(self.leFillMGO.text())
        hfo = self.tblHFO.sum_mass_t()
        mgo = self.tblMGO.sum_mass_t()
        state = {
            "header": {
                "date_text": date_text,
                "trim": trim,
                "heel": heel,
                "fill_hfo": fill_hfo,
                "fill_mgo": fill_mgo,
                "log_hfo": self._parse_float(self.leLogHFO.text()),
                "log_mgo": self._parse_float(self.leLogMGO.text()),
                "total_hfo": hfo,
                "total_mgo": mgo,
                "grand_total": hfo + mgo,
            },
            "rows_hfo": list(self.tblHFO.iter_rows()),
            "rows_mgo": list(self.tblMGO.iter_rows()),
        }
        return state

    def save_to_ops(self):
        state = self._collect_state()
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        h = state["header"]
        cur.execute("""
            INSERT INTO ops_header (date_text, trim, heel, fill_hfo, fill_mgo, log_hfo, log_mgo, total_hfo, total_mgo, grand_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_text) DO UPDATE SET
                trim=excluded.trim, heel=excluded.heel,
                fill_hfo=excluded.fill_hfo, fill_mgo=excluded.fill_mgo,
                log_hfo=excluded.log_hfo, log_mgo=excluded.log_mgo,
                total_hfo=excluded.total_hfo, total_mgo=excluded.total_mgo,
                grand_total=excluded.grand_total
        """, (h["date_text"], h["trim"], h["heel"], h["fill_hfo"], h["fill_mgo"],
              h["log_hfo"], h["log_mgo"], h["total_hfo"], h["total_mgo"], h["grand_total"]))
        cur.execute("DELETE FROM ops_rows WHERE date_text=?", (h["date_text"],))
        for fuel, rows in (("HFO", state["rows_hfo"]), ("MGO", state["rows_mgo"])):
            for r in rows:
                cur.execute("""
                    INSERT INTO ops_rows (date_text, fuel, code, desc, full100, full95, at_fill_m3,
                                          mode, level_cm, temp_c, dens15, vcf, v15, mass_t)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (h["date_text"], fuel, r["code"], r["desc"], r["full100"], r["full95"], r["at_fill_m3"],
                      r["mode"], r["level_cm"], r["temp_c"], r["dens15"], r["vcf"], r["v15"], r["mass_t"]))
        con.commit()
        con.close()
        info(f"Saved to {DB_OPS_PATH}\nDate: {h['date_text']}")

    def retrieve_from_ops(self):
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()
        try:
            cur.execute("SELECT date_text FROM ops_header ORDER BY date_text DESC")
            dates = [row[0] for row in cur.fetchall()]
        except sqlite3.Error:
            dates = []
        if not dates:
            con.close()
            msg("No saved entries found in ops.db.")
            return
        date_text, ok = QInputDialog.getItem(self, "Retrieve saved data", "Available dates:", dates, 0, False)
        if not ok or not date_text:
            con.close()
            return
        cur.execute("SELECT trim, heel, fill_hfo, fill_mgo, log_hfo, log_mgo FROM ops_header WHERE date_text=?", (date_text,))
        row = cur.fetchone()
        if not row:
            con.close()
            msg("Selected date not found.")
            return
        trim, heel, fill_hfo, fill_mgo, log_hfo, log_mgo = row
        cur.execute("SELECT fuel, code, desc, full100, full95, at_fill_m3, mode, level_cm, temp_c, dens15, vcf, v15, mass_t FROM ops_rows WHERE date_text=?", (date_text,))
        rows = cur.fetchall()
        con.close()

        d = QDate.fromString(date_text, "yyyy-MM-dd")
        if d.isValid():
            self.deDate.setDate(d)
        self.leTrim.setText(f"{trim:.2f}")
        self.leHeel.setText(heel)
        self.leFillHFO.setText(f"{fill_hfo:.2f}")
        self.leFillMGO.setText(f"{fill_mgo:.2f}")
        self.leLogHFO.setText(f"{(log_hfo or 0):.2f}")
        self.leLogMGO.setText(f"{(log_mgo or 0):.2f}")

        rows_hfo: List[Dict[str, Any]] = []
        rows_mgo: List[Dict[str, Any]] = []
        for (fuel, code, desc, full100, full95, at_fill_m3, mode, level_cm, temp_c, dens15, vcf, v15, mass_t) in rows:
            rd = {
                "code": code, "desc": desc, "full100": full100, "full95": full95,
                "at_fill_m3": at_fill_m3, "mode": mode, "level_cm": level_cm,
                "temp_c": temp_c, "dens15": dens15, "vcf": vcf, "v15": v15, "mass_t": mass_t,
            }
            (rows_hfo if fuel == "HFO" else rows_mgo).append(rd)

        self.tblHFO.apply_rows(rows_hfo)
        self.tblMGO.apply_rows(rows_mgo)
        self._on_fill_hfo_changed()
        self._on_fill_mgo_changed()
        self.update_totals()
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


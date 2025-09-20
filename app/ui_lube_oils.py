
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import sqlite3
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QDoubleValidator, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QMessageBox, QHeaderView, QAbstractItemView, QDateEdit,
    QDialog, QListWidget, QListWidgetItem,
    QInputDialog
)

# ---------------- paths / imports ----------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ops_cli import _compute_volumes  # returns (base, heel_corr, v_obs)
from app.vcf54b import vcf_54b            # VCF(dens15, T)

DB_CAP_PATH = ROOT / "data" / "sounding.db"
DB_OPS_PATH = ROOT / "data" / "ops.db"

# UI colors
INPUT_COLOR = "background-color:#fff6b0;"  # soft yellow for inputs

# ---------------- Tank groups ----------------
ME_CIRC_ROWS = [
    ("ME_LO_SETTL",     "ME L.O. Settling Tk"),
    ("ME_LO_STOR",      "ME L.O. Storage Tk"),
    ("ME LO Sump Tank", "ME L.O. Sump Tk"),   # shown but EXCLUDED from totals
]
ME_CYL_ROWS = [
    ("CLR1_ME_CYL_STORE_1",       "ME Cyl Oil Store 1"),
    ("CLR2_ME_CYL_STORE_2",       "ME Cyl Oil Store 2"),
    ("CLV1_Cyl_Oil_Service_tk_1", "ME Cyl Oil Service 1"),
    ("CLV2_Cyl_Oil_Service_tk_2", "ME Cyl Oil Service 2"),
]
AE_CIRC_ROWS = [
    ("GO_LO_ST_1", "GE L.O. Store 1"),
    ("GE_LO_ST_2", "GE L.O. Store 2"),
]

# Default Ullage behavior
DEFAULT_ULL_CODES: set[str] = set()

# Exclude these tanks from totals (still visible)
EXCLUDE_FROM_TOTALS: set[str] = {"ME LO Sump Tank"}

# ------------- helpers -------------
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

def _lookup_full_capacity_m3_any(name: str) -> Optional[float]:
    """
    Robust capacity finder:
      * look for a table with a 'name'-like column AND any column whose name CONTAINS 'vol'
      * return MAX(volume) for that tank name (assumed m3)
    """
    if not DB_CAP_PATH.exists():
        return None
    try:
        con = sqlite3.connect(DB_CAP_PATH)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')]
            # find a name-like column
            name_col = next((c for c in cols if ("name" in c.lower() or "tank" in c.lower())), None)
            if not name_col:
                continue
            # any volume-like column containing 'vol'
            for vc in [c for c in cols if "vol" in c.lower()]:
                try:
                    cur.execute(f'SELECT MAX("{vc}") FROM "{t}" WHERE "{name_col}"=?', (name,))
                    r = cur.fetchone()
                    if r and r[0] is not None:
                        con.close()
                        return float(r[0])
                except sqlite3.Error:
                    continue
        con.close()
    except Exception:
        pass
    return None

def _parse_float(text: str) -> float:
    try:
        return float(text.replace(",", ".").replace(" ", ""))
    except Exception:
        return 0.0

def _parse_int(text: str) -> int:
    try:
        return int(float(text.replace(",", ".").replace(" ", "")))
    except Exception:
        return 0

# ------------- Table widget -------------
class LOTable(QTableWidget):
    COLS = [
        "Tk", "Description", "100% Full (L)", "At Fill % (L)",
        "Mode", "Level (cm)", "Temp (°C)",
        "Observed Vol (L)",   # from capacity curve (trim/heel corrected)
        "VCF", "Vol@15 (L)", "Calc"
    ]

    def __init__(self, rows_spec: List[tuple], dens_provider: Callable[[], float], parent=None):
        """
        dens_provider: function returning current group Density@15 (kg/m³)
        """
        super().__init__(parent)
        self.rows_spec = rows_spec
        self._dens_provider = dens_provider
        self.setColumnCount(len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.setRowCount(len(rows_spec))
        self._row_widgets: List[Dict[str, Any]] = []

        # Ensure NOTHING inside the group is bold by default
        normal_font = QFont(self.font())
        normal_font.setBold(False)
        self.setFont(normal_font)

        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setBold(False)   # table headers NOT bold
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
        code, desc = spec

        # tk / desc
        it_code = QTableWidgetItem(short_code(code)); it_code.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 0, it_code)
        it_desc = QTableWidgetItem(desc); it_desc.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 1, it_desc)

        # 100% full (lookup from DB; m3 -> L)
        full100_m3 = _lookup_full_capacity_m3_any(code)
        full100_l = None if full100_m3 is None else int(round(full100_m3 * 1000))
        it_100 = QTableWidgetItem("-" if full100_l is None else f"{full100_l:,}".replace(",", " "))
        it_100.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 2, it_100)

        # At Fill % (display-only)
        it_fill = QTableWidgetItem("-"); it_fill.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 3, it_fill)

        # mode
        w_mode = QWidget(); hb = QHBoxLayout(w_mode); hb.setContentsMargins(0,0,0,0)
        rb_s = QRadioButton("So"); rb_u = QRadioButton("Ull")
        default_is_ull = (code in DEFAULT_ULL_CODES)
        rb_s.setChecked(not default_is_ull); rb_u.setChecked(default_is_ull)
        hb.addWidget(rb_s); hb.addWidget(rb_u); hb.addStretch(1)
        self.setCellWidget(r, 4, w_mode)
        widgets["rb_s"], widgets["rb_u"] = rb_s, rb_u

        # level / temp
        sp_level = QSpinBox(); sp_level.setRange(0, 2000); sp_level.setValue(0); sp_level.setStyleSheet(INPUT_COLOR)
        self.setCellWidget(r, 5, sp_level); widgets["level"] = sp_level
        sp_temp  = QSpinBox(); sp_temp.setRange(-20, 120); sp_temp.setValue(25); sp_temp.setStyleSheet(INPUT_COLOR)
        self.setCellWidget(r, 6, sp_temp); widgets["temp"] = sp_temp

        # Observed Vol (L) (computed)
        it_vobs = QTableWidgetItem("-"); it_vobs.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 7, it_vobs); widgets["vobs"] = it_vobs

        # VCF (computed)
        it_vcf = QTableWidgetItem("-"); it_vcf.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 8, it_vcf); widgets["vcf"] = it_vcf

        # Vol@15 (L) (computed)
        it_v15 = QTableWidgetItem("-"); it_v15.setFlags(Qt.ItemIsEnabled)
        self.setItem(r, 9, it_v15); widgets["v15"] = it_v15

        # Calc
        btn = QPushButton("Calc")
        btn.clicked.connect(lambda _=False, row=r: self.calc_one(row))
        self.setCellWidget(r, 10, btn)

        widgets["code_full"] = code
        widgets["full100_l"] = full100_l
        widgets["fill_col_item"] = it_fill
        self._row_widgets.append(widgets)

    # ---- IO helpers ----
    def get_row_inputs(self, row: int):
        w = self._row_widgets[row]
        code = w["code_full"]
        is_sounding = w["rb_s"].isChecked()
        level_cm = float(w["level"].value())
        temp_c = float(w["temp"].value())
        dens15 = float(self._dens_provider() or 0.0)
        return code, is_sounding, level_cm, temp_c, dens15

    def set_row_outputs(self, row: int, v_obs_l: Optional[int], vcf: Optional[float], v15_l: Optional[int]):
        w = self._row_widgets[row]
        w["vobs"].setText("-" if v_obs_l is None else f"{int(v_obs_l):,}".replace(",", " "))
        w["vcf"].setText("-" if vcf is None else f"{vcf:.6f}")
        w["v15"].setText("-" if v15_l is None else f"{int(v15_l):,}".replace(",", " "))

    # ---- core calc ----
    def calc_one(self, row: int, trim: float = 0.0, heel: Optional[str] = None):
        code, is_sounding, level_cm, temp_c, dens15 = self.get_row_inputs(row)

        if level_cm == 0 and not is_sounding:
            self.set_row_outputs(row, 0, 1.0, 0)
            return

        try:
            _, _, v_obs_m3 = _compute_volumes(
                name=code,
                trim=trim,
                sounding=level_cm if is_sounding else None,
                ullage=level_cm if not is_sounding else None,
                heel=heel
            )
        except Exception:
            self.set_row_outputs(row, None, None, None)
            raise

        v_obs_m3 = max(0.0, v_obs_m3)
        v_obs_l  = int(round(v_obs_m3 * 1000.0))
        vcf = vcf_54b(dens15, temp_c) if dens15 > 0 else 1.0
        v15_l = int(round(v_obs_m3 * vcf * 1000.0))
        self.set_row_outputs(row, v_obs_l, vcf, v15_l)

    def sum_v15_liters(self, exclude_codes: set[str] | None = None) -> int:
        total = 0
        ex = exclude_codes or set()
        for w in self._row_widgets:
            if w["code_full"] in ex:
                continue
            t = w["v15"].text()
            if t not in ("-", ""):
                try:
                    total += int(t.replace(" ", ""))
                except ValueError:
                    pass
        return total

    def update_fill_column(self, fill_pct: float):
        scale = max(0.0, min(100.0, fill_pct)) / 100.0
        for w in self._row_widgets:
            full100_l = w["full100_l"]
            if full100_l is None:
                w["fill_col_item"].setText("-")
            else:
                w["fill_col_item"].setText(f"{int(round(full100_l * scale)):,}".replace(",", " "))
        self.setHorizontalHeaderItem(3, QTableWidgetItem(f"At {fill_pct:.0f}% (L)"))

    # serialization
    def iter_rows(self):
        for r, w in enumerate(self._row_widgets):
            mode = "So" if w["rb_s"].isChecked() else "Ull"
            level = float(w["level"].value())
            temp = float(w["temp"].value())
            vobs_txt = w["vobs"].text()
            vcf_txt  = w["vcf"].text()
            v15_txt  = w["v15"].text()
            at_fill_txt = w["fill_col_item"].text()
            yield {
                "code": w["code_full"],
                "desc": self.item(r, 1).text() if self.item(r, 1) else "",
                "full100_l": w["full100_l"],
                "at_fill_l": None if at_fill_txt in ("-", "") else int(at_fill_txt.replace(" ", "")),
                "mode": mode,
                "level_cm": level,
                "temp_c": temp,
                "v_obs_l": None if vobs_txt in ("-", "") else int(vobs_txt.replace(" ", "")),
                "vcf": None if vcf_txt in ("-", "") else float(vcf_txt),
                "v15_l": None if v15_txt in ("-", "") else int(v15_txt.replace(" ", "")),
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
            self._row_widgets[i]["level"].setValue(int(row.get("level_cm") or 0))
            self._row_widgets[i]["temp"].setValue(int(row.get("temp_c") or 25))
            # computed
            vobs = row.get("v_obs_l")
            self._row_widgets[i]["vobs"].setText("-" if vobs in (None, "") else f"{int(vobs):,}".replace(",", " "))
            vcf = row.get("vcf")
            self._row_widgets[i]["vcf"].setText("-" if vcf is None else f"{vcf:.6f}")
            v15 = row.get("v15_l")
            self._row_widgets[i]["v15"].setText("-" if v15 in (None, "") else f"{int(v15):,}".replace(",", " "))
            # at fill
            at_fill = row.get("at_fill_l")
            if at_fill is not None:
                self._row_widgets[i]["fill_col_item"].setText(f"{int(at_fill):,}".replace(",", " "))

# Reuse the same dialog class
class RetrieveEntryDialog(QDialog):
    def __init__(self, dates: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Retrieve LO entry")
        self.resize(380, 420)

        v = QVBoxLayout(self)
        self.listw = QListWidget(self)
        for dt in dates:
            QListWidgetItem(dt, self.listw)
        self.listw.setSelectionMode(QAbstractItemView.SingleSelection)
        self.listw.itemDoubleClicked.connect(self.accept)
        v.addWidget(self.listw)

        h = QHBoxLayout()
        h.addStretch(1)
        btnLoad = QPushButton("Load", self)
        btnCancel = QPushButton("Cancel", self)
        btnLoad.clicked.connect(self.accept)
        btnCancel.clicked.connect(self.reject)
        h.addWidget(btnLoad)
        h.addWidget(btnCancel)
        v.addLayout(h)

    def selected_date(self) -> str | None:
        it = self.listw.currentItem()
        return it.text() if it else None

# ------------- Main Window -------------
class MainWindow(QWidget):
    _HEEL_TOKEN_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([PS])\s*$", re.I)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lube Oils – Sheet")
        self.resize(1440, 960)

        # Top bar (normal weight)
        top = QHBoxLayout()
        top.addWidget(QLabel("Date:"))
        self.deDate = QDateEdit(QDate.currentDate()); self.deDate.setCalendarPopup(True)
        self.deDate.setDisplayFormat("dd.MM.yyyy"); self.deDate.setFixedWidth(120)
        top.addWidget(self.deDate)

        top.addSpacing(12); top.addWidget(QLabel("Trim:"))
        self.leTrim = QLineEdit("0.00"); self.leTrim.setFixedWidth(80)
        self.leTrim.setValidator(QDoubleValidator(-10.0, 10.0, 2)); top.addWidget(self.leTrim)

        top.addSpacing(12); top.addWidget(QLabel("Heel:"))
        self.leHeel = QLineEdit("0"); self.leHeel.setFixedWidth(100)
        self.leHeel.setValidator(QDoubleValidator(-10.0, 10.0, 2))
        self.leHeel.setPlaceholderText("e.g., 1 (Port), -0.5 (Stbd), 0,5P, 0,5S")
        self.leHeel.setToolTip("Port: >0 or 0,5P | Starboard: <0 or 0,5S")
        top.addWidget(self.leHeel)

        top.addSpacing(16); top.addWidget(QLabel("Fill % (ME circ):"))
        self.leFillMECirc = QLineEdit("0.00"); self.leFillMECirc.setFixedWidth(80)
        self.leFillMECirc.setValidator(QDoubleValidator(0.0, 100.0, 2)); top.addWidget(self.leFillMECirc)

        top.addSpacing(12); top.addWidget(QLabel("Fill % (ME cyl):"))
        self.leFillMECyl = QLineEdit("0.00"); self.leFillMECyl.setFixedWidth(80)
        self.leFillMECyl.setValidator(QDoubleValidator(0.0, 100.0, 2)); top.addWidget(self.leFillMECyl)

        top.addSpacing(12); top.addWidget(QLabel("Fill % (AE circ):"))
        self.leFillAECirc = QLineEdit("0.00"); self.leFillAECirc.setFixedWidth(80)
        self.leFillAECirc.setValidator(QDoubleValidator(0.0, 100.0, 2)); top.addWidget(self.leFillAECirc)

        top.addStretch(1)

        # helpers
        def bold_title_only(gb: QGroupBox, size: int = 16):
            # Make only the TITLE bold; force contents to normal
            f = gb.font(); f.setPointSize(size); f.setBold(True); gb.setFont(f)
            gb.setStyleSheet(
                "QGroupBox { font-weight: 700; }"
                "QGroupBox > * { font-weight: normal; }"
            )

        def set_normal_font(widget):
            f = widget.font(); f.setBold(False); widget.setFont(f)

        def set_bold(widget):
            f = widget.font(); f.setBold(True); widget.setFont(f)

        # == ME Circulation ==
        g1 = QGroupBox("ME – Circulation Oil"); bold_title_only(g1, 16)
        v1 = QVBoxLayout(g1)

        g1h = QHBoxLayout()
        lblOil1 = QLabel("Oil:"); set_normal_font(lblOil1); g1h.addWidget(lblOil1)
        self.g1OilName = QLineEdit("System Oil"); self.g1OilName.setFixedWidth(220); set_normal_font(self.g1OilName); g1h.addWidget(self.g1OilName)
        g1h.addSpacing(16)
        lblD1 = QLabel("Density@15 (kg/m³):"); set_normal_font(lblD1); g1h.addWidget(lblD1)
        self.g1Dens = QLineEdit("900.0"); self.g1Dens.setValidator(QDoubleValidator(500.0, 1200.0, 3))
        self.g1Dens.setFixedWidth(100); self.g1Dens.setStyleSheet(INPUT_COLOR); set_normal_font(self.g1Dens)
        g1h.addWidget(self.g1Dens); g1h.addStretch(1)
        v1.addLayout(g1h)

        self.tblMECirc = LOTable(ME_CIRC_ROWS, dens_provider=lambda: _parse_float(self.g1Dens.text()))
        set_normal_font(self.tblMECirc)  # table not bold
        v1.addWidget(self.tblMECirc)
        f1Footer = QHBoxLayout()
        self.lblTotMECirc = QLabel("TOTAL 0 (L@15)"); set_bold(self.lblTotMECirc); self.lblTotMECirc.setAlignment(Qt.AlignLeft)
        f1Footer.addWidget(self.lblTotMECirc)
        f1Footer.addStretch(1)
        lblLog1 = QLabel("Log Book (L):"); set_bold(lblLog1); f1Footer.addWidget(lblLog1)
        self.leLogMECirc = QLineEdit("0"); self.leLogMECirc.setFixedWidth(100); self.leLogMECirc.setValidator(QDoubleValidator(0.0, 1e12, 2)); set_bold(self.leLogMECirc); f1Footer.addWidget(self.leLogMECirc)
        f1Footer.addSpacing(12); lblDiff1 = QLabel("Diff:"); set_bold(lblDiff1); f1Footer.addWidget(lblDiff1)
        self.lblDiffMECirc = QLabel("0"); set_bold(self.lblDiffMECirc); f1Footer.addWidget(self.lblDiffMECirc)
        v1.addLayout(f1Footer)

        # == ME Cylinder ==
        g2 = QGroupBox("ME – Cylinder Oil"); bold_title_only(g2, 16)
        v2 = QVBoxLayout(g2)

        g2h = QHBoxLayout()
        lblOil2 = QLabel("Oil:"); set_normal_font(lblOil2); g2h.addWidget(lblOil2)
        self.g2OilName = QLineEdit("Cylinder Oil"); self.g2OilName.setFixedWidth(220); set_normal_font(self.g2OilName); g2h.addWidget(self.g2OilName)
        g2h.addSpacing(16)
        lblD2 = QLabel("Density@15 (kg/m³):"); set_normal_font(lblD2); g2h.addWidget(lblD2)
        self.g2Dens = QLineEdit("900.0"); self.g2Dens.setValidator(QDoubleValidator(500.0, 1200.0, 3))
        self.g2Dens.setFixedWidth(100); self.g2Dens.setStyleSheet(INPUT_COLOR); set_normal_font(self.g2Dens)
        g2h.addWidget(self.g2Dens); g2h.addStretch(1)
        v2.addLayout(g2h)

        self.tblMECyl = LOTable(ME_CYL_ROWS, dens_provider=lambda: _parse_float(self.g2Dens.text()))
        set_normal_font(self.tblMECyl)
        v2.addWidget(self.tblMECyl)
        f2Footer = QHBoxLayout()
        self.lblTotMECyl = QLabel("TOTAL 0 (L@15)"); set_bold(self.lblTotMECyl); self.lblTotMECyl.setAlignment(Qt.AlignLeft)
        f2Footer.addWidget(self.lblTotMECyl)
        f2Footer.addStretch(1)
        lblLog2 = QLabel("Log Book (L):"); set_bold(lblLog2); f2Footer.addWidget(lblLog2)
        self.leLogMECyl = QLineEdit("0"); self.leLogMECyl.setFixedWidth(100); self.leLogMECyl.setValidator(QDoubleValidator(0.0, 1e12, 2)); set_bold(self.leLogMECyl); f2Footer.addWidget(self.leLogMECyl)
        f2Footer.addSpacing(12); lblDiff2 = QLabel("Diff:"); set_bold(lblDiff2); f2Footer.addWidget(lblDiff2)
        self.lblDiffMECyl = QLabel("0"); set_bold(self.lblDiffMECyl); f2Footer.addWidget(self.lblDiffMECyl)
        v2.addLayout(f2Footer)

        # == AE Circulation ==
        g3 = QGroupBox("AE – Circulation Oil"); bold_title_only(g3, 16)
        v3 = QVBoxLayout(g3)

        g3h = QHBoxLayout()
        lblOil3 = QLabel("Oil:"); set_normal_font(lblOil3); g3h.addWidget(lblOil3)
        self.g3OilName = QLineEdit("System Oil"); self.g3OilName.setFixedWidth(220); set_normal_font(self.g3OilName); g3h.addWidget(self.g3OilName)
        g3h.addSpacing(16)
        lblD3 = QLabel("Density@15 (kg/m³):"); set_normal_font(lblD3); g3h.addWidget(lblD3)
        self.g3Dens = QLineEdit("900.0"); self.g3Dens.setValidator(QDoubleValidator(500.0, 1200.0, 3))
        self.g3Dens.setFixedWidth(100); self.g3Dens.setStyleSheet(INPUT_COLOR); set_normal_font(self.g3Dens)
        g3h.addWidget(self.g3Dens); g3h.addStretch(1)
        v3.addLayout(g3h)

        self.tblAECirc = LOTable(AE_CIRC_ROWS, dens_provider=lambda: _parse_float(self.g3Dens.text()))
        set_normal_font(self.tblAECirc)
        v3.addWidget(self.tblAECirc)
        f3Footer = QHBoxLayout()
        self.lblTotAECirc = QLabel("TOTAL 0 (L@15)"); set_bold(self.lblTotAECirc); self.lblTotAECirc.setAlignment(Qt.AlignLeft)
        f3Footer.addWidget(self.lblTotAECirc)
        f3Footer.addStretch(1)
        lblLog3 = QLabel("Log Book (L):"); set_bold(lblLog3); f3Footer.addWidget(lblLog3)
        self.leLogAECirc = QLineEdit("0"); self.leLogAECirc.setFixedWidth(100); self.leLogAECirc.setValidator(QDoubleValidator(0.0, 1e12, 2)); set_bold(self.leLogAECirc); f3Footer.addWidget(self.leLogAECirc)
        f3Footer.addSpacing(12); lblDiff3 = QLabel("Diff:"); set_bold(lblDiff3); f3Footer.addWidget(lblDiff3)
        self.lblDiffAECirc = QLabel("0"); set_bold(self.lblDiffAECirc); f3Footer.addWidget(self.lblDiffAECirc)
        v3.addLayout(f3Footer)

        # Bottom bar
        bottom = QHBoxLayout()
        self.btnSave = QPushButton("Save"); self.btnSave.clicked.connect(self.save_to_ops); bottom.addWidget(self.btnSave)
        self.btnLoad = QPushButton("Retrieve"); self.btnLoad.clicked.connect(self.retrieve_from_ops); bottom.addWidget(self.btnLoad)
        self.btnCalcAll = QPushButton("Calculate"); self.btnCalcAll.clicked.connect(self.calc_all); bottom.addWidget(self.btnCalcAll)
        bottom.addStretch(1)
        self.lblGrand = QLabel("ME circ: 0    ME cyl: 0    AE circ: 0    Grand Total: 0 (L@15)")
        set_normal_font(self.lblGrand)
        bottom.addWidget(self.lblGrand); bottom.addStretch(1)
        self.lblDB = QLabel(f"DB: {DB_CAP_PATH}"); set_normal_font(self.lblDB); bottom.addWidget(self.lblDB)

        # Layout
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(g1)
        layout.addWidget(g2)
        layout.addWidget(g3)
        layout.addLayout(bottom)

        # Handlers
        self.install_row_handlers(self.tblMECirc)
        self.install_row_handlers(self.tblMECyl)
        self.install_row_handlers(self.tblAECirc)

        # Fill% handlers
        self.leFillMECirc.editingFinished.connect(lambda: self.tblMECirc.update_fill_column(self._current_fill("me_circ")))
        self.leFillMECyl.editingFinished.connect(lambda: self.tblMECyl.update_fill_column(self._current_fill("me_cyl")))
        self.leFillAECirc.editingFinished.connect(lambda: self.tblAECirc.update_fill_column(self._current_fill("ae_circ")))

        # Density edits should refresh VCF/Vol@15
        self.g1Dens.editingFinished.connect(self.calc_all)
        self.g2Dens.editingFinished.connect(self.calc_all)
        self.g3Dens.editingFinished.connect(self.calc_all)

        # LogBook edits refresh diffs
        self.leLogMECirc.editingFinished.connect(self.update_totals)
        self.leLogMECyl.editingFinished.connect(self.update_totals)
        self.leLogAECirc.editingFinished.connect(self.update_totals)

        # Initialize headers
        self.tblMECirc.update_fill_column(0.0)
        self.tblMECyl.update_fill_column(0.0)
        self.tblAECirc.update_fill_column(0.0)

        self._ensure_ops_schema()

    # ----- helpers -----
    def _set_diff_style(self, label: QLabel, diff: float):
        # Label font already bold; we only colorize by sign.
        if diff > 0:
            label.setStyleSheet("color:#0a7a0a;")
        elif diff < 0:
            label.setStyleSheet("color:#c21807;")
        else:
            label.setStyleSheet("color:inherit;")

    def _parse_heel(self, raw: str) -> str:
        s = raw.strip().upper().replace(" ", "")
        if not s:
            return "0"
        m = self._HEEL_TOKEN_RE.match(s)
        if m:
            val = m.group(1).replace(",", "."); side = m.group(2).upper()
            try: float(val)
            except ValueError: return "0"
            return f"{val}{side}"
        try:
            v = float(s.replace(",", "."))
        except ValueError:
            if s in ("P","S"): return "0"+s
            return "0"
        if v == 0: return "0"
        return f"{abs(v)}{'P' if v>0 else 'S'}"

    def _current_trim_heel(self) -> Tuple[float, str]:
        try:
            trim = float(self.leTrim.text().replace(",", "."))
        except ValueError:
            msg("Trim must be a number (e.g., -3, 0, 1.5).")
            raise
        return trim, self._parse_heel(self.leHeel.text())

    def _current_fill(self, which: str) -> float:
        le = {"me_circ": self.leFillMECirc, "me_cyl": self.leFillMECyl, "ae_circ": self.leFillAECirc}[which]
        try: v = float(le.text().replace(",", "."))
        except ValueError: v = 0.0
        return max(0.0, min(100.0, v))

    def install_row_handlers(self, table: LOTable):
        for r in range(table.rowCount()):
            btn = table.cellWidget(r, 10)
            try: btn.clicked.disconnect()
            except Exception: pass
            btn.clicked.connect(lambda _=False, row=r, t=table: self.calc_one_row(t, row))

    # ----- ops -----
    def calc_one_row(self, table: LOTable, row: int):
        try:
            trim, heel = self._current_trim_heel()
        except Exception:
            return
        try:
            table.calc_one(row, trim=trim, heel=heel)
        except Exception:
            code = table._row_widgets[row]["code_full"]
            msg(f"{code}: No base volume found (check name/level/trim/heel).")
            return
        self.update_totals()

    def calc_all(self):
        try:
            trim, heel = self._current_trim_heel()
        except Exception:
            return
        for tbl in (self.tblMECirc, self.tblMECyl, self.tblAECirc):
            for r in range(tbl.rowCount()):
                try:
                    tbl.calc_one(r, trim=trim, heel=heel)
                except Exception:
                    pass
        self.update_totals()

    def update_totals(self):
        me_circ = self.tblMECirc.sum_v15_liters(exclude_codes=EXCLUDE_FROM_TOTALS)
        me_cyl  = self.tblMECyl.sum_v15_liters()
        ae_circ = self.tblAECirc.sum_v15_liters()

        # totals
        self.lblTotMECirc.setText(f"TOTAL {me_circ:,} (L@15)".replace(",", " "))
        self.lblTotMECyl.setText(f"TOTAL {me_cyl:,} (L@15)".replace(",", " "))
        self.lblTotAECirc.setText(f"TOTAL {ae_circ:,} (L@15)".replace(",", " "))

        # diffs
        log1 = _parse_int(self.leLogMECirc.text())
        log2 = _parse_int(self.leLogMECyl.text())
        log3 = _parse_int(self.leLogAECirc.text())
        d1 = me_circ - log1; d2 = me_cyl - log2; d3 = ae_circ - log3
        self.lblDiffMECirc.setText(f"{d1:,}".replace(",", " ")); self._set_diff_style(self.lblDiffMECirc, d1)
        self.lblDiffMECyl.setText(f"{d2:,}".replace(",", " "));   self._set_diff_style(self.lblDiffMECyl, d2)
        self.lblDiffAECirc.setText(f"{d3:,}".replace(",", " "));  self._set_diff_style(self.lblDiffAECirc, d3)

        # grand bar
        self.lblGrand.setText(
            "ME circ: {0}    ME cyl: {1}    AE circ: {2}    Grand Total: {3} (L@15)".format(
                f"{me_circ:,}".replace(",", " "),
                f"{me_cyl:,}".replace(",", " "),
                f"{ae_circ:,}".replace(",", " "),
                f"{(me_circ + me_cyl + ae_circ):,}".replace(",", " "),
            )
        )

    # ----- persistence -----
    def _ensure_ops_schema(self):
        DB_OPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()

        # Create tables if they don't exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lo_header (
                date_text     TEXT PRIMARY KEY,
                trim          REAL,
                heel          TEXT,
                g1_oil_name   TEXT,
                g1_dens15     REAL,
                g2_oil_name   TEXT,
                g2_dens15     REAL,
                g3_oil_name   TEXT,
                g3_dens15     REAL,
                fill_me_circ  REAL,
                fill_me_cyl   REAL,
                fill_ae_circ  REAL,
                log_me_circ   INTEGER,
                log_me_cyl    INTEGER,
                log_ae_circ   INTEGER,
                total_me_circ INTEGER,
                total_me_cyl  INTEGER,
                total_ae_circ INTEGER,
                grand_total   INTEGER,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lo_rows (
                date_text  TEXT,
                group_tag  TEXT,   -- 'ME_CIRC' | 'ME_CYL' | 'AE_CIRC'
                code       TEXT,
                desc       TEXT,
                full100_l  INTEGER,
                at_fill_l  INTEGER,
                mode       TEXT,
                level_cm   REAL,
                temp_c     REAL,
                v_obs_l    INTEGER,
                vcf        REAL,
                v15_l      INTEGER,
                PRIMARY KEY (date_text, group_tag, code)
            )
        """)

        # Migration: add any missing columns
        def existing_cols(table: str) -> set[str]:
            cur.execute(f'PRAGMA table_info("{table}")')
            return {row[1] for row in cur.fetchall()}

        needed_header_cols = {
            "date_text": "TEXT", "trim": "REAL", "heel": "TEXT",
            "g1_oil_name": "TEXT", "g1_dens15": "REAL",
            "g2_oil_name": "TEXT", "g2_dens15": "REAL",
            "g3_oil_name": "TEXT", "g3_dens15": "REAL",
            "fill_me_circ": "REAL", "fill_me_cyl": "REAL", "fill_ae_circ": "REAL",
            "log_me_circ": "INTEGER", "log_me_cyl": "INTEGER", "log_ae_circ": "INTEGER",
            "total_me_circ": "INTEGER", "total_me_cyl": "INTEGER", "total_ae_circ": "INTEGER",
            "grand_total": "INTEGER", "created_at": "TEXT"
        }
        have = existing_cols("lo_header")
        for col, coltype in needed_header_cols.items():
            if col not in have:
                cur.execute(f'ALTER TABLE lo_header ADD COLUMN {col} {coltype}')

        needed_rows_cols = {
            "date_text": "TEXT", "group_tag": "TEXT", "code": "TEXT", "desc": "TEXT",
            "full100_l": "INTEGER", "at_fill_l": "INTEGER", "mode": "TEXT",
            "level_cm": "REAL", "temp_c": "REAL", "v_obs_l": "INTEGER",
            "vcf": "REAL", "v15_l": "INTEGER",
        }
        have_rows = existing_cols("lo_rows")
        for col, coltype in needed_rows_cols.items():
            if col not in have_rows:
                cur.execute(f'ALTER TABLE lo_rows ADD COLUMN {col} {coltype}')

        con.commit()
        con.close()

    def _collect_state(self) -> Dict[str, Any]:
        date_text = self.deDate.date().toString("yyyy-MM-dd")
        try: trim = float(self.leTrim.text().replace(",", "."))
        except Exception: trim = 0.0
        heel = self._parse_heel(self.leHeel.text())

        me_circ = self.tblMECirc.sum_v15_liters(exclude_codes=EXCLUDE_FROM_TOTALS)
        me_cyl  = self.tblMECyl.sum_v15_liters()
        ae_circ = self.tblAECirc.sum_v15_liters()

        return {
            "header": {
                "date_text": date_text,
                "trim": trim, "heel": heel,
                "g1_oil_name": self.g1OilName.text().strip(),
                "g1_dens15": _parse_float(self.g1Dens.text()),
                "g2_oil_name": self.g2OilName.text().strip(),
                "g2_dens15": _parse_float(self.g2Dens.text()),
                "g3_oil_name": self.g3OilName.text().strip(),
                "g3_dens15": _parse_float(self.g3Dens.text()),
                "fill_me_circ": self._current_fill("me_circ"),
                "fill_me_cyl":  self._current_fill("me_cyl"),
                "fill_ae_circ": self._current_fill("ae_circ"),
                "log_me_circ":  _parse_int(self.leLogMECirc.text()),
                "log_me_cyl":   _parse_int(self.leLogMECyl.text()),
                "log_ae_circ":  _parse_int(self.leLogAECirc.text()),
                "total_me_circ": me_circ,
                "total_me_cyl":  me_cyl,
                "total_ae_circ": ae_circ,
                "grand_total":   me_circ + me_cyl + ae_circ,
            },
            "rows_me_circ": list(self.tblMECirc.iter_rows()),
            "rows_me_cyl":  list(self.tblMECyl.iter_rows()),
            "rows_ae_circ": list(self.tblAECirc.iter_rows()),
        }

    def save_to_ops(self):
        st = self._collect_state()
        h = st["header"]
        con = sqlite3.connect(DB_OPS_PATH); cur = con.cursor()
        cur.execute("""
            INSERT INTO lo_header (date_text, trim, heel,
                g1_oil_name, g1_dens15, g2_oil_name, g2_dens15, g3_oil_name, g3_dens15,
                fill_me_circ, fill_me_cyl, fill_ae_circ,
                log_me_circ, log_me_cyl, log_ae_circ,
                total_me_circ, total_me_cyl, total_ae_circ, grand_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_text) DO UPDATE SET
                trim=excluded.trim, heel=excluded.heel,
                g1_oil_name=excluded.g1_oil_name, g1_dens15=excluded.g1_dens15,
                g2_oil_name=excluded.g2_oil_name, g2_dens15=excluded.g2_dens15,
                g3_oil_name=excluded.g3_oil_name, g3_dens15=excluded.g3_dens15,
                fill_me_circ=excluded.fill_me_circ,
                fill_me_cyl=excluded.fill_me_cyl,
                fill_ae_circ=excluded.fill_ae_circ,
                log_me_circ=excluded.log_me_circ,
                log_me_cyl=excluded.log_me_cyl,
                log_ae_circ=excluded.log_ae_circ,
                total_me_circ=excluded.total_me_circ,
                total_me_cyl=excluded.total_me_cyl,
                total_ae_circ=excluded.total_ae_circ,
                grand_total=excluded.grand_total
        """, (
            h["date_text"], h["trim"], h["heel"],
            h["g1_oil_name"], h["g1_dens15"], h["g2_oil_name"], h["g2_dens15"], h["g3_oil_name"], h["g3_dens15"],
            h["fill_me_circ"], h["fill_me_cyl"], h["fill_ae_circ"],
            h["log_me_circ"], h["log_me_cyl"], h["log_ae_circ"],
            h["total_me_circ"], h["total_me_cyl"], h["total_ae_circ"], h["grand_total"]
        ))
        cur.execute("DELETE FROM lo_rows WHERE date_text=?", (h["date_text"],))
        for tag, rows in (("ME_CIRC", st["rows_me_circ"]), ("ME_CYL", st["rows_me_cyl"]), ("AE_CIRC", st["rows_ae_circ"])):
            for r in rows:
                cur.execute("""
                    INSERT INTO lo_rows (date_text, group_tag, code, desc, full100_l, at_fill_l,
                                         mode, level_cm, temp_c, v_obs_l, vcf, v15_l)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    h["date_text"], tag, r["code"], r["desc"], r["full100_l"], r["at_fill_l"],
                    r["mode"], r["level_cm"], r["temp_c"], r["v_obs_l"], r["vcf"], r["v15_l"]
                ))
        con.commit(); con.close()
        info(f"Saved LO sheet to {DB_OPS_PATH}\nDate: {h['date_text']}")

    def retrieve_from_ops(self):
        con = sqlite3.connect(DB_OPS_PATH)
        cur = con.cursor()

        try:
            cur.execute("SELECT date_text FROM lo_header ORDER BY date_text DESC")
            dates = [r[0] for r in cur.fetchall()]
        except sqlite3.Error:
            dates = []

        if not dates:
            con.close()
            msg("No LO saved entries found in ops.db.")
            return

        dlg = RetrieveEntryDialog(dates, self)
        if dlg.exec() != QDialog.Accepted:
            con.close()
            return
        date_text = dlg.selected_date()
        if not date_text:
            con.close()
            return

        cur.execute(
            """SELECT trim, heel,
                    g1_oil_name, g1_dens15, g2_oil_name, g2_dens15, g3_oil_name, g3_dens15,
                    fill_me_circ, fill_me_cyl, fill_ae_circ,
                    log_me_circ, log_me_cyl, log_ae_circ
            FROM lo_header WHERE date_text=?""",
            (date_text,)
        )
        row = cur.fetchone()
        if not row:
            con.close()
            msg("Selected date not found.")
            return

        (trim, heel, g1name, g1dens, g2name, g2dens, g3name, g3dens,
        fill1, fill2, fill3, log1, log2, log3) = row

        cur.execute(
            """SELECT group_tag, code, desc, full100_l, at_fill_l, mode, level_cm, temp_c, v_obs_l, vcf, v15_l
            FROM lo_rows WHERE date_text=?""",
            (date_text,)
        )
        rows = cur.fetchall()
        con.close()

        # Header fields
        d = QDate.fromString(date_text, "yyyy-MM-dd")
        if d.isValid():
            self.deDate.setDate(d)

        self.leTrim.setText(f"{(trim or 0):.2f}")
        self.leHeel.setText(heel or "0")  # keep token like "0.5P"/"0.5S"

        self.g1OilName.setText(g1name or "")
        self.g1Dens.setText("" if g1dens is None else f"{g1dens}")
        self.g2OilName.setText(g2name or "")
        self.g2Dens.setText("" if g2dens is None else f"{g2dens}")
        self.g3OilName.setText(g3name or "")
        self.g3Dens.setText("" if g3dens is None else f"{g3dens}")

        self.leFillMECirc.setText(f"{(fill1 or 0):.2f}")
        self.leFillMECyl.setText(f"{(fill2 or 0):.2f}")
        self.leFillAECirc.setText(f"{(fill3 or 0):.2f}")

        self.leLogMECirc.setText(f"{(log1 or 0):.2f}")
        self.leLogMECyl.setText(f"{(log2 or 0):.2f}")
        self.leLogAECirc.setText(f"{(log3 or 0):.2f}")

        # Rows
        r1: List[Dict[str, Any]] = []
        r2: List[Dict[str, Any]] = []
        r3: List[Dict[str, Any]] = []
        for (tag, code, desc, full100_l, at_fill_l, mode, level_cm, temp_c, v_obs_l, vcf, v15_l) in rows:
            dct = {
                "code": code, "desc": desc,
                "full100_l": full100_l, "at_fill_l": at_fill_l,
                "mode": mode, "level_cm": level_cm, "temp_c": temp_c,
                "v_obs_l": v_obs_l, "vcf": vcf, "v15_l": v15_l
            }
            if tag == "ME_CIRC":
                r1.append(dct)
            elif tag == "ME_CYL":
                r2.append(dct)
            elif tag == "AE_CIRC":
                r3.append(dct)

        self.tblMECirc.apply_rows(r1)
        self.tblMECyl.apply_rows(r2)
        self.tblAECirc.apply_rows(r3)

        # Refresh headers/columns and totals
        self.tblMECirc.update_fill_column(self._current_fill("me_circ"))
        self.tblMECyl.update_fill_column(self._current_fill("me_cyl"))
        self.tblAECirc.update_fill_column(self._current_fill("ae_circ"))
        self.calc_all()

        info(f"Loaded LO entry for {date_text} from {DB_OPS_PATH}")

# --------- main ---------
def main():
    if not DB_CAP_PATH.exists():
        msg(f"Capacity DB not found:\n{DB_CAP_PATH}\n(Load or point to the correct DB.)")
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
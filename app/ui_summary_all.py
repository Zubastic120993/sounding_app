
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QGroupBox, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QSizePolicy, QPushButton, QMessageBox
)

# ----- import path so "from app..." works when run as script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_OPS_PATH = ROOT / "data" / "ops.db"
DB_CAP_PATH = ROOT / "data" / "sounding.db"  # just for display in footer

YELLOW = "background-color:#fff6cc;"


def warn(text: str):
    m = QMessageBox()
    m.setIcon(QMessageBox.Warning)
    m.setText(text)
    m.exec()


# ---------- tiny DB helpers ----------
def _open() -> sqlite3.Connection:
    return sqlite3.connect(DB_OPS_PATH)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def list_columns(con: sqlite3.Connection, table: str) -> List[str]:
    try:
        cur = con.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]
    except Exception:
        return []


def fetchall(con: sqlite3.Connection, sql: str, params: Tuple = ()) -> List[Tuple]:
    try:
        cur = con.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return []


# ---------- reusable compact table (read-only) ----------
class ReadOnlyTable(QTableWidget):
    def __init__(self, headers: List[str], stretch_last=True, row_height=26, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch if stretch_last else QHeaderView.ResizeToContents)
        self.horizontalHeader().setFixedHeight(28)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def load_rows(self, rows: List[List[Any]]):
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                txt = "-" if val is None else (f"{val}" if not isinstance(val, float) else f"{val:g}")
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemIsEnabled)
                self.setItem(r, c, it)
        self._set_min_height()

    def _set_min_height(self):
        header_h = self.horizontalHeader().height()
        row_h = self.verticalHeader().defaultSectionSize()
        total = header_h + max(1, self.rowCount()) * row_h + 8
        self.setMinimumHeight(total)


# ---------- group block with totals + details ----------
class GroupBlock(QWidget):
    def __init__(self, title: str, total_headers: List[str], detail_headers: List[str]):
        super().__init__()
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)

        g = QGroupBox(title)
        g.setFont(title_font)
        g.setFlat(True)

        v = QVBoxLayout(g)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        self.tblTotals = ReadOnlyTable(total_headers, stretch_last=True)
        self.tblDetails = ReadOnlyTable(detail_headers, stretch_last=True)

        v.addWidget(self.tblTotals)
        v.addWidget(self.tblDetails)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(g)

    def set_totals(self, rows: List[List[Any]]):
        self.tblTotals.load_rows(rows)

    def set_details(self, rows: List[List[Any]]):
        self.tblDetails.load_rows(rows)


# ---------- summarizer ----------
class SummaryWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Daily Tanks Summary")
        self.resize(1280, 760)

        # top bar: date picker built from all headers
        top = QHBoxLayout()
        top.addWidget(QLabel("Date:"))
        self.cbDate = QComboBox()
        self.cbDate.setMinimumWidth(160)
        top.addWidget(self.cbDate)

        self.btnRefresh = QPushButton("Refresh")
        self.btnRefresh.clicked.connect(self.refresh_current)
        top.addWidget(self.btnRefresh)
        top.addStretch(1)

        # group blocks
        # Fuel
        fuel_total_headers = ["Metric", "HFO (t)", "MGO (t)", "Grand Total (t)", "Log HFO (t)", "Diff HFO (t)", "Log MGO (t)", "Diff MGO (t)"]
        fuel_detail_headers = ["Tk", "Description", "Mode", "Level (cm)", "Temp (°C)", "Density@15", "VCF", "Observed (m³)", "Vol@15 (m³)", "Mass (t)"]
        self.grpFuel = GroupBlock("Fuel Tanks", fuel_total_headers, fuel_detail_headers)

        # Lube oils
        lo_total_headers = ["Metric", "ME circ (L@15)", "ME cyl (L@15)", "AE circ (L@15)", "Grand Total (L@15)"]
        lo_detail_headers = ["Group", "Tk", "Description", "Mode", "Level (cm)", "Temp (°C)", "Density@15", "VCF", "Observed (L)", "Vol@15 (L)"]
        self.grpLO = GroupBlock("Lube Oils", lo_total_headers, lo_detail_headers)

        # Other tanks
        other_total_headers = ["Metric", "Fresh Water (m³)", "UREA (m³)", "Misc (m³)", "Other (m³)", "Grand Total (m³)"]
        other_detail_headers = ["Group", "Tk", "Description", "Mode", "Level (cm)", "Observed Vol (m³)", "At % (m³)"]
        self.grpOther = GroupBlock("Other Tanks", other_total_headers, other_detail_headers)

        # bottom bar
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(QLabel(f"DB: {DB_OPS_PATH} | Cap DB: {DB_CAP_PATH}"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self.grpFuel)
        layout.addWidget(self.grpLO)
        layout.addWidget(self.grpOther)
        layout.addLayout(bottom)

        self._populate_dates()
        self.cbDate.currentIndexChanged.connect(self.refresh_current)
        if self.cbDate.count() > 0:
            self.cbDate.setCurrentIndex(0)
            self.refresh_current()

    # ---- dates ----
    def _populate_dates(self):
        dates: List[str] = []
        try:
            con = _open()
        except Exception:
            warn(f"Cannot open {DB_OPS_PATH}")
            return
        for t in ("ops_header", "lo_header", "misc_header"):
            if table_exists(con, t):
                for (d,) in fetchall(con, f"SELECT date_text FROM {t} ORDER BY date_text DESC"):
                    if d not in dates:
                        dates.append(d)
        con.close()
        dates.sort(reverse=True)
        self.cbDate.clear()
        self.cbDate.addItems(dates)

    # ---- refresh selected date ----
    def refresh_current(self):
        if self.cbDate.currentText().strip() == "":
            return
        date_text = self.cbDate.currentText()
        try:
            con = _open()
        except Exception:
            warn(f"Cannot open {DB_OPS_PATH}")
            return

        try:
            self._load_fuel(con, date_text)
            self._load_lube_oils(con, date_text)
            self._load_other(con, date_text)
        finally:
            con.close()

    # ---- load fuel group ----
    def _load_fuel(self, con: sqlite3.Connection, date_text: str):
        if not table_exists(con, "ops_header"):
            self.grpFuel.set_totals([["Totals", "-", "-", "-", "-", "-", "-", "-"]])
            self.grpFuel.set_details([])
            return

        hdr_cols = list_columns(con, "ops_header")
        row = fetchall(con,
                       "SELECT trim, heel, fill_hfo, fill_mgo, log_hfo, log_mgo, total_hfo, total_mgo, grand_total "
                       "FROM ops_header WHERE date_text=?",
                       (date_text,))
        if row:
            _, _, _, _, log_hfo, log_mgo, tot_hfo, tot_mgo, grand = row[0]
            diff_hfo = (tot_hfo or 0) - (log_hfo or 0) if log_hfo is not None else None
            diff_mgo = (tot_mgo or 0) - (log_mgo or 0) if log_mgo is not None else None
            self.grpFuel.set_totals([["Totals", tot_hfo, tot_mgo, grand, log_hfo, diff_hfo, log_mgo, diff_mgo]])
        else:
            self.grpFuel.set_totals([["Totals", "-", "-", "-", "-", "-", "-", "-"]])

        # details
        details: List[List[Any]] = []
        if table_exists(con, "ops_rows"):
            cols = list_columns(con, "ops_rows")
            rows = fetchall(con, "SELECT fuel, code, desc, mode, level_cm, temp_c, dens15, vcf, v15, mass_t "
                                 "FROM ops_rows WHERE date_text=? ORDER BY fuel, code", (date_text,))
            # try to show Observed if present
            observed_exists = "v_obs" in cols or "obs_m3" in cols
            obs_name = "v_obs" if "v_obs" in cols else ("obs_m3" if "obs_m3" in cols else None)

            for (fuel, code, desc, mode, level, temp, dens, vcf, v15, mass) in rows:
                # Tk/Description/Mode/Level/Temp/Density/VCF/Observed/Vol15/Mass
                observed = None
                if observed_exists:
                    # fetch observed for this row if column exists
                    r2 = fetchall(con, f"SELECT {obs_name} FROM ops_rows WHERE date_text=? AND code=? LIMIT 1",
                                  (date_text, code))
                    observed = r2[0][0] if r2 else None
                details.append([code.split("_", 1)[0], desc, mode, level, temp, dens, vcf, observed, v15, mass])
        self.grpFuel.set_details(details)

    # ---- load lube oils group ----
    def _load_lube_oils(self, con: sqlite3.Connection, date_text: str):
        if not table_exists(con, "lo_header"):
            self.grpLO.set_totals([["Totals", "-", "-", "-", "-"]])
            self.grpLO.set_details([])
            return

        # totals from header (be tolerant to column names)
        cols = list_columns(con, "lo_header")
        # try common names
        col_map = {
            "me_circ": None,
            "me_cyl": None,
            "ae_circ": None,
            "grand_total": None
        }
        # candidates by prefix
        for c in cols:
            lc = c.lower()
            if "me_circ" in lc and col_map["me_circ"] is None:
                col_map["me_circ"] = c
            elif "me_cyl" in lc and col_map["me_cyl"] is None:
                col_map["me_cyl"] = c
            elif "ae_circ" in lc and col_map["ae_circ"] is None:
                col_map["ae_circ"] = c
            elif "grand_total" in lc and col_map["grand_total"] is None:
                col_map["grand_total"] = c

        # fallback to zero/None
        vals = fetchall(con,
                        f"SELECT {', '.join([col_map[k] if col_map[k] else 'NULL' for k in ['me_circ','me_cyl','ae_circ','grand_total']])} "
                        f"FROM lo_header WHERE date_text=?",
                        (date_text,))
        if vals:
            me_circ, me_cyl, ae_circ, gtot = vals[0]
            self.grpLO.set_totals([["Totals", me_circ, me_cyl, ae_circ, gtot]])
        else:
            self.grpLO.set_totals([["Totals", "-", "-", "-", "-"]])

        # details from lo_rows (be forgiving to names)
        details: List[List[Any]] = []
        if table_exists(con, "lo_rows"):
            rcols = list_columns(con, "lo_rows")
            # choose available columns with reasonable defaults
            sel_parts = []
            for want in ["group_name", "code", "desc", "mode", "level_cm", "temp_c", "dens15", "vcf", "v_obs_l", "v15_l"]:
                if want in rcols:
                    sel_parts.append(want)
                else:
                    sel_parts.append(f"NULL AS {want}")
            sql = f"SELECT {', '.join(sel_parts)} FROM lo_rows WHERE date_text=? ORDER BY group_name, code"
            for row in fetchall(con, sql, (date_text,)):
                g, code, desc, mode, level, temp, dens, vcf, vobs, v15 = row
                details.append([g, (code or "").split("_", 1)[0], desc, mode, level, temp, dens, vcf, vobs, v15])
        self.grpLO.set_details(details)

    # ---- load other tanks group ----
    def _load_other(self, con: sqlite3.Connection, date_text: str):
        if not table_exists(con, "misc_header"):
            self.grpOther.set_totals([["Totals", "-", "-", "-", "-", "-"]])
            self.grpOther.set_details([])
            return

        row = fetchall(con,
                       "SELECT total_fw, total_urea, total_misc, total_other, grand_total "
                       "FROM misc_header WHERE date_text=?",
                       (date_text,))
        if row:
            fw, urea, misc, other, grand = row[0]
            self.grpOther.set_totals([["Totals", fw, urea, misc, other, grand]])
        else:
            self.grpOther.set_totals([["Totals", "-", "-", "-", "-", "-"]])

        details: List[List[Any]] = []
        if table_exists(con, "misc_rows"):
            rcols = list_columns(con, "misc_rows")
            sel_parts = []
            for want in ["group_name", "code", "desc", "mode", "level_cm", "v_obs", "at_fill_m3"]:
                if want in rcols:
                    sel_parts.append(want)
                else:
                    sel_parts.append(f"NULL AS {want}")
            sql = f"SELECT {', '.join(sel_parts)} FROM misc_rows WHERE date_text=? ORDER BY group_name, code"
            for row in fetchall(con, sql, (date_text,)):
                g, code, desc, mode, level, vobs, atfill = row
                details.append([g, (code or '').split("_", 1)[0], desc, mode, level, vobs, atfill])
        self.grpOther.set_details(details)


# ------------ main ------------
def main():
    if not DB_OPS_PATH.exists():
        warn(f"ops.db not found:\n{DB_OPS_PATH}")
    app = QApplication(sys.argv)
    w = SummaryWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
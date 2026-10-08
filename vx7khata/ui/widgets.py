"""Small reusable Qt widgets and helpers."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QCompleter, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import dates, money
from ..labels import JAMA_LABEL, UDHAAR_LABEL
from ..service import KhataError
from . import theme

MIN_DATE = QDate(dates.MIN_YEAR, 1, 1)
DISPLAY_FORMAT = "dd-MM-yyyy"


def danger_color() -> str:
    return theme.current()["danger"]


def success_color() -> str:
    return theme.current()["success"]


def balance_color(paise: int) -> Optional[str]:
    if paise > 0:
        return danger_color()  # customer owes
    if paise < 0:
        return success_color()  # advance / overpaid
    return None


def set_kind(widget: QWidget, kind: str) -> None:
    """Switch a widget's 'kind' style property (danger/success/warning/...) and re-apply the stylesheet."""
    widget.setProperty("kind", kind)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def parse_typed_date(text: str) -> Optional[QDate]:
    """Parse what a person types into a date box. Accepts 07-09-2026, 7/9/2026, 7.9.26, 07092026.

    Returns None when the text is not a real date."""
    t = text.strip().replace("/", "-").replace(".", "-").replace(" ", "-")
    if not t:
        return None
    if t.isdigit():
        if len(t) == 8:
            t = f"{t[0:2]}-{t[2:4]}-{t[4:8]}"
        elif len(t) == 6:
            t = f"{t[0:2]}-{t[2:4]}-20{t[4:6]}"
        else:
            return None
    parts = t.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    d, m, y = (int(p) for p in parts)
    if y < 100:
        y += 2000
    q = QDate(y, m, d)
    return q if q.isValid() else None


class DateField(QWidget):
    """Date box: type DD-MM-YYYY freely (Ctrl+A / select-all and retype works) or use the calendar button.

    Drop-in replacement for the old QDateEdit: date(), setDate(), dateChanged, setMaximumDate(), text_dmy(), set_iso().
    """

    dateChanged = Signal(QDate)

    def __init__(self, allow_future: bool = False, parent=None):
        super().__init__(parent)
        self.allow_future = allow_future
        self._min = MIN_DATE
        self._max = QDate(2100, 12, 31)
        self._date = QDate.currentDate()

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("DD-MM-YYYY")
        self.edit.setMaxLength(10)
        self.edit.setMinimumWidth(100)
        self.edit.editingFinished.connect(self._commit)
        self.button = QToolButton()
        self.button.setText("\U0001F4C5")
        self.button.setToolTip("Open calendar")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._open_calendar)
        self.setFocusProxy(self.edit)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

        self._calendar: Optional[QCalendarWidget] = None
        self.set_today()

    # -- limits -------------------------------------------------------------
    def setMinimumDate(self, d: QDate) -> None:
        self._min = d

    def setMaximumDate(self, d: QDate) -> None:
        self._max = d
        if self._date > d:
            self.setDate(d)

    def setEnabled(self, on: bool) -> None:  # keep both child widgets in step
        super().setEnabled(on)

    # -- value --------------------------------------------------------------
    def date(self) -> QDate:
        self._commit()
        return self._date

    def setDate(self, d: QDate) -> None:
        if not d.isValid():
            return
        d = max(self._min, min(self._max, d))
        changed = d != self._date
        self._date = d
        self.edit.setText(d.toString(DISPLAY_FORMAT))
        if changed:
            self.dateChanged.emit(d)

    def _commit(self) -> None:
        """Read the typed text. A bad or out-of-range entry snaps back to the last good date."""
        q = parse_typed_date(self.edit.text())
        if q is None or q < self._min or q > self._max:
            self.edit.setText(self._date.toString(DISPLAY_FORMAT))
            return
        self.setDate(q)
        self.edit.setText(self._date.toString(DISPLAY_FORMAT))

    def _open_calendar(self) -> None:
        self._commit()
        cal = QCalendarWidget()
        cal.setWindowFlags(Qt.WindowType.Popup)
        cal.setGridVisible(True)
        cal.setMinimumDate(self._min)
        cal.setMaximumDate(self._max)
        cal.setSelectedDate(self._date)
        cal.clicked.connect(lambda d: (self.setDate(d), cal.close()))
        cal.activated.connect(lambda d: (self.setDate(d), cal.close()))
        self._calendar = cal  # keep a reference while it is open
        cal.move(self.mapToGlobal(self.rect().bottomLeft()))
        cal.show()

    # -- helpers used by the rest of the app ---------------------------------
    def set_today(self) -> None:
        today = QDate.currentDate()
        if not self.allow_future:
            self._max = today
        self.setDate(today)

    def refresh_limits(self) -> None:
        if not self.allow_future:
            self._max = QDate.currentDate()

    def text_dmy(self) -> str:
        return self.date().toString(DISPLAY_FORMAT)

    def set_iso(self, iso: str) -> None:
        self.setDate(QDate.fromString(iso, "yyyy-MM-dd"))


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.horizontalHeader().setHighlightSections(False)
    table.setWordWrap(False)
    return table


def set_cell(table: QTableWidget, row: int, col: int, text: str, right: bool = False,
             data=None, color: Optional[str] = None, bold: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    if color:
        item.setForeground(QBrush(QColor(color)))
    if bold:
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
    item.setToolTip(text)
    table.setItem(row, col, item)
    return item


def selected_id(table: QTableWidget, col: int = 0) -> Optional[int]:
    rows = table.selectionModel().selectedRows()
    if not rows:
        return None
    item = table.item(rows[0].row(), col)
    return None if item is None else item.data(Qt.ItemDataRole.UserRole)


def fit_columns(table: QTableWidget, stretch_col: int) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)


def populate_customer_combo(combo: QComboBox, customers, keep_id=None, include_all: bool = False) -> None:
    """Fill a combo with customers; item data = customer id (None for 'All customers')."""
    if keep_id is None and combo.currentIndex() >= 0:
        keep_id = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if include_all:
        combo.addItem("All customers", None)
    for s in customers:
        combo.addItem(s.customer.name, s.customer.id)
    index = combo.findData(keep_id) if keep_id is not None else -1
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.count():
        combo.setCurrentIndex(0)
    combo.blockSignals(False)


# --------------------------------------------------------------------------- layout helpers
def page_header(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    h1 = QLabel(title)
    h1.setObjectName("h1")
    layout.addWidget(h1)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        layout.addWidget(sub)
    return box


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("primary")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def plain_button(text: str, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if danger:
        b.setObjectName("danger")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Card(QFrame):
    """White rounded panel with an optional title."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        if title:
            label = QLabel(title)
            label.setObjectName("h2")
            self.body.addWidget(label)


class KpiCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)
        self.title = QLabel(title.upper())
        self.title.setObjectName("kpiTitle")
        self.value = QLabel("-")
        self.value.setObjectName("kpiValue")
        self.caption = QLabel("")
        self.caption.setObjectName("kpiCaption")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set(self, value: str, caption: str = "", kind: str = "") -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        self.value.setStyleSheet(
            f"color: {theme.current()[kind]};" if kind in ("danger", "success", "warning") else "")


# --------------------------------------------------------------------------- entry form
class EntryForm(QWidget):
    """Fields of one ledger entry. Used by Quick Entry and by the edit dialog."""

    def __init__(self, with_customer: bool, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        if with_customer:
            self.new_customer_button = QPushButton("+ New customer")
            row = QHBoxLayout()
            row.addWidget(self.customer, 1)
            row.addWidget(self.new_customer_button)
            form.addRow("Customer", row)

        self.udhaar = QRadioButton(f"{UDHAAR_LABEL}  \u2013 customer took on credit")
        self.jama = QRadioButton(f"{JAMA_LABEL}  \u2013 customer paid")
        self.udhaar.setChecked(True)
        type_row = QHBoxLayout()
        type_row.addWidget(self.udhaar)
        type_row.addWidget(self.jama)
        type_row.addStretch(1)
        form.addRow("Type", type_row)

        self.date = DateField()
        self.date.setToolTip("Type the date as DD-MM-YYYY (select all and retype works) or use the calendar button.")
        form.addRow("Date", self.date)

        self.item = QLineEdit()
        self.item.setPlaceholderText("e.g. Gold chain repair, Previous payment")
        self.item.setMaxLength(200)
        form.addRow("Item / description", self.item)

        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("optional")
        self.rate = QLineEdit()
        self.rate.setPlaceholderText("optional (₹)")
        qr = QHBoxLayout()
        qr.addWidget(QLabel("Qty"))
        qr.addWidget(self.quantity)
        qr.addWidget(QLabel("× Rate"))
        qr.addWidget(self.rate)
        form.addRow("Quantity × Rate", qr)

        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.amount_hint = QLabel("")
        self.amount_hint.setObjectName("muted")
        amount_row = QHBoxLayout()
        amount_row.addWidget(self.amount, 1)
        amount_row.addWidget(self.amount_hint)
        form.addRow("Amount (₹)", amount_row)

        self.has_due = QCheckBox("Promised payment date")
        self.due = DateField(allow_future=True)
        self.due.setEnabled(False)
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_due)
        due_row.addWidget(self.due)
        due_row.addStretch(1)
        form.addRow("Due date", due_row)

        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(60)
        form.addRow("Notes", self.notes)

        self.quantity.textChanged.connect(self._recalculate)
        self.rate.textChanged.connect(self._recalculate)
        self.has_due.toggled.connect(self._due_toggled)
        self.udhaar.toggled.connect(self._type_changed)

    # -- behaviour --------------------------------------------------------
    def _due_toggled(self, on: bool) -> None:
        self.due.setEnabled(on and self.udhaar.isChecked())
        if on:  # sensible default: 30 days after the transaction date (set_values overrides it afterwards)
            self.due.setDate(self.date.date().addDays(30))

    def _type_changed(self, _checked: bool) -> None:
        is_udhaar = self.udhaar.isChecked()
        self.has_due.setEnabled(is_udhaar)
        self.due.setEnabled(is_udhaar and self.has_due.isChecked())

    def _recalculate(self) -> None:
        q, r = self.quantity.text().strip(), self.rate.text().strip()
        if q and r:
            try:
                amount = money.compute_amount_paise(money.parse_quantity(q), money.rupees_to_paise(r, "Rate"))
            except money.MoneyError:
                self.amount.setReadOnly(False)
                self.amount_hint.setText("")
                return
            self.amount.setText(money.plain_amount(amount))
            self.amount.setReadOnly(True)
            self.amount_hint.setText("= Qty × Rate")
        else:
            self.amount.setReadOnly(False)
            self.amount_hint.setText("")

    def customer_id(self) -> int:
        text = self.customer.currentText().strip()
        if not text:
            raise KhataError("Select a customer")
        index = self.customer.findText(text, Qt.MatchFlag.MatchFixedString)  # case-insensitive exact match
        if index < 0:
            raise KhataError(f"No customer named '{text}'. Pick one from the list or use '+ New customer'.")
        return self.customer.itemData(index)

    def select_customer(self, customer_id: int) -> None:
        index = self.customer.findData(customer_id)
        if index >= 0:
            self.customer.setCurrentIndex(index)

    def values(self) -> dict:
        both = bool(self.quantity.text().strip() and self.rate.text().strip())
        is_udhaar = self.udhaar.isChecked()
        return {
            "txn_type": "UDHAAR" if is_udhaar else "JAMA",
            "txn_date": self.date.text_dmy(),
            "item": self.item.text(),
            "amount": None if both else self.amount.text(),
            "quantity": self.quantity.text(),
            "rate": self.rate.text(),
            "notes": self.notes.toPlainText(),
            "due_date": self.due.text_dmy() if (is_udhaar and self.has_due.isChecked()) else None,
        }

    def set_values(self, txn) -> None:
        (self.udhaar if txn.txn_type == "UDHAAR" else self.jama).setChecked(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.date.set_iso(txn.txn_date)
        self.item.setText(txn.item)
        self.quantity.setText(txn.quantity or "")
        self.rate.setText(money.plain_amount(txn.rate_paise) if txn.rate_paise else "")
        self.amount.setText(money.plain_amount(txn.amount_paise))
        self.notes.setPlainText(txn.notes)
        if txn.due_date:
            self.has_due.setChecked(True)
            self.due.set_iso(txn.due_date)
        else:
            self.has_due.setChecked(False)
        self._type_changed(True)
        self._recalculate()

    def clear_for_next(self) -> None:
        """Keep customer, type and date; clear the rest so the next entry can be typed straight away."""
        self.item.clear()
        self.quantity.clear()
        self.rate.clear()
        self.amount.clear()
        self.amount.setReadOnly(False)
        self.amount_hint.setText("")
        self.notes.clear()
        self.has_due.setChecked(False)
        self.item.setFocus()

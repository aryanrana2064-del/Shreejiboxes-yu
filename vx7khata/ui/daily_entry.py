"""Daily Entry page: type many entries in one grid; they wait 24 hours and are then saved into the ledgers.

Each row = one ledger entry: Type, Customer, Product, Date, Quantity, Amount (rate), Total (automatic), Notes.
Total = Quantity x Amount. Leave Quantity empty and Total is simply the Amount (use this for payments / lump sums).

Rows added here do NOT go into the customer's ledger straight away. They appear in the "Waiting" list below the grid
and are saved automatically 24 hours later. Until then a row can be deleted (e.g. the customer brought the goods
back) or saved early with "Save now".
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableWidget,
    QVBoxLayout, QWidget,
)

from .. import dates, money, viewmodel
from ..labels import JAMA_LABEL, QUICK_PAYMENT_ITEM, UDHAAR_LABEL, type_label
from ..service import PENDING_HOURS, CreditLimitError, DuplicateEntryError, KhataError, KhataService
from .widgets import DateField, fit_columns, make_table, plain_button, primary_button, set_cell

COL_TYPE, COL_CUSTOMER, COL_PRODUCT, COL_DATE, COL_QTY, COL_AMOUNT, COL_TOTAL, COL_NOTES, COL_REMOVE = range(9)
HEADERS = ["Type", "Customer", "Product", "Date", "Quantity", "Amount (per unit)", "Total (auto)", "Notes", ""]
START_ROWS = 3

PCOL_CUSTOMER, PCOL_TYPE, PCOL_PRODUCT, PCOL_DATE, PCOL_AMOUNT, PCOL_LEFT, PCOL_ACTIONS = range(7)
PENDING_HEADERS = ["Customer", "Type", "Product", "Date", "Amount", "Saves in", ""]


def row_total_paise(quantity_text: str, amount_text: str) -> int | None:
    """Automatic total of one row in paise, or None when the numbers are blank / not valid yet.

    Quantity and Amount both filled -> Quantity x Amount.  Only Amount filled -> Amount.
    """
    amount_text = (amount_text or "").strip()
    quantity_text = (quantity_text or "").strip()
    if not amount_text:
        return None
    try:
        amount = money.rupees_to_paise(amount_text)
        if not quantity_text:
            return amount
        return money.compute_amount_paise(money.parse_quantity(quantity_text), amount)
    except money.MoneyError:
        return None


class DailyEntryPage(QWidget):
    def __init__(self, service: KhataService, on_saved, info, warn, confirm, parent=None):
        super().__init__(parent)
        self.service = service
        self._on_saved = on_saved
        self._info = info
        self._warn = warn
        self._confirm = confirm
        self._customers: list = []
        self._busy = False

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(True)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for col, width in ((COL_TYPE, 130), (COL_CUSTOMER, 190), (COL_PRODUCT, 170), (COL_DATE, 150), (COL_QTY, 90),
                           (COL_AMOUNT, 120), (COL_TOTAL, 110), (COL_NOTES, 200), (COL_REMOVE, 44)):
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(COL_NOTES, QHeaderView.ResizeMode.Stretch)

        self.summary = QLabel("")
        self.summary.setObjectName("h2")
        add_row = plain_button("+ Add row")
        add_row.clicked.connect(lambda: self.add_row())
        clear = plain_button("Clear all")
        clear.clicked.connect(self.clear_all)
        self.save_button = primary_button("Add entries (saved in 24 hours)")
        self.save_button.clicked.connect(self.save_all)

        bar = QHBoxLayout()
        bar.addWidget(add_row)
        bar.addWidget(clear)
        bar.addWidget(self.summary, 1)
        bar.addWidget(self.save_button)

        hint = QLabel("Total is calculated automatically (Quantity x Amount). Leave Quantity empty to enter a single amount, "
                      f"e.g. a payment. Added rows wait in the list below and are saved into the customer's ledger "
                      f"automatically after {PENDING_HOURS} hours. Delete a row there if the customer returns the goods.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)

        self.pending_title = QLabel("")
        self.pending_title.setObjectName("h2")
        self.pending_table = make_table(PENDING_HEADERS)
        self.pending_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pending_table.verticalHeader().setDefaultSectionSize(42)
        fit_columns(self.pending_table, PCOL_PRODUCT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.table, 3)
        layout.addLayout(bar)
        layout.addWidget(hint)
        layout.addWidget(self.pending_title)
        layout.addWidget(self.pending_table, 2)

        for _ in range(START_ROWS):
            self.add_row()
        self.refresh_pending()

    # ------------------------------------------------------------ customers
    def set_customers(self, summaries) -> None:
        """Refresh the customer lists in every row (keeps what was already chosen)."""
        self._customers = list(summaries)
        for row in range(self.table.rowCount()):
            self._fill_customer_combo(self.table.cellWidget(row, COL_CUSTOMER))

    def _fill_customer_combo(self, combo: QComboBox, keep_id=None) -> None:
        if keep_id is None and combo.currentText().strip():
            keep_id = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for s in self._customers:
            combo.addItem(s.customer.name, s.customer.id)
        index = combo.findData(keep_id) if keep_id is not None else -1
        combo.setCurrentIndex(index)           # -1 = nothing chosen yet
        if index < 0:
            combo.setEditText("")
        combo.blockSignals(False)

    # ------------------------------------------------------------ rows
    def add_row(self, copy_from: int | None = None) -> int:
        row = self.table.rowCount()
        if copy_from is None and row > 0:
            copy_from = row - 1
        self.table.insertRow(row)

        type_box = QComboBox()
        type_box.addItem(UDHAAR_LABEL, "UDHAAR")
        type_box.addItem(JAMA_LABEL, "JAMA")

        customer = QComboBox()
        customer.setEditable(True)
        customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        customer.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._fill_customer_combo(customer)

        product = QLineEdit()
        product.setPlaceholderText("Product / item")
        product.setMaxLength(120)
        date = DateField()
        qty = QLineEdit()
        qty.setPlaceholderText("Qty")
        amount = QLineEdit()
        amount.setPlaceholderText("0.00")
        total = QLineEdit()
        total.setReadOnly(True)
        total.setPlaceholderText("auto")
        total.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        notes = QLineEdit()
        notes.setPlaceholderText("Notes (optional)")
        notes.setMaxLength(200)
        remove = plain_button("\u2715", danger=True)
        remove.setToolTip("Remove this row")

        if copy_from is not None and copy_from < row:
            previous_type = self.table.cellWidget(copy_from, COL_TYPE)
            previous_customer = self.table.cellWidget(copy_from, COL_CUSTOMER)
            previous_date = self.table.cellWidget(copy_from, COL_DATE)
            type_box.setCurrentIndex(previous_type.currentIndex())
            if previous_customer.currentText().strip():
                self._fill_customer_combo(customer, keep_id=previous_customer.currentData())
            date.setDate(previous_date.date())

        for col, widget in ((COL_TYPE, type_box), (COL_CUSTOMER, customer), (COL_PRODUCT, product), (COL_DATE, date),
                            (COL_QTY, qty), (COL_AMOUNT, amount), (COL_TOTAL, total), (COL_NOTES, notes),
                            (COL_REMOVE, remove)):
            self.table.setCellWidget(row, col, widget)

        qty.textChanged.connect(lambda _t, w=total, q=qty, a=amount: self._update_total(w, q, a))
        amount.textChanged.connect(lambda _t, w=total, q=qty, a=amount: self._update_total(w, q, a))
        remove.clicked.connect(lambda _c=False, w=remove: self._remove_row_of(w))
        notes.returnPressed.connect(lambda w=notes: self._enter_in_notes(w))
        type_box.currentIndexChanged.connect(lambda _i: self._update_summary())
        self._update_summary()
        return row

    def _row_of(self, widget: QWidget, col: int) -> int:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, col) is widget:
                return row
        return -1

    def _remove_row_of(self, button: QWidget) -> None:
        row = self._row_of(button, COL_REMOVE)
        if row >= 0:
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self.add_row()
        self._update_summary()

    def _enter_in_notes(self, notes: QWidget) -> None:
        row = self._row_of(notes, COL_NOTES)
        if row == self.table.rowCount() - 1:
            new_row = self.add_row()
            self.table.cellWidget(new_row, COL_PRODUCT).setFocus()
        elif row >= 0:
            self.table.cellWidget(row + 1, COL_PRODUCT).setFocus()

    def clear_all(self) -> None:
        if self._has_data() and not self._confirm("Clear all", "Remove everything typed on this page?"):
            return
        self.table.setRowCount(0)
        for _ in range(START_ROWS):
            self.add_row()

    def _has_data(self) -> bool:
        return any(not self._row_is_empty(r) for r in range(self.table.rowCount()))

    def _row_is_empty(self, row: int) -> bool:
        return not any(self.table.cellWidget(row, c).text().strip()
                       for c in (COL_PRODUCT, COL_QTY, COL_AMOUNT, COL_NOTES))

    # ------------------------------------------------------------ totals
    def _update_total(self, total_widget: QLineEdit, qty: QLineEdit, amount: QLineEdit) -> None:
        paise = row_total_paise(qty.text(), amount.text())
        total_widget.setText("" if paise is None else money.format_inr(paise))
        self._update_summary()

    def _update_summary(self) -> None:
        count = 0
        udhaar = jama = 0
        for row in range(self.table.rowCount()):
            if self._row_is_empty(row):
                continue
            count += 1
            paise = row_total_paise(self.table.cellWidget(row, COL_QTY).text(),
                                    self.table.cellWidget(row, COL_AMOUNT).text())
            if paise is None:
                continue
            if self.table.cellWidget(row, COL_TYPE).currentData() == "UDHAAR":
                udhaar += paise
            else:
                jama += paise
        self.summary.setText(f"{count} entries   |   {UDHAAR_LABEL} {money.format_inr(udhaar)}   "
                             f"{JAMA_LABEL} {money.format_inr(jama)}")

    # ------------------------------------------------------------ saving
    def _customer_id(self, combo: QComboBox) -> int:
        text = combo.currentText().strip()
        if not text:
            raise KhataError("Select a customer")
        index = combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            raise KhataError(f"No customer named '{text}'. Pick one from the list.")
        return combo.itemData(index)

    def _row_values(self, row: int) -> dict:
        cell = lambda col: self.table.cellWidget(row, col)           # noqa: E731
        txn_type = cell(COL_TYPE).currentData()
        qty = cell(COL_QTY).text().strip()
        amount = cell(COL_AMOUNT).text().strip()
        product = cell(COL_PRODUCT).text().strip()
        if not product and txn_type == "JAMA":
            product = QUICK_PAYMENT_ITEM
        both = bool(qty and amount)
        return {
            "customer_id": self._customer_id(cell(COL_CUSTOMER)),
            "txn_type": txn_type,
            "txn_date": cell(COL_DATE).text_dmy(),
            "item": product,
            "quantity": qty,
            "rate": amount if both else "",
            "amount": None if both else amount,
            "notes": cell(COL_NOTES).text(),
        }

    def _save_row(self, row: int) -> bool:
        """Queue one row (it is saved into the ledger after 24 hours). Returns False when the person chose not to add
        it (duplicate / over limit)."""
        values = self._row_values(row)
        if values["quantity"] and not values["rate"]:
            raise KhataError("Enter the Amount (per unit) as well, or clear the Quantity")
        allow_duplicate = False
        allow_over_limit = False
        while True:
            try:
                self.service.add_pending_entry(allow_duplicate=allow_duplicate, allow_over_limit=allow_over_limit,
                                               **values)
                return True
            except DuplicateEntryError as exc:
                if allow_duplicate or not self._confirm("Possible duplicate", f"Row {row + 1}: {exc}\n\nAdd it again anyway?"):
                    return False
                allow_duplicate = True
            except CreditLimitError as exc:
                if allow_over_limit or not self._confirm("Credit limit exceeded", f"Row {row + 1}: {exc}\n\nAdd this entry anyway?"):
                    return False
                allow_over_limit = True

    def save_all(self) -> None:
        if self._busy:
            return
        rows = [r for r in range(self.table.rowCount()) if not self._row_is_empty(r)]
        if not rows:
            self._info("Nothing to save", "Type at least one entry first.")
            return
        self._busy = True
        self.save_button.setEnabled(False)
        saved_rows: list[int] = []
        skipped = 0
        error: str | None = None
        try:
            for row in rows:
                try:
                    if self._save_row(row):
                        saved_rows.append(row)
                    else:
                        skipped += 1
                except KhataError as exc:
                    error = f"Row {row + 1}: {exc}"
                    break
            self._remove_saved(saved_rows)
            if error:
                self._warn("Cannot add", f"{error}\n\n{len(saved_rows)} entries before it were added. "
                                         "Fix this row and press the button again.")
            else:
                message = (f"{len(saved_rows)} entries added. They will be saved into the customers' ledgers "
                           f"automatically after {PENDING_HOURS} hours.\nUntil then you can delete them from the "
                           "list below.")
                if skipped:
                    message += f"\n{skipped} row(s) were not added and are still on the page."
                self._info("Added", message)
        finally:
            self._busy = False
            self.save_button.setEnabled(True)

    def _remove_saved(self, saved_rows: list[int]) -> None:
        """Take the rows that were added to the waiting list off the grid; skipped or failed rows stay for correction."""
        if not saved_rows:
            return
        for row in sorted(saved_rows, reverse=True):
            self.table.removeRow(row)
        if self.table.rowCount() == 0:
            for _ in range(START_ROWS):
                self.add_row()
        self._update_summary()
        self._on_saved()

    # ------------------------------------------------------------ waiting list (24 hours)
    def refresh_pending(self) -> None:
        """Reload the list of rows that are waiting to be saved (also updates the 'Saves in' countdown)."""
        try:
            pending = self.service.list_pending()
        except KhataError:
            pending = []
        table = self.pending_table
        table.setRowCount(0)
        for p in pending:
            row = table.rowCount()
            table.insertRow(row)
            set_cell(table, row, PCOL_CUSTOMER, p.customer_name)
            set_cell(table, row, PCOL_TYPE, type_label(p.txn_type))
            set_cell(table, row, PCOL_PRODUCT, p.item)
            set_cell(table, row, PCOL_DATE, dates.format_date(p.txn_date))
            set_cell(table, row, PCOL_AMOUNT, money.format_inr(p.amount_paise), right=True)
            set_cell(table, row, PCOL_LEFT, viewmodel.time_left_text(self.service.seconds_until_post(p)))

            box = QWidget()
            box_layout = QHBoxLayout(box)
            box_layout.setContentsMargins(4, 2, 4, 2)
            box_layout.setSpacing(6)
            save_now = plain_button("Save now")
            save_now.setToolTip("Put this entry into the customer's ledger right away")
            save_now.clicked.connect(lambda _c=False, pid=p.id: self._save_pending_now(pid))
            delete = plain_button("Delete", danger=True)
            delete.setToolTip("Remove this entry. It will not be saved in the ledger (e.g. goods returned)")
            delete.clicked.connect(lambda _c=False, pid=p.id: self._delete_pending(pid))
            box_layout.addWidget(save_now)
            box_layout.addWidget(delete)
            table.setCellWidget(row, PCOL_ACTIONS, box)
        if pending:
            self.pending_title.setText(f"Waiting to be saved ({len(pending)}) – saved into the ledger automatically "
                                       f"after {PENDING_HOURS} hours")
        else:
            self.pending_title.setText("Waiting to be saved – nothing right now")

    def _describe(self, p) -> str:
        return (f"{p.customer_name}\n{type_label(p.txn_type)}  {money.format_inr(p.amount_paise)}  –  {p.item}\n"
                f"Date {dates.format_date(p.txn_date)}")

    def _delete_pending(self, pending_id: int) -> None:
        try:
            p = self.service.get_pending_entry(pending_id)
        except KhataError as exc:
            self._warn("Cannot delete", str(exc))
            self.refresh_pending()
            return
        if not self._confirm("Delete entry", f"Delete this entry?\n\n{self._describe(p)}\n\n"
                                              "It will NOT be saved in the customer's ledger."):
            return
        try:
            self.service.delete_pending(pending_id)
        except KhataError as exc:
            self._warn("Cannot delete", str(exc))
        self.refresh_pending()

    def _save_pending_now(self, pending_id: int) -> None:
        try:
            p = self.service.get_pending_entry(pending_id)
        except KhataError as exc:
            self._warn("Cannot save", str(exc))
            self.refresh_pending()
            return
        if not self._confirm("Save now", f"Put this entry into the customer's ledger now?\n\n{self._describe(p)}"):
            return
        try:
            self.service.post_pending(pending_id)
        except KhataError as exc:
            self._warn("Cannot save", str(exc))
            self.refresh_pending()
            return
        self._on_saved()  # refreshes the whole window, including this list

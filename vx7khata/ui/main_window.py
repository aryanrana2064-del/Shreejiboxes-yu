"""Main window: dark sidebar + pages (Dashboard, Customers, New Entry, Daily Entry, Ledger, Recovery, Reports, Activity, Settings)."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, backup, dates, exports, insights, money, security, viewmodel
from ..service import CreditLimitError, DuplicateEntryError, KhataError, KhataService
from ..labels import JAMA_LABEL, JAMA_WORD, UDHAAR_LABEL, UDHAAR_WORD, type_label
from . import theme
from .charts import MonthlyChart
from .daily_entry import DailyEntryPage
from .dialogs import AuditDetailDialog, CustomerDialog, EditEntryDialog, PinSetupDialog, LockDialog, QuickPaymentDialog, ReminderDialog
from .widgets import (
    Card, DateField, EntryForm, KpiCard, balance_color, fit_columns, make_table, page_header, plain_button,
    populate_customer_combo, primary_button, selected_id, set_cell,
)

PAGES = (
    ("dashboard", "Dashboard"),
    ("customers", "Customers"),
    ("entry", "New Entry"),
    ("daily", "Daily Entry"),
    ("ledger", "Ledger"),
    ("recovery", "Recovery"),
    ("reports", "Reports"),
    ("activity", "Activity"),
    ("settings", "Settings"),
)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_") or "khata"


def _documents_dir() -> str:
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())


def _set_state(widget: QWidget, state: str) -> None:
    """Switch the 'state' style property (used by the credit progress bar) and re-apply the stylesheet."""
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class MainWindow(QMainWindow):
    def __init__(self, service: KhataService):
        super().__init__()
        self.svc = service
        self.guard = security.PinGuard(service)
        self.setWindowTitle(f"{APP_NAME}  –  Customer Ledger")
        self.resize(1340, 840)
        self.setMinimumSize(1120, 700)
        self._saving = False
        self._locked = False
        self.current_key = "dashboard"
        self._audit_cache: dict[int, object] = {}

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.page_index: dict[str, int] = {}
        builders = {
            "dashboard": self._build_dashboard, "customers": self._build_customers, "entry": self._build_entry, "daily": self._build_daily,
            "ledger": self._build_ledger, "recovery": self._build_recovery, "reports": self._build_reports,
            "activity": self._build_activity, "settings": self._build_settings,
        }
        for key, _label in PAGES:
            self.page_index[key] = self.stack.addWidget(builders[key]())

        for number, (key, _label) in enumerate(PAGES, start=1):
            QShortcut(QKeySequence(f"Ctrl+{number}"), self).activated.connect(lambda k=key: self.show_page(k))
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new_entry_shortcut)
        QShortcut(QKeySequence("Ctrl+J"), self).activated.connect(lambda: self._quick_payment())
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.lock_now)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh_all)

        self._update_theme_button()
        self.refresh_all()
        self.show_page("dashboard")

        # Daily Entry rows are saved into the ledgers 24 hours after they were added; check once a minute.
        self._pending_timer = QTimer(self)
        self._pending_timer.setInterval(60_000)
        self._pending_timer.timeout.connect(self._auto_post_pending)
        self._pending_timer.start()

    # ------------------------------------------------------------ shared helpers
    def info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def confirm(self, title: str, text: str, default_no: bool = True) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes)
        return box.exec() == QMessageBox.StandardButton.Yes

    def shop_name(self) -> str:
        return self.svc.get_setting("shop_name", "")

    def shop_details(self) -> str:
        return self.svc.get_setting("shop_details", "")

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        return page, layout

    def _header(self, layout: QVBoxLayout, title: str, subtitle: str = "", *actions: QWidget) -> None:
        row = QHBoxLayout()
        row.addWidget(page_header(title, subtitle), 1)
        for widget in actions:
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)

    def _export_path(self, title: str, default_name: str, pattern: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path(_documents_dir()) / default_name), pattern)
        return path or None

    def _offer_open(self, path) -> None:
        if self.confirm("Export complete", f"Saved:\n{path}\n\nOpen it now?", default_no=False):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _run_export(self, title: str, default_name: str, kind: str, writer) -> None:
        """Ask for a file name, call ``writer(path)``, report errors, offer to open the result."""
        pattern = "PDF files (*.pdf)" if kind == "pdf" else "Excel files (*.xlsx)"
        path = self._export_path(title, default_name, pattern)
        if not path:
            return
        try:
            writer(path)
        except (OSError, KhataError) as exc:
            self.warn("Export failed", str(exc))
            return
        self._offer_open(path)

    # ------------------------------------------------------------ navigation / refresh
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(224)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 22, 14, 16)
        layout.setSpacing(4)
        brand = QLabel("VX7 KHATA PRO")
        brand.setObjectName("brand")
        sub = QLabel("Customer Ledger Manager")
        sub.setObjectName("brandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in PAGES:
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)

        self.theme_button = QPushButton()
        self.theme_button.setObjectName("sideTool")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        self.lock_button = QPushButton("Lock  (Ctrl+L)")
        self.lock_button.setObjectName("sideTool")
        self.lock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_button.clicked.connect(self.lock_now)
        note = QLabel(f"Works fully offline.\nData stays on this PC.\nv{__version__}")
        note.setObjectName("sideNote")
        layout.addWidget(self.theme_button)
        layout.addWidget(self.lock_button)
        layout.addSpacing(6)
        layout.addWidget(note)
        return side

    def show_page(self, key: str) -> None:
        self.current_key = key
        self.stack.setCurrentIndex(self.page_index[key])
        self.nav_buttons[key].setChecked(True)
        self._refresh_page(key)

    def _new_entry_shortcut(self) -> None:
        self.show_page("entry")
        self.entry_form.item.setFocus()

    def refresh_all(self) -> None:
        summaries = self.svc.list_customers()
        populate_customer_combo(self.entry_form.customer, summaries)
        populate_customer_combo(self.ledger_customer, summaries)
        populate_customer_combo(self.report_customer, summaries, include_all=True)
        self.daily_page.set_customers(summaries)
        self.daily_page.refresh_pending()
        for field in (self.entry_form.date, self.ledger_from, self.ledger_to, self.rep_from, self.rep_to):
            field.refresh_limits()
        self._refresh_dashboard()
        self._refresh_customers(summaries)
        self._update_entry_context()
        self._refresh_ledger()
        self._refresh_page(self.current_key)
        self.statusBar().showMessage(f"{len(summaries)} customers  |  data file: {self.svc.db_path}")

    def _refresh_page(self, key: str) -> None:
        if key == "recovery":
            self._refresh_recovery()
        elif key == "activity":
            self._refresh_activity()
        elif key == "settings":
            self._refresh_settings()

    # ------------------------------------------------------------ dashboard
    def _build_dashboard(self) -> QWidget:
        page, layout = self._page()
        quick = primary_button("Quick payment  (Ctrl+J)")
        quick.clicked.connect(lambda: self._quick_payment())
        self._header(layout, "Dashboard", "Everything owed and paid across all customers", quick)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {
            "customers": KpiCard("Total customers"),
            "udhaar": KpiCard(f"Total {UDHAAR_WORD}"),
            "jama": KpiCard(f"Total {JAMA_WORD}"),
            "net": KpiCard("Net outstanding"),
            "today": KpiCard("Today's transactions"),
            "historical": KpiCard("Historical transactions"),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 3, i % 3)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        chart_card = Card(f"{UDHAAR_LABEL} vs {JAMA_WORD} – last 12 months")
        self.chart = MonthlyChart()
        chart_card.body.addWidget(self.chart, 1)
        middle.addWidget(chart_card, 3)

        side = QVBoxLayout()
        side.setSpacing(14)
        debtors = Card("Top debtors")
        self.debtor_table = make_table(["Customer", "Balance"])
        fit_columns(self.debtor_table, 0)
        self.debtor_table.setMinimumHeight(170)
        debtors.body.addWidget(self.debtor_table, 1)
        attention = Card("Needs attention")
        self.attention_label = QLabel("")
        self.attention_label.setWordWrap(True)
        attention.body.addWidget(self.attention_label)
        open_recovery = plain_button("Open Recovery")
        open_recovery.clicked.connect(lambda: self.show_page("recovery"))
        attention.body.addWidget(open_recovery, 0, Qt.AlignmentFlag.AlignLeft)
        side.addWidget(debtors, 3)
        side.addWidget(attention, 2)
        middle.addLayout(side, 2)
        layout.addLayout(middle, 1)
        return page

    def _refresh_dashboard(self) -> None:
        d = self.svc.dashboard()
        self.cards["customers"].set(str(d.total_customers), f"{d.total_transactions} entries in total")
        self.cards["udhaar"].set(money.format_inr(d.total_udhaar), "given on credit, all time")
        self.cards["jama"].set(money.format_inr(d.total_jama), "received, all time")
        net = d.net_outstanding
        self.cards["net"].set(money.format_inr(net),
                              "customers owe you" if net > 0 else ("advances held" if net < 0 else "all settled"),
                              "danger" if net > 0 else ("success" if net < 0 else ""))
        self.cards["today"].set(str(d.todays_transactions), "entries dated today")
        self.cards["historical"].set(str(d.historical_transactions), "entries dated before today")

        self.chart.set_data(insights.monthly_summary(self.svc, 12))

        debtors = insights.top_debtors(self.svc, 6)
        self.debtor_table.setRowCount(len(debtors))
        for row, s in enumerate(debtors):
            set_cell(self.debtor_table, row, 0, s.customer.name, data=s.customer.id)
            set_cell(self.debtor_table, row, 1, money.format_inr(s.balance), right=True,
                     color=balance_color(s.balance), bold=True)

        overdue = insights.overdue_items(self.svc)
        due_soon = insights.due_soon_items(self.svc, 7)
        over_limit = insights.over_limit_customers(self.svc)
        lines = []
        if overdue:
            lines.append(f"• {len(overdue)} overdue payment{'s' if len(overdue) != 1 else ''}, "
                         f"{money.format_inr(sum(r.remaining for r in overdue))} unpaid")
        if due_soon:
            lines.append(f"• {len(due_soon)} due within 7 days, {money.format_inr(sum(r.remaining for r in due_soon))}")
        if over_limit:
            lines.append(f"• {len(over_limit)} customer{'s' if len(over_limit) != 1 else ''} over credit limit")
        self.attention_label.setText("\n".join(lines) if lines else "Nothing needs attention right now.")

    # ------------------------------------------------------------ customers
    def _build_customers(self) -> QWidget:
        page, layout = self._page()
        add = primary_button("+ Add customer")
        add.clicked.connect(self._add_customer)
        self._header(layout, "Customers", "Each customer has a separate ledger", add)

        top = QHBoxLayout()
        self.cust_search = QLineEdit()
        self.cust_search.setPlaceholderText("Search by name or phone…")
        self.cust_search.setClearButtonEnabled(True)
        self.cust_search.textChanged.connect(lambda _t: self._refresh_customers())
        top.addWidget(self.cust_search, 1)
        for text, handler, danger in (("Quick payment", self._quick_payment_for_selected, False),
                                      ("Edit", self._edit_customer, False), ("Open ledger", self._open_customer_ledger, False),
                                      ("Add entry", self._entry_for_customer, False),
                                      ("Reminder", self._customer_reminder, False),
                                      ("STATEMENT_MENU", None, False),
                                      ("Delete", self._delete_customer, True)):
            if text == "STATEMENT_MENU":
                for statement_button in self._statement_buttons(lambda: selected_id(self.cust_table)):
                    top.addWidget(statement_button)
                continue
            button = plain_button(text, danger=danger)
            button.clicked.connect(handler)
            top.addWidget(button)
        layout.addLayout(top)

        self.cust_table = make_table(["Name", "Mobile", "Address", f"Total {UDHAAR_LABEL}", f"Total {JAMA_LABEL}", "Balance", "Credit limit"])
        fit_columns(self.cust_table, 2)
        self.cust_table.doubleClicked.connect(lambda _i: self._open_customer_ledger())
        layout.addWidget(self.cust_table, 1)
        self.cust_total = QLabel("")
        self.cust_total.setObjectName("h2")
        layout.addWidget(self.cust_total)
        return page

    def _refresh_customers(self, summaries=None) -> None:
        all_summaries = summaries if summaries is not None else self.svc.list_customers()
        needle = self.cust_search.text()
        shown = self.svc.list_customers(needle) if needle.strip() else all_summaries
        keep = selected_id(self.cust_table)
        warn_color = theme.current()["warning"]
        self.cust_table.setRowCount(len(shown))
        for r, s in enumerate(shown):
            c = s.customer
            state, _fraction = viewmodel.credit_state(s)
            set_cell(self.cust_table, r, 0, c.name, data=c.id, bold=True)
            set_cell(self.cust_table, r, 1, c.mobile)
            set_cell(self.cust_table, r, 2, c.address.replace("\n", " "))
            set_cell(self.cust_table, r, 3, money.format_inr(s.total_udhaar), right=True)
            set_cell(self.cust_table, r, 4, money.format_inr(s.total_jama), right=True)
            set_cell(self.cust_table, r, 5, money.format_inr(s.balance), right=True,
                     color=balance_color(s.balance), bold=True)
            set_cell(self.cust_table, r, 6, viewmodel.credit_text(s),
                     color={"over": theme.current()["danger"], "warn": warn_color}.get(state))
            if keep == c.id:
                self.cust_table.selectRow(r)
        tu = sum(s.total_udhaar for s in all_summaries)
        tj = sum(s.total_jama for s in all_summaries)
        self.cust_total.setText(
            f"Overall ({len(all_summaries)} customers):   {UDHAAR_LABEL} {money.format_inr(tu)}   "
            f"{JAMA_LABEL} {money.format_inr(tj)}   Balance {money.format_inr(tu - tj)}"
        )

    def _current_customer_id(self) -> int | None:
        cid = selected_id(self.cust_table)
        if cid is None:
            self.info("Select a customer", "Click a customer row first.")
        return cid

    def _add_customer(self) -> None:
        dialog = CustomerDialog(self.svc, parent=self)
        if dialog.exec():
            self.refresh_all()

    def _edit_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        dialog = CustomerDialog(self.svc, self.svc.get_customer(cid), parent=self)
        if dialog.exec():
            self.refresh_all()

    def _delete_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        customer = self.svc.get_customer(cid)
        if not self.confirm("Delete customer", f"Delete customer '{customer.name}'?\n"
                            "Only customers with no ledger entries can be deleted."):
            return
        try:
            self.svc.delete_customer(cid)
        except KhataError as exc:
            self.warn("Cannot delete", str(exc))
            return
        self.refresh_all()

    def _open_customer_ledger(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        self.ledger_customer.setCurrentIndex(max(0, self.ledger_customer.findData(cid)))
        self._refresh_ledger()
        self.show_page("ledger")

    def _entry_for_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        self.entry_form.select_customer(cid)
        self.show_page("entry")
        self.entry_form.item.setFocus()

    def _customer_reminder(self) -> None:
        cid = self._current_customer_id()
        if cid is not None:
            self._show_reminder(cid)

    def _show_reminder(self, customer_id: int) -> None:
        try:
            dialog = ReminderDialog(self.svc, customer_id, parent=self)
        except KhataError as exc:  # e.g. the customer owes nothing
            self.info("No reminder needed", str(exc))
            return
        dialog.exec()

    # ------------------------------------------------------------ daily entry
    def _build_daily(self) -> QWidget:
        page, layout = self._page()
        self._header(layout, "Daily Entry", "Enter many entries for different customers in one go – "
                     "each row is saved into that customer's ledger automatically after 24 hours (delete it before then "
                     "if the goods come back)")
        self.daily_page = DailyEntryPage(self.svc, on_saved=self.refresh_all, info=self.info, warn=self.warn,
                                         confirm=self.confirm)
        layout.addWidget(self.daily_page, 1)
        return page

    # ------------------------------------------------------------ new entry
    def _build_entry(self) -> QWidget:
        page, layout = self._page()
        quick = primary_button("Quick payment  (Ctrl+J)")
        quick.clicked.connect(lambda: self._quick_payment())
        statement = self._statement_buttons(self._entry_customer_id_or_none)
        self._header(layout, "New Entry", f"Record {UDHAAR_WORD} or {JAMA_WORD} – any date, including old dates. "
                     "Only a payment? Use Quick payment – just the amount.", *statement, quick)
        card = Card()
        self.entry_form = EntryForm(with_customer=True)
        self.entry_form.new_customer_button.clicked.connect(self._quick_new_customer)
        self.entry_form.item.returnPressed.connect(self._save_entry)
        self.entry_form.amount.returnPressed.connect(self._save_entry)
        self.entry_form.customer.currentIndexChanged.connect(self._update_entry_context)
        self.entry_form.customer.editTextChanged.connect(self._update_entry_context)
        card.body.addWidget(self.entry_form)
        self.entry_context = QLabel("")
        self.entry_context.setObjectName("muted")
        card.body.addWidget(self.entry_context)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        clear = plain_button("Clear")
        clear.clicked.connect(self.entry_form.clear_for_next)
        self.entry_save = primary_button("Save entry")
        self.entry_save.clicked.connect(self._save_entry)
        buttons.addWidget(clear)
        buttons.addWidget(self.entry_save)
        card.body.addLayout(buttons)
        layout.addWidget(card)

        hint = QLabel("After saving, customer, type and date stay selected so the next item can be typed straight away.")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        recent = Card("Saved in this session")
        self.recent_table = make_table(["Customer", "Date", "Item / Description", "Type", "Amount", "Saved at"])
        fit_columns(self.recent_table, 2)
        recent.body.addWidget(self.recent_table, 1)
        layout.addWidget(recent, 1)
        return page

    # ------------------------------------------------------------ quick payment
    def _quick_payment(self, customer_id: int | None = None) -> None:
        """Open the amount-only payment dialog (no item needed)."""
        if not self.svc.list_customers():
            self.info("No customers", "Add a customer first.")
            return
        dialog = QuickPaymentDialog(self.svc, self._add_with_prompts, customer_id=customer_id, parent=self)
        if dialog.exec() and dialog.saved:
            txn = dialog.saved
            customer = self.svc.get_customer(txn.customer_id)
            balance = self.svc.customer_balance(txn.customer_id)
            self.refresh_all()
            self.statusBar().showMessage(
                f"{money.format_inr(txn.amount_paise)} received from {customer.name}. "
                f"New balance: {money.format_inr(balance)}", 8000)

    def _quick_payment_for_selected(self) -> None:
        self._quick_payment(selected_id(self.cust_table))

    def _ledger_quick_payment(self) -> None:
        self._quick_payment(self.ledger_customer.currentData())

    def _update_entry_context(self, *_args) -> None:
        try:
            cid = self.entry_form.customer_id()
            customer = self.svc.get_customer(cid)
        except KhataError:
            self.entry_context.setText("")
            return
        balance = self.svc.customer_balance(cid)
        text = f"Current balance: {money.format_inr(balance)}"
        if customer.credit_limit_paise:
            used = round(max(0, balance) * 100 / customer.credit_limit_paise)
            text += f"   |   Credit limit {money.format_inr(customer.credit_limit_paise)} ({used}% used)"
        self.entry_context.setText(text)

    def _quick_new_customer(self) -> None:
        dialog = CustomerDialog(self.svc, parent=self)
        if dialog.exec() and dialog.saved:
            self.refresh_all()
            self.entry_form.select_customer(dialog.saved.id)

    def _add_with_prompts(self, customer_id: int, values: dict):
        """Save an entry; ask before overriding the duplicate guard or the customer's credit limit."""
        allow_duplicate = False
        allow_over_limit = False
        while True:
            try:
                return self.svc.add_transaction(customer_id, allow_duplicate=allow_duplicate,
                                                allow_over_limit=allow_over_limit, **values)
            except DuplicateEntryError as exc:
                if allow_duplicate or not self.confirm("Possible duplicate", f"{exc}\n\nSave it again anyway?"):
                    return None
                allow_duplicate = True
            except CreditLimitError as exc:
                if allow_over_limit or not self.confirm("Credit limit exceeded", f"{exc}\n\nSave this entry anyway?"):
                    return None
                allow_over_limit = True

    def _save_entry(self) -> None:
        if self._saving:
            return
        self._saving = True
        self.entry_save.setEnabled(False)
        try:
            try:
                cid = self.entry_form.customer_id()
                txn = self._add_with_prompts(cid, self.entry_form.values())
            except KhataError as exc:
                self.warn("Cannot save entry", str(exc))
                return
            if txn is None:
                return
            customer = self.svc.get_customer(cid)
            self.recent_table.insertRow(0)
            set_cell(self.recent_table, 0, 0, customer.name)
            set_cell(self.recent_table, 0, 1, dates.format_date(txn.txn_date))
            set_cell(self.recent_table, 0, 2, txn.item)
            set_cell(self.recent_table, 0, 3, type_label(txn.txn_type))
            set_cell(self.recent_table, 0, 4, money.format_inr(txn.amount_paise), right=True,
                     color=balance_color(txn.amount_paise if txn.txn_type == "UDHAAR" else -txn.amount_paise))
            set_cell(self.recent_table, 0, 5, datetime.fromisoformat(txn.created_at).strftime("%H:%M:%S"))
            self.entry_form.clear_for_next()
            self.refresh_all()
            self.statusBar().showMessage("Entry saved", 4000)
        finally:
            self._saving = False
            self.entry_save.setEnabled(True)

    # ------------------------------------------------------------ ledger
    def _build_ledger(self) -> QWidget:
        page, layout = self._page()
        print_btn = plain_button("Print")
        pdf = plain_button("PDF")
        xlsx = plain_button("Excel")
        print_btn.clicked.connect(lambda: self._export_statement("print"))
        pdf.clicked.connect(lambda: self._export_statement("pdf"))
        xlsx.clicked.connect(lambda: self._export_statement("xlsx"))
        self._header(layout, "Ledger", "Chronological ledger with running balance", print_btn, pdf, xlsx)

        filters = QHBoxLayout()
        self.ledger_customer = QComboBox()
        self.ledger_customer.setMinimumWidth(240)
        self.ledger_range = QCheckBox("Date range")
        self.ledger_from = DateField()
        self.ledger_to = DateField()
        self.ledger_from.setEnabled(False)
        self.ledger_to.setEnabled(False)
        self.ledger_search = QLineEdit()
        self.ledger_search.setPlaceholderText("Search item description…")
        self.ledger_search.setClearButtonEnabled(True)
        filters.addWidget(QLabel("Customer"))
        filters.addWidget(self.ledger_customer)
        filters.addWidget(self.ledger_range)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.ledger_from)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self.ledger_to)
        filters.addWidget(self.ledger_search, 1)
        layout.addLayout(filters)
        self.ledger_customer.currentIndexChanged.connect(lambda _i: self._refresh_ledger())
        self.ledger_range.toggled.connect(self._ledger_range_toggled)
        self.ledger_from.dateChanged.connect(lambda _d: self._refresh_ledger())
        self.ledger_to.dateChanged.connect(lambda _d: self._refresh_ledger())
        self.ledger_search.textChanged.connect(lambda _t: self._refresh_ledger())

        credit_row = QHBoxLayout()
        self.ledger_info = QLabel("")
        self.ledger_info.setObjectName("h2")
        self.ledger_credit_label = QLabel("")
        self.ledger_credit_label.setObjectName("muted")
        self.ledger_credit_bar = QProgressBar()
        self.ledger_credit_bar.setRange(0, 100)
        self.ledger_credit_bar.setFixedWidth(180)
        self.ledger_credit_bar.setTextVisible(False)
        credit_row.addWidget(self.ledger_info, 1)
        credit_row.addWidget(self.ledger_credit_label)
        credit_row.addWidget(self.ledger_credit_bar)
        layout.addLayout(credit_row)

        self.ledger_table = make_table(["Date", "Item / Description", "Qty", "Rate", UDHAAR_LABEL, JAMA_LABEL, "Balance",
                                        "Due", "Notes"])
        fit_columns(self.ledger_table, 1)
        self.ledger_table.doubleClicked.connect(lambda _i: self._edit_entry())
        layout.addWidget(self.ledger_table, 1)

        self.ledger_summary = QLabel("")
        self.ledger_summary.setObjectName("h2")
        self.ledger_note = QLabel("")
        self.ledger_note.setObjectName("muted")
        layout.addWidget(self.ledger_summary)
        layout.addWidget(self.ledger_note)

        buttons = QHBoxLayout()
        add = primary_button("+ Add entry")
        quick_pay = plain_button("Quick payment")
        quick_pay.clicked.connect(self._ledger_quick_payment)
        edit = plain_button("Edit selected")
        delete = plain_button("Delete selected", danger=True)
        remind = plain_button("Send reminder")
        add.clicked.connect(self._ledger_add_entry)
        edit.clicked.connect(self._edit_entry)
        delete.clicked.connect(self._delete_entry)
        remind.clicked.connect(self._ledger_reminder)
        for b in (add, quick_pay, edit, delete):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(remind)
        layout.addLayout(buttons)
        return page

    def _ledger_range_toggled(self, on: bool) -> None:
        self.ledger_from.setEnabled(on)
        self.ledger_to.setEnabled(on)
        self._refresh_ledger()

    def _ledger_args(self) -> dict:
        args = {"search": self.ledger_search.text()}
        if self.ledger_range.isChecked():
            args["date_from"] = self.ledger_from.text_dmy()
            args["date_to"] = self.ledger_to.text_dmy()
        return args

    def _current_ledger(self):
        cid = self.ledger_customer.currentData()
        if cid is None:
            return None
        return self.svc.ledger(cid, **self._ledger_args())

    def _refresh_ledger(self) -> None:
        self.ledger_table.setRowCount(0)
        self.ledger_credit_bar.setVisible(False)
        self.ledger_credit_label.setText("")
        try:
            led = self._current_ledger()
        except KhataError as exc:
            self.ledger_summary.setText("")
            self.ledger_info.setText("")
            self.ledger_note.setText(str(exc))
            return
        if led is None:
            self.ledger_info.setText("")
            self.ledger_summary.setText("Add a customer to begin.")
            self.ledger_note.setText("")
            return

        customer = led.customer
        self.ledger_info.setText(customer.name + (f"   ·   {customer.mobile}" if customer.mobile else ""))
        if customer.credit_limit_paise:
            balance = self.svc.customer_balance(customer.id)
            fraction = max(0.0, balance / customer.credit_limit_paise)
            self.ledger_credit_bar.setValue(min(100, round(fraction * 100)))
            _set_state(self.ledger_credit_bar, "over" if fraction > 1 else ("warn" if fraction >= viewmodel.WARN_FRACTION else ""))
            self.ledger_credit_label.setText(f"Credit used {money.format_inr(max(0, balance))} of "
                                             f"{money.format_inr(customer.credit_limit_paise)}")
            self.ledger_credit_bar.setVisible(True)

        open_list, _advance = insights.open_items(self.svc, customer.id)
        today_iso = self.svc.today().isoformat()
        overdue_ids = {i.txn.id for i in open_list if i.txn.due_date and i.txn.due_date < today_iso}
        danger = theme.current()["danger"]
        success = theme.current()["success"]

        self.ledger_table.setRowCount(len(led.rows))
        for r, row in enumerate(led.rows):
            t = row.txn
            set_cell(self.ledger_table, r, 0, dates.format_date(t.txn_date), data=t.id)
            set_cell(self.ledger_table, r, 1, t.item)
            set_cell(self.ledger_table, r, 2, t.quantity or "", right=True)
            set_cell(self.ledger_table, r, 3, money.format_inr(t.rate_paise, symbol=False) if t.rate_paise else "", right=True)
            set_cell(self.ledger_table, r, 4, money.format_inr(row.udhaar, symbol=False) if row.udhaar else "",
                     right=True, color=danger if row.udhaar else None)
            set_cell(self.ledger_table, r, 5, money.format_inr(row.jama, symbol=False) if row.jama else "",
                     right=True, color=success if row.jama else None)
            set_cell(self.ledger_table, r, 6, money.format_inr(row.balance, symbol=False), right=True,
                     color=balance_color(row.balance), bold=True)
            if t.due_date:
                overdue = t.id in overdue_ids
                set_cell(self.ledger_table, r, 7, dates.format_date(t.due_date) + ("  (overdue)" if overdue else ""),
                         color=danger if overdue else None)
            else:
                set_cell(self.ledger_table, r, 7, "")
            set_cell(self.ledger_table, r, 8, t.notes.replace("\n", " "))
        self.ledger_summary.setText(
            f"Opening {money.format_inr(led.opening_balance)}   |   "
            f"{UDHAAR_LABEL} {money.format_inr(led.total_udhaar)}   {JAMA_LABEL} {money.format_inr(led.total_jama)}   "
            f"({len(led.rows)} entries)   |   Closing {money.format_inr(led.closing_balance)}"
        )
        note = "Sorted by transaction date; entries on the same date keep the order they were entered."
        if led.search:
            note = "Item search is active: the running balance still shows the true account balance. " + note
        self.ledger_note.setText(note)

    def _ledger_add_entry(self) -> None:
        cid = self.ledger_customer.currentData()
        if cid is not None:
            self.entry_form.select_customer(cid)
        self.show_page("entry")

    def _ledger_reminder(self) -> None:
        cid = self.ledger_customer.currentData()
        if cid is None:
            self.info("No customer", "Select a customer first.")
            return
        self._show_reminder(cid)

    def _selected_txn_id(self) -> int | None:
        tid = selected_id(self.ledger_table)
        if tid is None:
            self.info("Select an entry", "Click an entry row first.")
        return tid

    def _edit_entry(self) -> None:
        tid = self._selected_txn_id()
        if tid is None:
            return
        txn = self.svc.get_transaction(tid)
        customer = self.svc.get_customer(txn.customer_id)
        dialog = EditEntryDialog(self.svc, txn, customer.name, parent=self)
        if dialog.exec():
            self.refresh_all()

    def _delete_entry(self) -> None:
        tid = self._selected_txn_id()
        if tid is None:
            return
        txn = self.svc.get_transaction(tid)
        text = (f"Delete this entry?\n\n{dates.format_date(txn.txn_date)}  {txn.txn_type}  "
                f"{money.format_inr(txn.amount_paise)}\n{txn.item}\n\nThis cannot be undone (it is kept in the "
                "Activity log). All balances will be recalculated.")
        if not self.confirm("Delete entry", text):
            return
        try:
            self.svc.delete_transaction(tid)
        except KhataError as exc:
            self.warn("Cannot delete", str(exc))
            return
        self.refresh_all()

    def _entry_customer_id_or_none(self) -> int | None:
        try:
            return self.entry_form.customer_id()
        except KhataError:
            return None

    def _statement_buttons(self, get_customer_id) -> list[QPushButton]:
        """Three one-click buttons (Print / PDF / Excel) for whichever customer ``get_customer_id()`` returns."""
        buttons = []
        for text, kind, tip in (("Print", "print", "Print this customer's statement"),
                                ("PDF", "pdf", "Save this customer's statement as a PDF file"),
                                ("Excel", "xlsx", "Save this customer's statement as an Excel file")):
            button = plain_button(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, k=kind: self._customer_statement(get_customer_id, k))
            buttons.append(button)
        return buttons

    def _customer_statement(self, get_customer_id, kind: str) -> None:
        """Statement of one customer (whole history) for the menu buttons on the Customers / New Entry pages."""
        cid = get_customer_id()
        if cid is None:
            self.info("Select a customer", "Select a customer first.")
            return
        try:
            led = self.svc.ledger(cid)
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        self._statement_output(led, kind)

    def _export_statement(self, kind: str) -> None:
        """Statement of the customer shown on the Ledger page (respects its date range and search)."""
        try:
            led = self._current_ledger()
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if led is None:
            self.info("No customer", "Select a customer first.")
            return
        self._statement_output(led, kind)

    def _statement_output(self, led, kind: str) -> None:
        if kind == "print":
            self._print_statement(led)
            return
        period = ""
        if led.date_from or led.date_to:
            period = f"_{led.date_from or 'start'}_{led.date_to or 'latest'}"
        default = f"Statement_{_safe_filename(led.customer.name)}{period}.{kind}"
        fn = exports.export_statement_pdf if kind == "pdf" else exports.export_statement_xlsx
        self._run_export("Export statement", default, kind,
                         lambda path: fn(led, path, self.shop_name(), self.shop_details()))

    def _print_statement(self, led) -> None:
        """Print preview with printer choice; works with any installed printer or 'Microsoft Print to PDF'."""
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog

        html = exports.statement_html(led, self.shop_name(), self.shop_details())
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle(f"Print statement \u2013 {led.customer.name}")
        preview.resize(960, 720)

        def paint(target) -> None:
            doc = QTextDocument()
            doc.setHtml(html)
            (getattr(doc, "print_", None) or doc.print)(target)

        preview.paintRequested.connect(paint)
        preview.exec()

    # ------------------------------------------------------------ recovery (aging / overdue / limits)
    def _build_recovery(self) -> QWidget:
        page, layout = self._page()
        self.rec_asof = DateField(allow_future=True)
        self.rec_asof.dateChanged.connect(lambda _d: self._refresh_recovery())
        asof_box = QWidget()
        asof_row = QHBoxLayout(asof_box)
        asof_row.setContentsMargins(0, 0, 0, 0)
        asof_row.addWidget(QLabel("As on"))
        asof_row.addWidget(self.rec_asof)
        self._header(layout, "Recovery", "Who owes what, for how long, and who to remind", asof_box)

        self.rec_tabs = QTabWidget()

        # -- aging
        aging_tab = QWidget()
        aging_layout = QVBoxLayout(aging_tab)
        aging_layout.setContentsMargins(0, 12, 0, 0)
        self.aging_table = make_table(["Customer", "Mobile", "0-30 days", "31-60 days", "61-90 days", "90+ days",
                                       "Outstanding", "Oldest unpaid"])
        fit_columns(self.aging_table, 0)
        aging_layout.addWidget(self.aging_table, 1)
        self.aging_total = QLabel("")
        self.aging_total.setObjectName("h2")
        aging_layout.addWidget(self.aging_total)
        aging_note = QLabel("Payments are applied to the oldest unpaid debit (Dr) first (FIFO). "
                            "Advance payments offset the customer's next debit (Dr).")
        aging_note.setObjectName("muted")
        aging_layout.addWidget(aging_note)
        aging_buttons = QHBoxLayout()
        remind = primary_button("Send reminder")
        remind.clicked.connect(lambda: self._recovery_reminder(self.aging_table))
        aging_pdf = plain_button("Export PDF")
        aging_xlsx = plain_button("Export Excel")
        aging_pdf.clicked.connect(lambda: self._export_aging("pdf"))
        aging_xlsx.clicked.connect(lambda: self._export_aging("xlsx"))
        aging_buttons.addWidget(remind)
        aging_buttons.addStretch(1)
        aging_buttons.addWidget(aging_pdf)
        aging_buttons.addWidget(aging_xlsx)
        aging_layout.addLayout(aging_buttons)
        self.rec_tabs.addTab(aging_tab, "Aging")

        # -- overdue
        overdue_tab = QWidget()
        overdue_layout = QVBoxLayout(overdue_tab)
        overdue_layout.setContentsMargins(0, 12, 0, 0)
        self.overdue_table = make_table(["Customer", "Item / Description", "Entry date", "Due date", "Days overdue",
                                         "Unpaid"])
        fit_columns(self.overdue_table, 1)
        overdue_layout.addWidget(self.overdue_table, 1)
        self.overdue_total = QLabel("")
        self.overdue_total.setObjectName("h2")
        overdue_layout.addWidget(self.overdue_total)
        overdue_buttons = QHBoxLayout()
        remind2 = primary_button("Send reminder")
        remind2.clicked.connect(lambda: self._recovery_reminder(self.overdue_table))
        overdue_pdf = plain_button("Export PDF")
        overdue_xlsx = plain_button("Export Excel")
        overdue_pdf.clicked.connect(lambda: self._export_overdue("pdf"))
        overdue_xlsx.clicked.connect(lambda: self._export_overdue("xlsx"))
        overdue_buttons.addWidget(remind2)
        overdue_buttons.addStretch(1)
        overdue_buttons.addWidget(overdue_pdf)
        overdue_buttons.addWidget(overdue_xlsx)
        overdue_layout.addLayout(overdue_buttons)
        self.rec_tabs.addTab(overdue_tab, "Overdue")

        # -- credit limits
        limit_tab = QWidget()
        limit_layout = QVBoxLayout(limit_tab)
        limit_layout.setContentsMargins(0, 12, 0, 0)
        self.limit_table = make_table(["Customer", "Balance", "Credit limit", "Over by", "Used"])
        fit_columns(self.limit_table, 0)
        limit_layout.addWidget(self.limit_table, 1)
        self.rec_tabs.addTab(limit_tab, "Over credit limit")

        layout.addWidget(self.rec_tabs, 1)
        return page

    def _refresh_recovery(self) -> None:
        as_of = self.rec_asof.text_dmy()
        try:
            report = insights.aging_report(self.svc, as_of)
            overdue = insights.overdue_items(self.svc, as_of)
        except KhataError as exc:
            self.warn("Recovery", str(exc))
            return
        danger = theme.current()["danger"]

        self.aging_table.setRowCount(len(report.rows))
        for r, row in enumerate(report.rows):
            set_cell(self.aging_table, r, 0, row.customer.name, data=row.customer.id, bold=True)
            set_cell(self.aging_table, r, 1, row.customer.mobile)
            for i, amount in enumerate(row.buckets):
                set_cell(self.aging_table, r, 2 + i, money.format_inr(amount, symbol=False) if amount else "",
                         right=True, color=danger if (i == 3 and amount) else None)
            set_cell(self.aging_table, r, 6, money.format_inr(row.total, symbol=False), right=True, bold=True)
            set_cell(self.aging_table, r, 7, dates.format_date(row.oldest_date) if row.oldest_date else "")
        self.aging_total.setText(
            "Total outstanding " + money.format_inr(report.total_outstanding) + "   |   " +
            "   ".join(f"{label}: {money.format_inr(amount)}"
                        for label, amount in zip(insights.BUCKET_LABELS, report.bucket_totals)))

        self.overdue_table.setRowCount(len(overdue))
        for r, row in enumerate(overdue):
            set_cell(self.overdue_table, r, 0, row.customer.name, data=row.customer.id, bold=True)
            set_cell(self.overdue_table, r, 1, row.txn.item)
            set_cell(self.overdue_table, r, 2, dates.format_date(row.txn.txn_date))
            set_cell(self.overdue_table, r, 3, dates.format_date(row.txn.due_date))
            set_cell(self.overdue_table, r, 4, str(row.days_overdue), right=True, color=danger)
            set_cell(self.overdue_table, r, 5, money.format_inr(row.remaining), right=True, bold=True)
        self.overdue_total.setText(f"{len(overdue)} overdue payment{'s' if len(overdue) != 1 else ''}, "
                                   f"{money.format_inr(sum(r.remaining for r in overdue))} unpaid")

        over = insights.over_limit_customers(self.svc)
        self.limit_table.setRowCount(len(over))
        for r, s in enumerate(over):
            limit = s.customer.credit_limit_paise
            set_cell(self.limit_table, r, 0, s.customer.name, data=s.customer.id, bold=True)
            set_cell(self.limit_table, r, 1, money.format_inr(s.balance), right=True, color=danger)
            set_cell(self.limit_table, r, 2, money.format_inr(limit), right=True)
            set_cell(self.limit_table, r, 3, money.format_inr(s.balance - limit), right=True, color=danger, bold=True)
            set_cell(self.limit_table, r, 4, f"{round(s.balance * 100 / limit)}%", right=True)

    def _recovery_reminder(self, table) -> None:
        cid = selected_id(table)
        if cid is None:
            self.info("Select a customer", "Click a row first.")
            return
        self._show_reminder(cid)

    def _export_aging(self, kind: str) -> None:
        try:
            report = insights.aging_report(self.svc, self.rec_asof.text_dmy())
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if not report.rows:
            self.info("Nothing to export", "No customer has an outstanding balance.")
            return
        fn = exports.export_aging_pdf if kind == "pdf" else exports.export_aging_xlsx
        self._run_export("Export aging report", f"Aging_{report.as_of}.{kind}", kind,
                         lambda path: fn(report, path, self.shop_name()))

    def _export_overdue(self, kind: str) -> None:
        try:
            as_of_iso = dates.parse_date(self.rec_asof.text_dmy(), allow_future=True)
            rows = insights.overdue_items(self.svc, as_of_iso)
        except (KhataError, dates.DateError) as exc:
            self.warn("Cannot export", str(exc))
            return
        if not rows:
            self.info("Nothing to export", "There are no overdue payments.")
            return
        fn = exports.export_overdue_pdf if kind == "pdf" else exports.export_overdue_xlsx
        self._run_export("Export overdue payments", f"Overdue_{as_of_iso}.{kind}", kind,
                         lambda path: fn(rows, as_of_iso, path, self.shop_name()))

    # ------------------------------------------------------------ reports
    def _build_reports(self) -> QWidget:
        page, layout = self._page()
        self._header(layout, "Reports", "Export exactly the data you select")

        register = Card("Transaction register")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.report_customer = QComboBox()
        self.rep_range = QCheckBox("Limit to date range")
        self.rep_from = DateField()
        self.rep_to = DateField()
        self.rep_from.setEnabled(False)
        self.rep_to.setEnabled(False)
        self.rep_range.toggled.connect(lambda on: (self.rep_from.setEnabled(on), self.rep_to.setEnabled(on)))
        dates_row = QHBoxLayout()
        dates_row.addWidget(self.rep_range)
        dates_row.addWidget(QLabel("From"))
        dates_row.addWidget(self.rep_from)
        dates_row.addWidget(QLabel("To"))
        dates_row.addWidget(self.rep_to)
        dates_row.addStretch(1)
        self.rep_search = QLineEdit()
        self.rep_search.setPlaceholderText("Item description contains… (optional)")
        form.addRow("Customer", self.report_customer)
        form.addRow("Dates", dates_row)
        form.addRow("Item search", self.rep_search)
        register.body.addLayout(form)
        reg_buttons = QHBoxLayout()
        reg_buttons.addStretch(1)
        reg_pdf = plain_button("Register PDF")
        reg_xlsx = primary_button("Register Excel")
        reg_pdf.clicked.connect(lambda: self._export_register("pdf"))
        reg_xlsx.clicked.connect(lambda: self._export_register("xlsx"))
        reg_buttons.addWidget(reg_pdf)
        reg_buttons.addWidget(reg_xlsx)
        register.body.addLayout(reg_buttons)
        layout.addWidget(register)

        balances = Card("Customer balance summary")
        bform = QFormLayout()
        self.rep_cust_filter = QLineEdit()
        self.rep_cust_filter.setPlaceholderText("Name or phone contains… (optional)")
        bform.addRow("Customers", self.rep_cust_filter)
        balances.body.addLayout(bform)
        bal_buttons = QHBoxLayout()
        bal_buttons.addStretch(1)
        bal_pdf = plain_button("Balances PDF")
        bal_xlsx = primary_button("Balances Excel")
        bal_pdf.clicked.connect(lambda: self._export_customers("pdf"))
        bal_xlsx.clicked.connect(lambda: self._export_customers("xlsx"))
        bal_buttons.addWidget(bal_pdf)
        bal_buttons.addWidget(bal_xlsx)
        balances.body.addLayout(bal_buttons)
        layout.addWidget(balances)

        tip = QLabel("A single customer's statement (opening/closing balance, running balance) is exported from the "
                     "Ledger page. Aging and overdue reports are on the Recovery page.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _export_register(self, kind: str) -> None:
        cid = self.report_customer.currentData()
        args = {"search": self.rep_search.text(), "customer_ids": None if cid is None else [cid]}
        if self.rep_range.isChecked():
            args["date_from"] = self.rep_from.text_dmy()
            args["date_to"] = self.rep_to.text_dmy()
        try:
            reg = self.svc.register(**args)
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if not reg.rows:
            self.info("Nothing to export", "No entries match these filters.")
            return
        label = self.report_customer.currentText() or "All customers"
        fn = exports.export_register_pdf if kind == "pdf" else exports.export_register_xlsx
        self._run_export("Export register", f"Register_{_safe_filename(label)}.{kind}", kind,
                         lambda path: fn(reg, path, self.shop_name(), label))

    def _export_customers(self, kind: str) -> None:
        text = self.rep_cust_filter.text()
        summaries = self.svc.list_customers(text)
        if not summaries:
            self.info("Nothing to export", "No customers match this filter.")
            return
        fn = exports.export_customers_pdf if kind == "pdf" else exports.export_customers_xlsx
        self._run_export("Export customer balances", f"Customer_balances.{kind}", kind,
                         lambda path: fn(summaries, path, self.shop_name(), text.strip()))

    # ------------------------------------------------------------ activity log
    def _build_activity(self) -> QWidget:
        page, layout = self._page()
        self._header(layout, "Activity", "Every add, edit and delete is recorded here; the app has no way to edit or remove these records")
        self.audit_search = QLineEdit()
        self.audit_search.setPlaceholderText("Search the activity log…")
        self.audit_search.setClearButtonEnabled(True)
        self.audit_search.textChanged.connect(lambda _t: self._refresh_activity())
        layout.addWidget(self.audit_search)
        self.audit_table = make_table(["When", "Action", "Details"])
        fit_columns(self.audit_table, 2)
        self.audit_table.doubleClicked.connect(lambda _i: self._open_audit_detail())
        layout.addWidget(self.audit_table, 1)
        self.audit_note = QLabel("Double-click a row to see exactly what changed. The latest 500 entries are shown.")
        self.audit_note.setObjectName("muted")
        layout.addWidget(self.audit_note)
        return page

    def _refresh_activity(self) -> None:
        entries = self.svc.audit_entries(500, search=self.audit_search.text())
        self._audit_cache = {e.id: e for e in entries}
        self.audit_table.setRowCount(len(entries))
        danger = theme.current()["danger"]
        for r, e in enumerate(entries):
            set_cell(self.audit_table, r, 0, viewmodel.timestamp_text(e.at), data=e.id)
            set_cell(self.audit_table, r, 1, viewmodel.action_label(e.action), bold=True,
                     color=danger if e.action.endswith("DELETE") else None)
            set_cell(self.audit_table, r, 2, e.summary)

    def _open_audit_detail(self) -> None:
        entry = self._audit_cache.get(selected_id(self.audit_table))
        if entry is not None:
            AuditDetailDialog(entry, parent=self).exec()

    # ------------------------------------------------------------ settings
    def _build_settings(self) -> QWidget:
        outer, outer_layout = self._page()
        self._header(outer_layout, "Settings", "Business details, security, backups and appearance")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        # business + reminder text
        business = Card("Business and reminders")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.shop_edit = QLineEdit()
        self.shop_edit.setPlaceholderText("Shown at the top of every PDF / Excel report and in reminders")
        self.shop_details_edit = QPlainTextEdit()
        self.shop_details_edit.setPlaceholderText("Address, phone, GSTIN – printed under the name on statements")
        self.shop_details_edit.setFixedHeight(64)
        self.country_edit = QLineEdit()
        self.country_edit.setMaximumWidth(90)
        self.country_edit.setPlaceholderText("91")
        self.template_edit = QPlainTextEdit()
        self.template_edit.setFixedHeight(110)
        form.addRow("Business name", self.shop_edit)
        form.addRow("Details", self.shop_details_edit)
        form.addRow("WhatsApp country code", self.country_edit)
        form.addRow("Reminder message", self.template_edit)
        business.body.addLayout(form)
        placeholders = QLabel("You can use: " + "  ".join(insights.TEMPLATE_PLACEHOLDERS))
        placeholders.setObjectName("muted")
        business.body.addWidget(placeholders)
        business_buttons = QHBoxLayout()
        business_buttons.addStretch(1)
        reset_template = plain_button("Reset message")
        reset_template.clicked.connect(self._reset_template)
        save_business = primary_button("Save")
        save_business.clicked.connect(self._save_business)
        business_buttons.addWidget(reset_template)
        business_buttons.addWidget(save_business)
        business.body.addLayout(business_buttons)
        layout.addWidget(business)

        # appearance
        appearance = Card("Appearance")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setMaximumWidth(200)
        self.theme_combo.currentIndexChanged.connect(self._theme_combo_changed)
        appearance.body.addWidget(self.theme_combo)
        layout.addWidget(appearance)

        # security
        security_card = Card("Security")
        self.pin_status = QLabel("")
        security_card.body.addWidget(self.pin_status)
        pin_note = QLabel("The PIN locks the app window. It does not encrypt the data file, so keep Windows "
                          "user accounts and disk protection in place as well.")
        pin_note.setObjectName("muted")
        pin_note.setWordWrap(True)
        security_card.body.addWidget(pin_note)
        pin_buttons = QHBoxLayout()
        self.pin_set_button = plain_button("Set PIN")
        self.pin_set_button.clicked.connect(self._set_pin)
        self.pin_remove_button = plain_button("Remove PIN", danger=True)
        self.pin_remove_button.clicked.connect(self._remove_pin)
        lock = plain_button("Lock now")
        lock.clicked.connect(self.lock_now)
        pin_buttons.addWidget(self.pin_set_button)
        pin_buttons.addWidget(self.pin_remove_button)
        pin_buttons.addWidget(lock)
        pin_buttons.addStretch(1)
        security_card.body.addLayout(pin_buttons)
        layout.addWidget(security_card)

        # data / backups
        data = Card("Data and backups")
        self.data_label = QLabel("")
        self.data_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.data_label.setWordWrap(True)
        data.body.addWidget(self.data_label)
        self.auto_backup_check = QCheckBox("Back up automatically once a day (keeps the latest 14)")
        self.auto_backup_check.toggled.connect(self._auto_backup_toggled)
        data.body.addWidget(self.auto_backup_check)
        self.auto_backup_label = QLabel("")
        self.auto_backup_label.setObjectName("muted")
        self.auto_backup_label.setWordWrap(True)
        data.body.addWidget(self.auto_backup_label)
        data_buttons = QHBoxLayout()
        choose = plain_button("Choose backup folder…")
        choose.clicked.connect(self._choose_backup_folder)
        now = plain_button("Back up now")
        now.clicked.connect(self._backup_now)
        create = plain_button("Save backup as…")
        create.clicked.connect(self._create_backup)
        restore = plain_button("Restore from backup…")
        restore.clicked.connect(self._restore_backup)
        open_folder = plain_button("Open data folder")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.svc.db_path.parent))))
        for b in (choose, now, create, restore, open_folder):
            data_buttons.addWidget(b)
        data_buttons.addStretch(1)
        data.body.addLayout(data_buttons)
        layout.addWidget(data)
        layout.addStretch(1)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        return outer

    def _refresh_settings(self) -> None:
        self.shop_edit.setText(self.shop_name())
        self.shop_details_edit.setPlainText(self.shop_details())
        self.country_edit.setText(self.svc.get_setting(insights.SETTING_COUNTRY_CODE, insights.DEFAULT_COUNTRY_CODE))
        self.template_edit.setPlainText(insights.get_template(self.svc))

        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(theme.current_name())))
        self.theme_combo.blockSignals(False)

        has_pin = security.has_pin(self.svc)
        self.pin_status.setText("PIN lock is ON" if has_pin else "PIN lock is OFF")
        self.pin_set_button.setText("Change PIN" if has_pin else "Set PIN")
        self.pin_remove_button.setEnabled(has_pin)

        d = self.svc.dashboard()
        self.data_label.setText(f"Data file: {self.svc.db_path}\n{d.total_customers} customers, "
                                f"{d.total_transactions} entries.")
        self.auto_backup_check.blockSignals(True)
        self.auto_backup_check.setChecked(self.svc.get_setting("auto_backup_enabled", "1") == "1")
        self.auto_backup_check.blockSignals(False)
        folder = self.svc.auto_backup_dir()
        count = len(list(folder.glob("auto-*.db"))) if folder.is_dir() else 0
        self.auto_backup_label.setText(f"Folder: {folder}   ({count} automatic backup{'s' if count != 1 else ''} stored)")

    def _save_business(self) -> None:
        country = "".join(ch for ch in self.country_edit.text() if ch.isdigit())
        if not country:
            self.warn("Country code", "Enter the WhatsApp country code as digits, for example 91.")
            return
        try:
            insights.set_template(self.svc, self.template_edit.toPlainText())
        except KhataError as exc:
            self.warn("Reminder message", str(exc))
            return
        lines = [line.strip() for line in self.shop_details_edit.toPlainText().splitlines() if line.strip()]
        self.svc.set_setting("shop_name", " ".join(self.shop_edit.text().split()))
        self.svc.set_setting("shop_details", "\n".join(lines[:4]))
        self.svc.set_setting(insights.SETTING_COUNTRY_CODE, country)
        self.statusBar().showMessage("Settings saved", 4000)
        self._refresh_settings()

    def _reset_template(self) -> None:
        insights.set_template(self.svc, "")
        self.template_edit.setPlainText(insights.get_template(self.svc))

    # -- theme
    def _update_theme_button(self) -> None:
        self.theme_button.setText("Switch to light mode" if theme.current_name() == "dark" else "Switch to dark mode")

    def _apply_theme(self, name: str) -> None:
        theme.set_current(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(name))
        self._update_theme_button()
        self.chart.update()
        self.refresh_all()

    def _toggle_theme(self) -> None:
        name = "light" if theme.current_name() == "dark" else "dark"
        self.svc.set_setting("theme", name)
        self._apply_theme(name)

    def _theme_combo_changed(self, _index: int) -> None:
        name = self.theme_combo.currentData() or "light"
        self.svc.set_setting("theme", name)
        self._apply_theme(name)

    # -- PIN / lock
    def _set_pin(self) -> None:
        if PinSetupDialog(self.svc, parent=self).exec():
            self._refresh_settings()

    def _remove_pin(self) -> None:
        pin, ok = QInputDialog.getText(self, "Remove PIN", "Enter the current PIN to remove the lock:",
                                       QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            security.clear_pin(self.svc, pin)
        except KhataError as exc:
            self.warn("Remove PIN", str(exc))
            return
        self._refresh_settings()
        self.statusBar().showMessage("PIN removed", 4000)

    def lock_now(self) -> None:
        if not security.has_pin(self.svc):
            self.info("No PIN set", "Set a PIN under Settings > Security first.")
            return
        if self._locked:
            return
        self._locked = True
        root = self.centralWidget()
        root.setVisible(False)  # hide the data while the PIN screen is up
        try:
            unlocked = LockDialog(self.svc, self.guard, self).exec()
        finally:
            root.setVisible(True)
            self._locked = False
        if not unlocked:
            self.close()

    # -- backups
    def _auto_backup_toggled(self, on: bool) -> None:
        self.svc.set_setting("auto_backup_enabled", "1" if on else "0")

    def _choose_backup_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the automatic backup folder",
                                                  str(self.svc.auto_backup_dir().parent))
        if folder:
            self.svc.set_setting("auto_backup_dir", folder)
            self._refresh_settings()

    def _backup_now(self) -> None:
        try:
            path = self.svc.auto_backup_if_due(force=True)
        except (backup.BackupError, OSError) as exc:
            self.warn("Backup failed", str(exc))
            return
        self._refresh_settings()
        self.info("Backup complete", f"Saved and verified:\n{path}")

    def _create_backup(self) -> None:
        default = f"VX7_KHATA_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
        path = self._export_path("Save backup", default, "VX7 KHATA backup (*.db)")
        if not path:
            return
        try:
            self.svc.create_backup(path)
            info = backup.validate_backup(path)
        except (backup.BackupError, OSError) as exc:
            self.warn("Backup failed", str(exc))
            return
        self.info("Backup complete", f"Saved and verified:\n{path}\n\n{info.customers} customers, "
                  f"{info.transactions} entries.")

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup to restore", _documents_dir(),
                                              "VX7 KHATA backup (*.db);;All files (*)")
        if not path:
            return
        try:
            info = backup.validate_backup(path)
        except backup.BackupError as exc:
            self.warn("This backup cannot be used", str(exc))
            return
        current = self.svc.dashboard()
        text = (f"Restore this backup?\n\n{path}\n\nBackup contains {info.customers} customers and "
                f"{info.transactions} entries.\nYour current data ({current.total_customers} customers, "
                f"{current.total_transactions} entries) will be replaced, including settings such as the PIN.\n\n"
                "A safety copy of the current data is saved first.")
        if not self.confirm("Restore backup", text):
            return
        try:
            safety = self.svc.restore_backup(path, safety_dir=self.svc.db_path.parent / "safety_backups")
        except (backup.BackupError, KhataError, OSError) as exc:
            self.warn("Restore failed", f"{exc}\n\nYour current data was not changed.")
            return
        self._apply_theme(self.svc.get_setting("theme", "light"))
        self.info("Restore complete", f"Backup restored.\n\nSafety copy of your previous data:\n{safety}")

    # ------------------------------------------------------------ lifecycle
    def _auto_post_pending(self) -> None:
        """Save every Daily Entry row whose 24 hours are over (also covers the time the app was closed)."""
        if self._saving:
            return
        try:
            posted = self.svc.post_due_pending()
        except (KhataError, sqlite3.Error) as exc:
            self.statusBar().showMessage(f"Could not save waiting Daily Entry rows: {exc}", 8000)
            return
        if posted:
            self.refresh_all()
            self.statusBar().showMessage(f"{len(posted)} Daily Entry row(s) saved into the ledgers after 24 hours", 10000)
        elif self.current_key == "daily":
            self.daily_page.refresh_pending()  # keeps the 'Saves in' countdown up to date

    def run_startup_tasks(self) -> None:
        """Save Daily Entry rows that became due while the app was closed, then the daily automatic backup.
        Called once after the window is shown."""
        self._auto_post_pending()
        if self.svc.get_setting("auto_backup_enabled", "1") != "1":
            return
        try:
            self.svc.auto_backup_if_due()
        except (backup.BackupError, OSError) as exc:
            self.warn("Automatic backup failed", f"{exc}\n\nChoose another folder under Settings > Data and backups.")

    def closeEvent(self, event) -> None:
        try:
            if self.svc.get_setting("auto_backup_enabled", "1") == "1":
                self.svc.auto_backup_if_due()
        except Exception:  # noqa: BLE001 - a failed backup must never stop the app from closing
            pass
        super().closeEvent(event)

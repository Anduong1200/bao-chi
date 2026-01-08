"""
Flash News Hunter - Triage UI
3-Tab workflow: Stream → Reading Box → Archive
"""

import sys
import asyncio
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
    QLineEdit, QComboBox, QSplitter, QGroupBox, QHeaderView,
    QFileDialog, QMessageBox, QTabWidget, QSpinBox, QPlainTextEdit,
    QCheckBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QColor, QTextCursor, QIcon

from config import get_config
from storage import get_storage, Article, STATUS_NEW, STATUS_PICKED, STATUS_ARCHIVED
from main import FlashNewsHunter


# === Dark Theme ===
DARK_STYLE = """
QMainWindow, QWidget, QDialog {
    background-color: #0d1117;
    color: #c9d1d9;
    font-family: 'Segoe UI', sans-serif;
}
QTabWidget::pane { border: 2px solid #30363d; }
QTabBar::tab { 
    background: #161b22; 
    color: #c9d1d9; 
    padding: 10px 20px; 
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #238636; color: white; }
QTabBar::tab:hover { background: #21262d; }

QPushButton {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: bold;
}
QPushButton:hover { background: #30363d; border-color: #8b949e; }
QPushButton:disabled { color: #484f58; }
QPushButton#pickBtn { background: #1f6feb; }
QPushButton#pickBtn:hover { background: #388bfd; }
QPushButton#saveBtn { background: #238636; }
QPushButton#saveBtn:hover { background: #2ea043; }
QPushButton#discardBtn { background: #da3633; }
QPushButton#discardBtn:hover { background: #f85149; }
QPushButton#startBtn { background: #238636; }
QPushButton#stopBtn { background: #da3633; }

QTableWidget {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #30363d;
    gridline-color: #21262d;
}
QTableWidget::item { padding: 8px; border-bottom: 1px solid #21262d; }
QTableWidget::item:selected { background: #1f6feb; }
QHeaderView::section { 
    background: #161b22; 
    color: #8b949e; 
    padding: 10px; 
    border: none;
    font-weight: bold;
}

QTextEdit, QPlainTextEdit {
    background: #161b22;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 10px;
}

QGroupBox {
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 15px;
    padding-top: 15px;
    font-weight: bold;
    color: #58a6ff;
}
QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 10px; }

QLineEdit, QSpinBox, QComboBox {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #30363d;
    padding: 8px;
    border-radius: 6px;
}
QLineEdit:focus { border-color: #58a6ff; }

QCheckBox::indicator { 
    width: 18px; height: 18px; 
    border-radius: 4px;
    border: 2px solid #30363d;
}
QCheckBox::indicator:checked { background: #238636; }

QSplitter::handle { background: #30363d; }
"""


class HunterSignals(QObject):
    """Thread-safe signals."""
    article_captured = pyqtSignal(object)
    log_message = pyqtSignal(str, str)
    stats_updated = pyqtSignal(dict)


class HunterThread(QThread):
    """Background thread for capture loop."""
    
    def __init__(self, signals: HunterSignals, interval: int):
        super().__init__()
        self.signals = signals
        self.interval = interval
        self.hunter: Optional[FlashNewsHunter] = None
        self._loop = None
    
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        self.hunter = FlashNewsHunter(
            poll_interval=self.interval,
            on_article=self._on_article,
            on_log=self._on_log
        )
        
        try:
            self._loop.run_until_complete(self.hunter.start())
        except Exception as e:
            self.signals.log_message.emit(f"Error: {e}", "error")
        finally:
            self._loop.close()
    
    def _on_article(self, article: Article):
        self.signals.article_captured.emit(article)
    
    def _on_log(self, msg: str, level: str):
        self.signals.log_message.emit(msg, level)
    
    def stop(self):
        if self.hunter and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.hunter.stop(), self._loop)
        elif self.hunter:
             # Loop already closed, just ensure hunter logic knows it's stopping if possible, 
             # but asyncio tasks are likely dead.
             pass


class ArticleReader(QDialog):
    """Fullscreen article reader from cache."""
    
    def __init__(self, article: Article, parent=None):
        super().__init__(parent)
        self.article = article
        self.setWindowTitle(article.title[:60])
        self.setMinimumSize(900, 700)
        self.setWindowIcon(QIcon())
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        link_status = "🟢 LIVE" if self.article.link_alive else "🔴 DEAD"
        status_color = "#238636" if self.article.link_alive else "#da3633"
        
        header = QLabel(f"""
            <h1 style="color: #58a6ff; margin: 0;">{self.article.title}</h1>
            <p style="color: #8b949e; margin: 5px 0;">
                <b>Source:</b> {self.article.source_name} | 
                <b>Author:</b> {self.article.author} |
                <b>Captured:</b> {self.article.crawled_at[:19]}
            </p>
            <p style="color: {status_color}; font-weight: bold; margin: 5px 0;">
                Original Link: {link_status}
            </p>
        """)
        header.setWordWrap(True)
        layout.addWidget(header)
        
        # Content (from CACHE - works even if link is dead)
        content = QTextEdit()
        content.setReadOnly(True)
        
        import re
        clean = re.sub(r'<img[^>]*>', '', self.article.content_html)
        
        content.setHtml(f"""
            <style>
                body {{ line-height: 1.8; }}
                .sapo {{ font-style: italic; color: #8b949e; font-size: 16px; }}
            </style>
            <div class="sapo">{self.article.sapo}</div>
            <hr style="border-color: #30363d;">
            <div>{clean}</div>
        """)
        layout.addWidget(content)
        
        # Notice
        if not self.article.link_alive:
            notice = QLabel("""
                <p style="background: #da3633; color: white; padding: 10px; border-radius: 6px;">
                    ⚠️ Original link is DEAD. You are reading from CACHED version.
                </p>
            """)
            layout.addWidget(notice)
        
        # Footer
        footer = QHBoxLayout()
        
        url_btn = QPushButton("Open Original URL")
        url_btn.clicked.connect(self._open_url)
        footer.addWidget(url_btn)
        
        footer.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        
        layout.addLayout(footer)
    
    def _open_url(self):
        import webbrowser
        webbrowser.open(self.article.url)


class StreamPanel(QWidget):
    """The Stream - New articles flowing in."""
    
    def __init__(self, signals: HunterSignals):
        super().__init__()
        self.signals = signals
        self.storage = get_storage()
        self.hunter_thread = None
        self._setup_ui()
        self._connect_signals()
        self.seen_ids = set()
        # self._refresh() # Don't load history on startup for "Live Mode" feel
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        # Controls
        ctrl = QGroupBox("Capture Controls")
        ctrl_layout = QHBoxLayout(ctrl)
        
        # Interval
        ctrl_layout.addWidget(QLabel("Interval:"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(3, 60)
        self.interval_spin.setValue(5)
        self.interval_spin.setSuffix("s")
        ctrl_layout.addWidget(self.interval_spin)
        
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setFixedWidth(50)
        self.apply_btn.clicked.connect(self._restart_if_running)
        ctrl_layout.addWidget(self.apply_btn)
        
        # Source Filter
        ctrl_layout.addWidget(QLabel("  Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All Sources")
        # Load sources
        from config import get_config
        for src in get_config().sources:
            if src.enabled:
                self.filter_combo.addItem(src.name)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        ctrl_layout.addWidget(self.filter_combo)
        
        # Search
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search title...")
        self.search_edit.textChanged.connect(self._apply_filter)
        ctrl_layout.addWidget(self.search_edit)

        self.pick_selected_btn = QPushButton("📥 Pick Selected")
        self.pick_selected_btn.clicked.connect(self._pick_selected)
        ctrl_layout.addWidget(self.pick_selected_btn)

        ctrl_layout.addStretch()

        self.start_btn = QPushButton("▶ Start Capture")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._start)
        ctrl_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        ctrl_layout.addWidget(self.stop_btn)
        
        # Status
        self.status = QLabel("● Stopped")
        self.status.setStyleSheet("color: #da3633; font-weight: bold; margin-left: 10px;")
        ctrl_layout.addWidget(self.status)
        
        layout.addWidget(ctrl)
        
        # Stats
        stats_layout = QHBoxLayout()
        self.stat_new = QLabel("New: 0")
        self.stat_new.setStyleSheet("color: #58a6ff; font-size: 14px;")
        stats_layout.addWidget(self.stat_new)
        
        self.stat_dead = QLabel("Dead Links: 0")
        self.stat_dead.setStyleSheet("color: #da3633; font-size: 14px;")
        stats_layout.addWidget(self.stat_dead)
        
        stats_layout.addStretch()
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)
        stats_layout.addWidget(refresh_btn)
        
        layout.addLayout(stats_layout)
        
        # Table + Log
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Article table
        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        table_layout.setContentsMargins(0, 0, 0, 0)
        
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Time", "Link", "Source", "Title", "Action", "ID"])
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(4, 80)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(5, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self._on_double_click)
        table_layout.addWidget(self.table)
        
        splitter.addWidget(table_widget)
        
        # Log
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Log"))
        clear_btn = QPushButton("Clear")
        clear_btn.setMaximumWidth(60)
        clear_btn.clicked.connect(lambda: self.log.clear())
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)
        
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        log_layout.addWidget(self.log)
        
        splitter.addWidget(log_widget)
        splitter.setSizes([400, 150])
        
        layout.addWidget(splitter)
    
    def _connect_signals(self):
        self.signals.article_captured.connect(self._on_article)
        self.signals.log_message.connect(self._on_log)
    
    def _matches_filter(self, article: Article) -> bool:
        """Check if article matches current filters."""
        source_filter = self.filter_combo.currentText()
        if source_filter != "All Sources" and article.source_name != source_filter:
            return False
            
        search_text = self.search_edit.text().lower().strip()
        if search_text and search_text not in article.title.lower():
            return False
            
        return True

    def _refresh(self):
        """Refresh table from DB with filters."""
        self.table.setRowCount(0)
        self.seen_ids.clear()
        
        # Get history (limit 500)
        articles = self.storage.get_stream(500)
        filtered = [a for a in articles if self._matches_filter(a)]
        
        # Add to table (bottom append)
        for article in filtered:
            self._add_row(article, at_top=False)
        
        # Update Stats
        stats = self.storage.get_stats()
        self.stat_new.setText(f"New: {stats['new']}")
        self.stat_dead.setText(f"Dead Links: {stats['dead_links']}")

    def _restart_if_running(self):
        """Restart capture if running to apply new interval."""
        if hasattr(self, 'hunter_thread') and self.hunter_thread and self.hunter_thread.isRunning():
            self._stop()
            QTimer.singleShot(1000, self._start)
            
    def _apply_filter(self):
        """Trigger refresh on filter change."""
        self._refresh()
    
    def _add_row(self, article: Article, at_top: bool=False):
        """Add article to table if not duplicate."""
        if article.id in self.seen_ids:
            return
            
        self.seen_ids.add(article.id)
        
        row = 0 if at_top else self.table.rowCount()
        self.table.insertRow(row)
        
        # Time
        time_str = article.crawled_at[11:19] if len(article.crawled_at) > 19 else ""
        self.table.setItem(row, 0, QTableWidgetItem(time_str))
        
        # Link status
        link_item = QTableWidgetItem("🟢" if article.link_alive else "🔴")
        link_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 1, link_item)
        
        # Source
        self.table.setItem(row, 2, QTableWidgetItem(article.source_name[:12]))
        
        # Title
        title_item = QTableWidgetItem(article.title[:60])
        if not article.link_alive:
            title_item.setForeground(QColor("#da3633"))
        self.table.setItem(row, 3, title_item)
        
        # Pick button
        pick_btn = QPushButton("Pick")
        pick_btn.setObjectName("pickBtn")
        pick_btn.clicked.connect(lambda checked, aid=article.id: self._pick_article(aid))
        self.table.setCellWidget(row, 4, pick_btn)
        
        # ID (hidden)
        self.table.setItem(row, 5, QTableWidgetItem(article.id))
        
        # Limit rows
        while self.table.rowCount() > 500:
            self.table.removeRow(self.table.rowCount() - 1)
    
    def _on_article(self, article: Article):
        """Handle new article from capture."""
        if not self._matches_filter(article):
            return
            
        self._add_row(article, at_top=True)
        
        # Update stats
        stats = self.storage.get_stats()
        self.stat_new.setText(f"New: {stats['new']}")
    
    def _on_log(self, msg: str, level: str="INFO"):
        time = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{time}] {msg}")
        self.log.moveCursor(QTextCursor.MoveOperation.End)
    
    def _pick_article(self, article_id):
        """Move article to Reading Box."""
        self.storage.pick_article(article_id)
        # Remove from table immediately for responsiveness
        for row in range(self.table.rowCount()):
            if self.table.item(row, 5).text() == article_id:
                self.table.removeRow(row)
                break
        
    def _pick_selected(self):
        """Pick currently selected article."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
            
        for index in rows:
            article_id = self.table.item(index.row(), 5).text()
            self.storage.pick_article(article_id)
            
        self._refresh()
    
    def _on_double_click(self, index):
        """Read article."""
        article_id = self.table.item(index.row(), 5).text()
        article = self.storage.get_article(article_id)
        if article:
            dialog = ArticleReader(article, self)
            dialog.setStyleSheet(DARK_STYLE)
            dialog.exec()
    
    def _start(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText("● Capturing")
        self.status.setStyleSheet("color: #238636; font-weight: bold;")
        
        self.hunter_thread = HunterThread(self.signals, self.interval_spin.value())
        self.hunter_thread.finished.connect(self._on_stopped)
        self.hunter_thread.start()
    
    def _stop(self):
        if self.hunter_thread:
            self.hunter_thread.stop()
    
    def _on_stopped(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("● Stopped")
        self.status.setStyleSheet("color: #da3633; font-weight: bold;")


class ReadingBoxPanel(QWidget):
    """Reading Box - Review picked articles."""
    
    def __init__(self):
        super().__init__()
        self.storage = get_storage()
        self._setup_ui()
        self._refresh()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel("<h2>📥 Reading Box</h2>"))
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        
        header.addStretch()
        
        self.count_label = QLabel("0 items")
        self.count_label.setStyleSheet("color: #8b949e;")
        header.addWidget(self.count_label)
        
        layout.addLayout(header)
        
        # Splitter: List + Reader
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # List
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Time", "Source", "Title", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(3, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_select)
        list_layout.addWidget(self.table)
        
        splitter.addWidget(list_widget)
        
        # Reader
        reader_widget = QWidget()
        reader_layout = QVBoxLayout(reader_widget)
        
        self.reader = QTextEdit()
        self.reader.setReadOnly(True)
        reader_layout.addWidget(self.reader)
        
        # Actions
        actions = QHBoxLayout()
        
        self.save_btn = QPushButton("💾 SAVE to Archive")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.clicked.connect(self._save)
        actions.addWidget(self.save_btn)
        
        self.discard_btn = QPushButton("🗑 Discard")
        self.discard_btn.setObjectName("discardBtn")
        self.discard_btn.clicked.connect(self._discard)
        actions.addWidget(self.discard_btn)
        
        back_btn = QPushButton("↩ Back to Stream")
        back_btn.clicked.connect(self._unpick)
        actions.addWidget(back_btn)
        
        reader_layout.addLayout(actions)
        
        splitter.addWidget(reader_widget)
        splitter.setSizes([300, 500])
        
        layout.addWidget(splitter)
    
    def _refresh(self):
        articles = self.storage.get_picked()
        self.table.setRowCount(0)
        
        for article in articles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            time_str = article.crawled_at[11:19] if len(article.crawled_at) > 19 else ""
            self.table.setItem(row, 0, QTableWidgetItem(time_str))
            self.table.setItem(row, 1, QTableWidgetItem(article.source_name[:12]))
            
            title_item = QTableWidgetItem(article.title[:60])
            if not article.link_alive:
                title_item.setForeground(QColor("#da3633"))
            self.table.setItem(row, 2, title_item)
            
            self.table.setItem(row, 3, QTableWidgetItem(article.id))
        
        self.count_label.setText(f"{len(articles)} items")
    
    def _on_select(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        
        article_id = self.table.item(selected[0].row(), 3).text()
        article = self.storage.get_article(article_id)
        
        if article:
            import re
            clean = re.sub(r'<img[^>]*>', '', article.content_html[:5000])
            
            link_status = "🟢 LIVE" if article.link_alive else "🔴 DEAD (Reading from cache)"
            
            self.reader.setHtml(f"""
                <h2 style="color: #58a6ff; margin: 0;">{article.title}</h2>
                <p style="color: #8b949e;">
                    {article.source_name} | {article.author} | {article.crawled_at[:19]}
                </p>
                <p style="color: {'#238636' if article.link_alive else '#da3633'}; font-weight: bold;">
                    {link_status}
                </p>
                <hr style="border-color: #30363d;">
                <p><i>{article.sapo}</i></p>
                <hr style="border-color: #30363d;">
                {clean}
            """)
    
    def _get_selected_id(self) -> Optional[str]:
        selected = self.table.selectedItems()
        if selected:
            return self.table.item(selected[0].row(), 3).text()
        return None
    
    def _save(self):
        article_id = self._get_selected_id()
        if article_id:
            self.storage.archive_article(article_id)
            self._refresh()
            self.reader.clear()
    
    def _discard(self):
        article_id = self._get_selected_id()
        if article_id:
            self.storage.discard_article(article_id)
            self._refresh()
            self.reader.clear()
    
    def _unpick(self):
        article_id = self._get_selected_id()
        if article_id:
            self.storage.unpick_article(article_id)
            self._refresh()
            self.reader.clear()


class ArchivePanel(QWidget):
    """Archive - Permanently saved articles."""
    
    def __init__(self):
        super().__init__()
        self.storage = get_storage()
        self._setup_ui()
        self._refresh()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel("<h2>📁 Archive</h2>"))
        
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.search.setMaximumWidth(200)
        self.search.returnPressed.connect(self._search)
        header.addWidget(self.search)
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        
        export_btn = QPushButton("💾 Export JSON")
        export_btn.clicked.connect(self._export)
        header.addWidget(export_btn)
        
        export_full_btn = QPushButton("📦 Export Full DB")
        export_full_btn.clicked.connect(self._export_full)
        header.addWidget(export_full_btn)
        
        import_btn = QPushButton("📥 Import DB")
        import_btn.clicked.connect(self._import_db)
        header.addWidget(import_btn)
        
        header.addStretch()
        
        self.count_label = QLabel("0 items")
        self.count_label.setStyleSheet("color: #8b949e;")
        header.addWidget(self.count_label)
        
        layout.addLayout(header)
        
        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Date", "Source", "Title", "Link", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(4, True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.doubleClicked.connect(self._on_double_click)
        splitter.addWidget(self.table)
        
        # Preview
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        preview_layout.addWidget(self.preview)
        
        open_btn = QPushButton("🌐 Open URL")
        open_btn.clicked.connect(self._open_url)
        preview_layout.addWidget(open_btn)
        
        splitter.addWidget(preview_widget)
        splitter.setSizes([500, 400])
        
        layout.addWidget(splitter)
    
    def _refresh(self):
        articles = self.storage.get_archived()
        self._populate(articles)
    
    def _search(self):
        keyword = self.search.text().strip()
        if keyword:
            articles = [a for a in self.storage.search_articles(keyword) if a.status == STATUS_ARCHIVED]
        else:
            articles = self.storage.get_archived()
        self._populate(articles)
    
    def _populate(self, articles):
        self.table.setRowCount(0)
        
        for article in articles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            date_str = article.crawled_at[:10] if len(article.crawled_at) >= 10 else ""
            self.table.setItem(row, 0, QTableWidgetItem(date_str))
            self.table.setItem(row, 1, QTableWidgetItem(article.source_name[:12]))
            
            title_item = QTableWidgetItem(article.title[:60])
            if not article.link_alive:
                title_item.setForeground(QColor("#da3633"))
            self.table.setItem(row, 2, title_item)
            
            link_item = QTableWidgetItem("🟢" if article.link_alive else "🔴")
            link_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, link_item)
            
            self.table.setItem(row, 4, QTableWidgetItem(article.id))
        
        self.count_label.setText(f"{len(articles)} items")
    
    def _on_select(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        
        article_id = self.table.item(selected[0].row(), 4).text()
        article = self.storage.get_article(article_id)
        
        if article:
            import re
            clean = re.sub(r'<img[^>]*>', '', article.content_html[:3000])
            
            self.preview.setHtml(f"""
                <h2 style="color: #58a6ff;">{article.title}</h2>
                <p style="color: #8b949e;">{article.source_name} | {article.author}</p>
                <hr>
                <p><i>{article.sapo}</i></p>
                <hr>
                {clean}...
            """)
    
    def _on_double_click(self, index):
        article_id = self.table.item(index.row(), 4).text()
        article = self.storage.get_article(article_id)
        if article:
            dialog = ArticleReader(article, self)
            dialog.setStyleSheet(DARK_STYLE)
            dialog.exec()
    
    def _open_url(self):
        selected = self.table.selectedItems()
        if selected:
            import webbrowser
            article_id = self.table.item(selected[0].row(), 4).text()
            article = self.storage.get_article(article_id)
            if article:
                webbrowser.open(article.url)
    
    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", f"archive_{datetime.now():%Y%m%d}.json", "JSON (*.json)"
        )
        if path:
            self.storage.export_json(path, STATUS_ARCHIVED)
            QMessageBox.information(self, "Done", f"Exported to {path}")
    
    def _export_full(self):
        """Export full database as SQLite backup."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Full Database", 
            f"backup_{datetime.now():%Y%m%d_%H%M}.db", 
            "SQLite Database (*.db)"
        )
        if path:
            data = self.storage.export_full_db(path)
            QMessageBox.information(
                self, "Done", 
                f"Exported to {data['path']}\n\n"
                f"Articles: {data['articles']}\n"
                f"Images: {data['images']}"
            )
    
    def _import_db(self):
        """Import database from backup."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Database", "", 
            "Database Files (*.db *.json);;SQLite (*.db);;JSON (*.json)"
        )
        if path:
            reply = QMessageBox.question(
                self, "Import Mode",
                "How do you want to import?\n\n"
                "YES = Merge (add to existing)\n"
                "NO = Replace (clear existing first)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            
            if reply == QMessageBox.StandardButton.Cancel:
                return
            
            merge = reply == QMessageBox.StandardButton.Yes
            result = self.storage.import_db(path, merge=merge)
            
            self._refresh()
            QMessageBox.information(
                self, "Done",
                f"Imported:\n"
                f"Articles: {result['articles']}\n"
                f"Images: {result['images']}"
            )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flash News Hunter")
        self.setMinimumSize(1400, 900)
        self.setWindowIcon(QIcon())
        
        self.signals = HunterSignals()
        self._setup_ui()
        self.setStyleSheet(DARK_STYLE)
    
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Tabs
        tabs = QTabWidget()
        
        self.stream = StreamPanel(self.signals)
        tabs.addTab(self.stream, "⚡ The Stream")
        
        self.reading_box = ReadingBoxPanel()
        tabs.addTab(self.reading_box, "📖 Reading Box")
        
        self.archive_scan = ArchiveScanPanel(self.signals) # Pass signals instead of hunter
        tabs.addTab(self.archive_scan, "🕰️ Archive Hunter")
        
        self.archive = ArchivePanel()
        tabs.addTab(self.archive, "📁 Database")
        
        tabs.currentChanged.connect(self._on_tab_changed)
        
        layout.addWidget(tabs)
    
    def _on_tab_changed(self, index):
        if index == 1:
            self.reading_box._refresh()
        elif index == 2:
            self.archive._refresh()
    
    def closeEvent(self, event):
        self.stream._stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


class DeepScanWorker(QThread):
    """Background worker for deep scanning."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(int)  # count of found articles
    
    def __init__(self, signals, source_config, target_date):
        super().__init__()
        self.signals = signals
        self.source_config = source_config
        self.target_date = target_date
        self._is_running = True
        
    def run(self):
        # Create a temporary scanner just for this task
        from scanner import Scanner
        scanner = Scanner(self.source_config)
        
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            links = loop.run_until_complete(
                scanner.scan_by_date(
                    self.target_date, 
                    progress_callback=self.progress.emit
                )
            )
            
            self.progress.emit(f"✅ Deep scan finished. Found {len(links)} manual candidates.")
            
            # Auto-archive found links
            count = 0
            
            # Use separate archiver instance
            from archiver import AutoArchiver
            from storage import get_storage
            from archiver import AutoArchiver
            from storage import get_storage
            from config import get_config
            
            archiver = AutoArchiver()
            
            for link in links:
                if not self._is_running: break
                self.progress.emit(f"Archiving: {link.title[:50]}...")
                
                # Synchronously run async capture in loop
                article = loop.run_until_complete(
                    archiver.capture(link, self.source_config.name)
                )
                
                if article:
                    self.signals.article_captured.emit(article)
                    count += 1
            
            loop.run_until_complete(archiver.close())
            self.finished.emit(count)
            
        except Exception as e:
            self.progress.emit(f"❌ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            self.finished.emit(0)
            
        finally:
            loop.run_until_complete(scanner.close())
            loop.close()

    def stop(self):
        self._is_running = False


class ArchiveScanPanel(QWidget):
    """Panel for Historical Deep Scan."""
    
    def __init__(self, signals: HunterSignals):
        super().__init__()
        self.signals = signals
        self.worker = None
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Controls
        ctrl_layout = QHBoxLayout()
        
        # Source Selector
        self.source_combo = QComboBox()
        self.refresh_sources()
        ctrl_layout.addWidget(QLabel("Source:"))
        ctrl_layout.addWidget(self.source_combo)
        
        # Date Picker
        from PyQt6.QtWidgets import QDateEdit
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(datetime.now().date())
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        ctrl_layout.addWidget(QLabel("Target Date:"))
        ctrl_layout.addWidget(self.date_edit)
        
        # Buttons
        self.btn_scan = QPushButton("Start Deep Scan")
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_scan.setStyleSheet("background: #1f6feb;")
        ctrl_layout.addWidget(self.btn_scan)
        
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)
        
        # Info
        info = QLabel("💡 This feature scans past pages to find articles from the selected date.")
        info.setStyleSheet("color: #8b949e; font-style: italic;")
        layout.addWidget(info)
        
        # Log Output
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("Scan logs will appear here...")
        layout.addWidget(self.log_area)
        
        # Progress Bar
        from PyQt6.QtWidgets import QProgressBar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        
    def refresh_sources(self):
        self.source_combo.clear()
        config = get_config()
        for source in config.sources:
            if source.deep_scan:
                self.source_combo.addItem(source.name, source)
                
    def log(self, msg: str):
        self.log_area.append(msg)
        cursor = self.log_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_area.setTextCursor(cursor)
        
    def start_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            self.btn_scan.setText("Start Deep Scan")
            self.btn_scan.setStyleSheet("background: #1f6feb;")
            self.progress_bar.hide()
            self.log("⏹️ Scan stopped by user.")
            return

        source_idx = self.source_combo.currentIndex()
        if source_idx < 0:
            QMessageBox.warning(self, "Error", "No deep-scan compatible source selected.")
            return
            
        source_config = self.source_combo.itemData(source_idx)
        target_date = self.date_edit.date().toPyDate()
        
        self.log(f"🚀 Starting Deep Scan for {source_config.name} on {target_date}...")
        
        self.worker = DeepScanWorker(self.signals, source_config, target_date)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.on_finished)
        
        self.btn_scan.setText("Stop Scan")
        self.btn_scan.setStyleSheet("background: #da3633;")
        self.progress_bar.show()
        
        self.worker.start()
        
    def on_finished(self, count):
        self.btn_scan.setText("Start Deep Scan")
        self.btn_scan.setStyleSheet("background: #1f6feb;")
        self.progress_bar.hide()
        self.log(f"🏁 Done. Total archived: {count}")
        QMessageBox.information(self, "Deep Scan Complete", f"Found and archived {count} articles.")


if __name__ == "__main__":
    main()

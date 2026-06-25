"""
MangaK Downloader — "🔗 URL" Tab

Allows the user to input a manga URL or slug, load the full manga info
(cover, details, rating, genres, summary), display the chapter list, and
trigger downloads.
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import (
    QObject,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
    Qt,
)
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mangak.core import (
    Manga,
    MangaKClient,
    ChapterListItem,
    DownloadTask,
    DownloadStatus,
    Settings,
    MangaNotFoundError,
)
from mangak.core.themes import Colors
from mangak.gui.widgets.glass_panel import GlassPanel
from mangak.gui.widgets.toast import ToastManager


# ──────────────────────────────────────────────
#  Background worker (QThread) for loading manga
# ──────────────────────────────────────────────


class _MangaLoader(QObject):
    """Loads manga info + chapter list in a background thread."""

    finished = pyqtSignal(object, list)  # Manga, list[ChapterListItem]
    error_occurred = pyqtSignal(str)

    def __init__(self, slug: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._slug = slug

    @pyqtSlot()
    def run(self) -> None:
        import asyncio

        try:
            async def _load() -> tuple[Manga, list[ChapterListItem]]:
                async with MangaKClient() as client:
                    manga = await client.get_manga_info(self._slug)
                    chapters = await client.get_chapter_list(manga.id, manga.cv)
                return manga, chapters

            manga, chapters = asyncio.run(_load())
            self.finished.emit(manga, chapters)
        except MangaNotFoundError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class _CoverLoader(QObject):
    """Downloads a cover image in a background thread."""

    loaded = pyqtSignal(bytes)

    def __init__(self, cover_url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._url = cover_url

    @pyqtSlot()
    def run(self) -> None:
        import asyncio

        try:

            async def _fetch() -> bytes:
                import httpx

                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=30,
                ) as c:
                    resp = await c.get(
                        self._url,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36"
                            ),
                            "Referer": "https://mangak.io/",
                        },
                    )
                    resp.raise_for_status()
                    return resp.content

            data = asyncio.run(_fetch())
            self.loaded.emit(data)
        except Exception:
            pass  # cover is optional — silently skip


# ──────────────────────────────────────────────
#  Chapter row widget
# ──────────────────────────────────────────────


class _ChapterRow(QFrame):
    """Single row in the chapter list with checkbox + download button."""

    download_requested = pyqtSignal(str, str, str, str, str)  # manga_slug, manga_name, chapter_slug, chapter_name, chapter_id

    def __init__(
        self,
        chapter: ChapterListItem,
        manga_slug: str,
        manga_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._chapter = chapter
        self._manga_slug = manga_slug
        self._manga_name = manga_name
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("chapterRow")
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
            #chapterRow {{
                background: {Colors.BG_SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 0 12px;
            }}
            #chapterRow:hover {{
                background: {Colors.BG_ELEVATED};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(12)

        # Checkbox for selection
        self._checkbox = QCheckBox()
        self._checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid {Colors.BORDER};
                border-radius: 4px;
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background: {Colors.ACCENT_PRIMARY};
                border: 2px solid {Colors.ACCENT_PRIMARY};
            }}
            QCheckBox::indicator:hover {{
                border: 2px solid {Colors.ACCENT_PRIMARY};
            }}
        """)
        layout.addWidget(self._checkbox)

        # Chapter name
        name_label = QLabel(self._chapter.name)
        name_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 13px;")
        name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(name_label)

        # Date
        date_label = QLabel(
            self._chapter.updated_at.strftime("%Y-%m-%d")
            if hasattr(self._chapter.updated_at, "strftime")
            else str(self._chapter.updated_at)[:10]
        )
        date_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(date_label)

        # Download button
        dl_btn = QPushButton("⬇")
        dl_btn.setFixedSize(32, 32)
        dl_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ACCENT_PRIMARY};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 15px;
            }}
            QPushButton:hover {{
                background: {Colors.ACCENT_PRIMARY}CC;
            }}
            QPushButton:pressed {{
                background: {Colors.ACCENT_PRIMARY}AA;
            }}
        """)
        dl_btn.clicked.connect(self._on_download)
        layout.addWidget(dl_btn)

    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self._checkbox.setChecked(checked)

    def chapter_slug(self) -> str:
        return self._chapter.slug

    def _on_download(self) -> None:
        self.download_requested.emit(
            self._manga_slug,
            self._manga_name,
            self._chapter.slug,
            self._chapter.name,
            self._chapter.id,
        )


# ──────────────────────────────────────────────
#  Manga-by-URL Tab
# ──────────────────────────────────────────────


class MangaByUrlTab(QWidget):
    """'🔗 URL' tab: input a manga URL/slug, view details + chapters, download."""

    download_started = pyqtSignal(object)  # DownloadTask

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_manga: Optional[Manga] = None
        self._current_chapters: list[ChapterListItem] = []
        self._settings = Settings()
        self._setup_ui()

    # ── UI Setup ───────────────────────────────

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"""
            MangaByUrlTab {{
                background: {Colors.BG_BASE};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # ── Title ──
        title = QLabel("🔗  Manga by URL")
        title_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        root.addWidget(title)

        # ── Input row ──
        input_panel = GlassPanel()
        input_layout = QHBoxLayout(input_panel)
        input_layout.setContentsMargins(16, 12, 16, 12)
        input_layout.setSpacing(12)

        input_layout.addWidget(QLabel("🔗"))

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://mangak.io/nano-machine  or just  nano-machine")
        self._url_input.setStyleSheet(f"""
            QLineEdit {{
                background: {Colors.BG_SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border-color: {Colors.ACCENT_PRIMARY};
            }}
        """)
        self._url_input.returnPressed.connect(self._on_load)
        input_layout.addWidget(self._url_input, 1)

        self._load_btn = QPushButton("Load")
        self._load_btn.setFixedHeight(36)
        self._load_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ACCENT_PRIMARY};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {Colors.ACCENT_PRIMARY}CC;
            }}
            QPushButton:pressed {{
                background: {Colors.ACCENT_PRIMARY}AA;
            }}
            QPushButton:disabled {{
                background: {Colors.BORDER};
                color: {Colors.TEXT_SECONDARY};
            }}
        """)
        self._load_btn.clicked.connect(self._on_load)
        input_layout.addWidget(self._load_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {Colors.BG_ELEVATED};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        self._clear_btn.clicked.connect(self._on_clear)
        input_layout.addWidget(self._clear_btn)

        root.addWidget(input_panel)

        # ── Loading indicator ──
        self._loading_label = QLabel("")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 14px;")
        self._loading_label.setVisible(False)
        root.addWidget(self._loading_label)

        # ── Manga detail panel ──
        self._detail_panel = GlassPanel()
        self._detail_panel.setVisible(False)
        detail_layout = QHBoxLayout(self._detail_panel)
        detail_layout.setContentsMargins(20, 16, 20, 16)
        detail_layout.setSpacing(20)

        # Cover
        self._cover_label = QLabel()
        self._cover_label.setFixedSize(140, 200)
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setStyleSheet(f"""
            background: {Colors.BG_SURFACE};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
        """)
        detail_layout.addWidget(self._cover_label)

        # Info column
        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)

        self._title_label = QLabel()
        title_font_big = QFont("Segoe UI", 18, QFont.Weight.Bold)
        self._title_label.setFont(title_font_big)
        self._title_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        self._title_label.setWordWrap(True)
        info_layout.addWidget(self._title_label)

        self._alt_label = QLabel()
        self._alt_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px;")
        info_layout.addWidget(self._alt_label)

        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"color: {Colors.ACCENT_SECONDARY}; font-size: 13px;")
        info_layout.addWidget(self._status_label)

        self._author_label = QLabel()
        self._author_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px;")
        info_layout.addWidget(self._author_label)

        # Genres as chips
        self._genres_layout = QHBoxLayout()
        self._genres_layout.setSpacing(6)
        info_layout.addLayout(self._genres_layout)

        # Rating
        self._rating_label = QLabel()
        self._rating_label.setStyleSheet(f"color: {Colors.WARNING}; font-size: 14px; font-weight: bold;")
        info_layout.addWidget(self._rating_label)

        # Summary
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px;")
        self._summary_label.setWordWrap(True)
        self._summary_label.setMaximumHeight(80)
        info_layout.addWidget(self._summary_label)

        info_layout.addStretch()
        detail_layout.addLayout(info_layout, 1)
        root.addWidget(self._detail_panel)

        # ── Chapters section ──
        chapters_header = QHBoxLayout()
        chapters_header.setSpacing(12)

        ch_title = QLabel("📖 Chapters")
        ch_title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        chapters_header.addWidget(ch_title)

        # Select All / Select None
        self._select_all_btn = QPushButton("☑ Select All")
        self._select_all_btn.setFixedHeight(34)
        self._select_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ACCENT_PRIMARY};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {Colors.ACCENT_PRIMARY}CC;
            }}
        """)
        self._select_all_btn.clicked.connect(self._on_select_all)
        chapters_header.addWidget(self._select_all_btn)

        self._select_none_btn = QPushButton("☐ Select None")
        self._select_none_btn.setFixedHeight(34)
        self._select_none_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.BG_SURFACE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {Colors.BG_HOVER};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        self._select_none_btn.clicked.connect(self._on_select_none)
        self._select_none_btn.setVisible(True)
        chapters_header.addWidget(self._select_none_btn)

        chapters_header.addStretch()

        # Download Selected
        self._dl_selected_btn = QPushButton("⬇ Download Selected")
        self._dl_selected_btn.setFixedHeight(34)
        self._dl_selected_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.SUCCESS};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {Colors.SUCCESS}CC;
            }}
            QPushButton:disabled {{
                background: {Colors.BORDER};
                color: {Colors.TEXT_SECONDARY};
            }}
        """)
        self._dl_selected_btn.clicked.connect(self._on_download_selected)
        self._dl_selected_btn.setVisible(True)
        chapters_header.addWidget(self._dl_selected_btn)

        root.addLayout(chapters_header)

        # Chapter scroll area
        self._chapter_scroll = QScrollArea()
        self._chapter_scroll.setWidgetResizable(True)
        self._chapter_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: {Colors.BG_SURFACE};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {Colors.BORDER};
                border-radius: 4px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self._chapter_container = QWidget()
        self._chapter_container.setStyleSheet("background: transparent;")
        self._chapter_list_layout = QVBoxLayout(self._chapter_container)
        self._chapter_list_layout.setContentsMargins(0, 0, 0, 0)
        self._chapter_list_layout.setSpacing(6)
        self._chapter_list_layout.addStretch()

        self._chapter_scroll.setWidget(self._chapter_container)
        root.addWidget(self._chapter_scroll, 1)

    # ── Slots ──────────────────────────────────

    def _on_load(self) -> None:
        text = self._url_input.text().strip()
        if not text:
            return

        slug = self._extract_slug(text)
        if not slug:
            ToastManager.show_toast_cls("Error", "Could not extract manga slug from input.", "error")
            return

        self._load_manga(slug)

    def _on_clear(self) -> None:
        self._url_input.clear()
        self._current_manga = None
        self._current_chapters = []
        self._detail_panel.setVisible(False)
        self._select_all_btn.setVisible(False)
        self._select_none_btn.setVisible(False)
        self._dl_selected_btn.setVisible(False)
        self._clear_chapter_list()
        self._loading_label.setVisible(False)

    def _on_select_all(self) -> None:
        """Check all chapter rows."""
        for i in range(self._chapter_list_layout.count()):
            item = self._chapter_list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _ChapterRow):
                item.widget().set_checked(True)

    def _on_select_none(self) -> None:
        """Uncheck all chapter rows."""
        for i in range(self._chapter_list_layout.count()):
            item = self._chapter_list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), _ChapterRow):
                item.widget().set_checked(False)

    def _on_download_selected(self) -> None:
        """Download all checked chapters."""
        if not self._current_manga or not self._current_chapters:
            return

        settings = Settings()
        fmt = settings.get("export_format", "cbz")
        dl_dir = settings.get("download_dir", "downloads")

        downloaded = 0
        for i in range(self._chapter_list_layout.count()):
            item = self._chapter_list_layout.itemAt(i)
            if not item or not item.widget() or not isinstance(item.widget(), _ChapterRow):
                continue
            row = item.widget()
            if not row.is_checked():
                continue

            ch = row._chapter
            task = DownloadTask(
                manga_slug=self._current_manga.slug,
                manga_name=self._current_manga.name,
                chapter_slug=ch.slug,
                chapter_name=ch.name,
                chapter_id=ch.id,
                images=[],  # images fetched during download
                format=fmt,
                output_dir=dl_dir,
                delete_after=settings.get("delete_images_after_export", True),
            )
            self.download_started.emit(task)
            downloaded += 1

        if downloaded > 0:
            ToastManager.show_toast_cls(
                "Queued", f"{downloaded} chapter(s) queued for download", "success"
            )

    def _load_manga(self, slug: str) -> None:
        self._loading_label.setText("⏳ Loading manga info…")
        self._loading_label.setVisible(True)
        self._detail_panel.setVisible(False)
        self._select_all_btn.setVisible(False)
        self._select_none_btn.setVisible(False)
        self._dl_selected_btn.setVisible(False)
        self._load_btn.setEnabled(False)
        self._clear_chapter_list()

        # Background thread
        self._worker_thread = QThread()
        self._worker = _MangaLoader(slug)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_manga_loaded)
        self._worker.error_occurred.connect(self._on_load_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error_occurred.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.start()

    def _clear_chapter_list(self) -> None:
        while self._chapter_list_layout.count() > 0:
            item = self._chapter_list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    @pyqtSlot(object, list)
    def _on_manga_loaded(self, manga: Manga, chapters: list[ChapterListItem]) -> None:
        self._loading_label.setVisible(False)
        self._load_btn.setEnabled(True)
        self._current_manga = manga
        self._current_chapters = chapters

        # Populate detail panel
        self._title_label.setText(manga.name)
        self._alt_label.setText(f"Alt: {manga.alt_name or manga.display_alt_name or ''}")
        self._status_label.setText(f"● {manga.status}")
        if manga.authors:
            self._author_label.setText(f"Author: {manga.authors[0].name}")
        else:
            self._author_label.setText("")
        self._rating_label.setText(f"★ {manga.display_rating or manga.rating}")

        summary = manga.summary.strip()
        if len(summary) > 400:
            summary = summary[:397] + "..."
        self._summary_label.setText(summary)

        # Genres as chips
        self._clear_genres()
        for g in manga.genres:
            chip = QLabel(g.name)
            chip.setStyleSheet(f"""
                background: {Colors.ACCENT_PRIMARY};
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
            """)
            chip.setFixedHeight(22)
            self._genres_layout.addWidget(chip)
        self._genres_layout.addStretch()

        # Cover
        self._load_cover(str(manga.cover))

        self._detail_panel.setVisible(True)

        # Chapters
        self._populate_chapters(chapters)

        # Show controls
        self._select_all_btn.setVisible(True)
        self._select_none_btn.setVisible(True)
        self._dl_selected_btn.setVisible(True)

        ToastManager.show_toast_cls(
            "Loaded",
            f"{manga.name} — {len(chapters)} chapters",
            "info",
        )

    @pyqtSlot(str)
    def _on_load_error(self, error_msg: str) -> None:
        self._loading_label.setVisible(False)
        self._load_btn.setEnabled(True)
        ToastManager.show_toast_cls("Error", error_msg, "error")

    def _populate_chapters(self, chapters: list[ChapterListItem]) -> None:
        self._clear_chapter_list()

        for ch in chapters:
            row = _ChapterRow(ch, self._current_manga.slug, self._current_manga.name)
            row.download_requested.connect(self._on_chapter_download)
            # Insert before stretch
            self._chapter_list_layout.insertWidget(
                self._chapter_list_layout.count() - 1, row
            )

        # Select all by default
        QTimer.singleShot(50, self._on_select_all)

    def _clear_genres(self) -> None:
        while self._genres_layout.count() > 0:
            item = self._genres_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _load_cover(self, url: str) -> None:
        self._cover_label.setText("⏳")
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._cover_thread = QThread()
        self._cover_worker = _CoverLoader(url)
        self._cover_worker.moveToThread(self._cover_thread)
        self._cover_thread.started.connect(self._cover_worker.run)
        self._cover_worker.loaded.connect(self._on_cover_loaded)
        self._cover_worker.loaded.connect(self._cover_thread.quit)
        self._cover_thread.finished.connect(self._cover_worker.deleteLater)
        self._cover_thread.finished.connect(self._cover_thread.deleteLater)
        self._cover_thread.start()

    @pyqtSlot(bytes)
    def _on_cover_loaded(self, data: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            pixmap = pixmap.scaled(
                140, 200,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._cover_label.setPixmap(pixmap)

    def _on_chapter_download(
        self,
        manga_slug: str,
        manga_name: str,
        chapter_slug: str,
        chapter_name: str,
        chapter_id: str,
    ) -> None:
        settings = Settings()
        fmt = settings.get("export_format", "cbz")
        dl_dir = settings.get("download_dir", "downloads")

        task = DownloadTask(
            manga_slug=manga_slug,
            manga_name=manga_name,
            chapter_slug=chapter_slug,
            chapter_name=chapter_name,
            chapter_id=chapter_id,
            images=[],
            format=fmt,
            output_dir=dl_dir,
            delete_after=settings.get("delete_images_after_export", True),
            status=DownloadStatus.QUEUED,
        )
        self.download_started.emit(task)
        ToastManager.show_toast_cls("Queued", f"{manga_name} — {chapter_name}", "success")

    @staticmethod
    def _extract_slug(text: str) -> Optional[str]:
        """Extract manga slug from URL or plain text."""
        text = text.strip()
        if not text:
            return None

        # Match mangak.io/<slug> or mangak.io/<slug>/something
        m = re.search(r"mangak\.io/([a-z0-9-]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).lower()

        # Plain slug
        if re.match(r"^[a-z0-9-]+$", text, re.IGNORECASE):
            return text.lower()

        return None

    # ── Public helper ──

    def load_slug(self, slug: str) -> None:
        """Load a manga by slug programmatically (called from search tab)."""
        self._url_input.setText(slug)
        self._load_manga(slug)
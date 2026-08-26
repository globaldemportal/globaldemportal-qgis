"""The sidebar's building blocks.

Each class corresponds to one CSS class in the web app - ``SectionTitle`` is
``.section-title``, ``ToolCard`` is ``.tool-btn``, and so on.  Anything QSS
cannot express (uppercasing, letter spacing, hover repaints on a QFrame) is done
here so that ``theme.py`` stays a faithful transcription of the stylesheet.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QCursor, QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .theme import C, px, set_px


def _spaced(widget, spacing):
    """CSS letter-spacing, which QSS does not implement."""
    font = widget.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    widget.setFont(font)


def hline(color=None):
    """A 1px rule - the CSS borders that separate the sidebar's bands."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: %s; border: none;" % (color or C["border"]))
    return line


class SectionTitle(QLabel):
    """``.section-title`` - 10px, 600, uppercase, 0.8px tracking."""

    def __init__(self, text, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionTitle")
        _spaced(self, 0.8)


class OptionLabel(QLabel):
    """``.option-label`` - the small 'Type' / 'Source' captions."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("optionLabel")


class StatItem(QWidget):
    """One cell of ``.stats-bar``: a big accent number over an uppercase caption."""

    def __init__(self, value, label, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(8), px(11), px(8), px(11))
        layout.setSpacing(px(3))

        self.value = QLabel(value)
        self.value.setObjectName("statValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label = QLabel(label.upper())
        self.label.setObjectName("statLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_px(self.label, 9.5)                 # .stat-label font-size
        _spaced(self.label, 0.6)

        layout.addWidget(self.value)
        layout.addWidget(self.label)

    def set_value(self, text):
        self.value.setText(text)


class TabButton(QToolButton):
    """``.tab`` - flat, checkable, with the 2px accent underline when active."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("tab")
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class ToolCard(QToolButton):
    """``.tool-btn`` - icon above label, checkable, one of a mutually exclusive set.

    The icon is re-rendered on toggle because QSS cannot recolour a QIcon.
    """

    def __init__(self, icon_name, text, parent=None):
        super().__init__(parent)
        self.setObjectName("toolBtn")
        self._icon_name = icon_name
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIconSize(self._icon_size())
        self._repaint_icon(False)
        self.toggled.connect(self._repaint_icon)

    def _icon_size(self):
        from qgis.PyQt.QtCore import QSize

        return QSize(px(18), px(18))  # .tool-btn svg { width/height: 18px }

    def _repaint_icon(self, checked):
        self.setIcon(icons.icon(self._icon_name, px(18),
                                C["accent_soft"] if checked else C["muted"]))


class KeyChip(QWidget):
    """One ``C Click`` / ``Esc Reset`` pair under the tool grid."""

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(px(4))

        chip = QLabel(key)
        chip.setObjectName("keyChip")
        text = QLabel(label)
        text.setObjectName("hintText")

        layout.addWidget(chip)
        layout.addWidget(text)


class ResOption(QFrame):
    """``.res-option`` - the 30m / 90m pair.  Checkable, exclusive, hover-lit."""

    clicked = pyqtSignal()

    def __init__(self, value, label, parent=None):
        super().__init__(parent)
        self.setObjectName("resOption")
        self.setProperty("active", "false")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(8), px(10), px(8), px(10))
        layout.setSpacing(px(2))

        self.value = QLabel(value)
        self.value.setObjectName("resValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel(label)
        self.label.setObjectName("resLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.value)
        layout.addWidget(self.label)

    def set_active(self, active):
        # A dynamic property drives the [active="true"] rule; Qt needs to be told
        # to re-evaluate the stylesheet for the change to show.
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class UploadArea(QFrame):
    """``.upload-area`` - the dashed GeoJSON drop zone, click or drag & drop."""

    activated = pyqtSignal()
    dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("uploadArea")
        self.setAcceptDrops(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(12), px(18), px(12), px(18))
        layout.setSpacing(0)

        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("upload", px(22), C["dim"]))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text = QLabel("Click to browse or drag & drop")
        text.setObjectName("uploadText")
        set_px(text, 11.5)                      # .upload-area font-size
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("GeoJSON Polygon / MultiPolygon · Max 25 MB")
        hint.setObjectName("uploadHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(glyph)
        layout.addSpacing(px(7))
        layout.addWidget(text)
        layout.addSpacing(px(3))
        layout.addWidget(hint)

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 - Qt naming
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".geojson", ".json")):
                self.dropped.emit(path)
                event.acceptProposedAction()
                return

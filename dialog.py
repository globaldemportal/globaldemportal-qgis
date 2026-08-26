"""The download dialog - this plugin's counterpart of the page's modal.

The web app's modal offers copyable URLs, a wget script and a Python snippet,
because a browser cannot write to a folder.  A QGIS plugin can, so this asks the
two questions a browser never could - where to put the files, and in which CRS -
and then does the work.  It also offers what only a desktop GIS can: mosaicking
the tiles into one raster, levelling them to each other first, and rendering the
result with a single stretched ramp (see :mod:`mosaic`).

The chrome (#1e293b panel, #334155 rules, 12px radius, the same type scale)
follows ``.modal`` in the stylesheet.
"""

import os

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)
from qgis.gui import QgsProjectionSelectionWidget

from . import paths
from .products import format_size
from .theme import C, px, scale_lengths, set_px


class DownloadDialog(QDialog):
    """Confirm a download: folder, CRS, whether to add the layers to the project."""

    def __init__(self, product, selection, parent=None):
        super().__init__(parent)
        self.product = product
        self.selection = selection
        self.setWindowTitle("Download DEM data")
        self.setObjectName("downloadDialog")
        self.setMinimumWidth(px(560))

        # Every rule is scoped to a *direct* child of this dialog.
        #
        # A Qt stylesheet applies to its owner and to everything below it in the
        # object hierarchy, dialogs included - and QGIS parents the Coordinate
        # Reference System Selector to the projection widget inside this dialog.
        # Anything unscoped here therefore repaints QGIS's own selector, and a
        # bare ``QWidget { color: ... }`` rule left it unreadable.  Two things
        # keep that from happening, and both are needed: the ``>`` combinator
        # below, which keeps these rules on the widgets this dialog creates
        # itself, and the dialog being parented to the QGIS main window rather
        # than to the panel, so the panel's own sheet is not inherited either
        # (see ``PortalPanel._start_download``).
        self.setStyleSheet(scale_lengths("""
            QDialog#downloadDialog { background: %s; }
            #downloadDialog > QLabel { color: %s; font-size: 12px; }
            #downloadDialog > QLabel#modalTitle {
                font-size: 15px; font-weight: 600; color: %s;
            }
            #downloadDialog > QLabel#modalSubtitle { font-size: 11px; color: %s; }
            #downloadDialog > QLabel#optionLabel { font-size: 11px; color: %s; }
            #downloadDialog > QLineEdit#pathInput {
                background: %s; border: 1px solid %s; border-radius: 6px;
                padding: 7px 10px; color: %s; font-size: 12px;
            }
            #downloadDialog > QLineEdit#pathInput:focus { border: 1px solid %s; }
            #downloadDialog > QCheckBox { font-size: 12px; color: %s; }
            #downloadDialog > QPushButton#searchBtn {
                background: %s; border: none; border-radius: 6px;
                padding: 7px 13px; color: #ffffff; font-size: 12px; font-weight: 500;
            }
            #downloadDialog > QPushButton#searchBtn:hover { background: %s; }
            #downloadDialog > QPushButton#btnPrimary {
                background: %s; border: none; border-radius: 8px; color: #ffffff;
                font-size: 14px; font-weight: 600; padding: 11px;
            }
            #downloadDialog > QPushButton#btnPrimary:hover { background: %s; }
            #downloadDialog > QPushButton#btnSecondary {
                background: transparent; border: 1px solid %s; border-radius: 8px;
                color: %s; font-size: 13px; font-weight: 500; padding: 9px;
            }
            #downloadDialog > QPushButton#btnSecondary:hover {
                border: 1px solid %s; color: %s;
            }
            #downloadDialog > QFrame#rule { background: %s; border: none; }
        """ % (C["bg_panel"], C["muted"], C["text"], C["dim"], C["dim"],
               C["bg_deep"], C["border"], C["text"], C["accent"], C["muted"],
               C["accent_deep"],
               C["accent_hover"], C["accent"], C["accent_hover"], C["border"],
               C["muted"], C["dimmer"], C["text"], C["border"])))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(22), px(18), px(22), px(18))  # .modal-header
        layout.setSpacing(px(14))

        layout.addLayout(self._header())
        layout.addWidget(self._rule())
        layout.addLayout(self._folder_row())
        layout.addLayout(self._crs_row())

        self.load_check = QCheckBox("Add the downloaded rasters to the project")
        self.load_check.setChecked(paths.auto_load())
        layout.addWidget(self.load_check)

        self.keep_check = QCheckBox("Keep the original files after reprojecting")
        self.keep_check.setChecked(paths.keep_originals())
        self.keep_check.setEnabled(bool(self.crs_widget.crs().isValid()))
        layout.addWidget(self.keep_check)

        layout.addWidget(self._rule())
        layout.addLayout(self._mosaic_options())

        self.status = QLabel("")
        self.status.setObjectName("modalBody")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addStretch(1)
        layout.addWidget(self._rule())
        layout.addLayout(self._buttons())

    # ── pieces ───────────────────────────────────────────────────────────
    def _header(self):
        column = QVBoxLayout()
        column.setSpacing(px(2))
        count = len(self.selection)
        title = QLabel("Download %d %s"
                       % (count, "clip" if self.product.mosaic
                          else "tile" if count == 1 else "tiles"))
        title.setObjectName("modalTitle")
        column.addWidget(title)

        if self.product.mosaic:
            detail = ("%s is a single %d GB Cloud-Optimized GeoTIFF. Only the "
                      "selected window is read, over HTTP range requests - the "
                      "whole file is never fetched."
                      % (self.product.label.split(" - ")[0],
                         self.product.source_size_gb))
        else:
            detail = "%s · about %s from the OpenTopography mirror" % (
                self.product.label, format_size(len(self.selection)
                                                * self.product.size_mb))
        subtitle = QLabel(detail)
        subtitle.setObjectName("modalSubtitle")
        subtitle.setWordWrap(True)
        set_px(subtitle, 11)
        column.addWidget(subtitle)
        return column

    @staticmethod
    def _rule():
        line = QFrame()
        line.setObjectName("rule")
        line.setFixedHeight(1)
        return line

    def _folder_row(self):
        column = QVBoxLayout()
        column.setSpacing(px(5))
        label = QLabel("Output folder")
        label.setObjectName("optionLabel")
        column.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(px(7))
        self.folder_input = QLineEdit(paths.download_dir())
        self.folder_input.setObjectName("pathInput")
        browse = QPushButton("Browse…")
        browse.setObjectName("searchBtn")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder_input, 1)
        row.addWidget(browse)
        column.addLayout(row)
        return column

    def _crs_row(self):
        column = QVBoxLayout()
        column.setSpacing(px(5))
        label = QLabel("Coordinate reference system")
        label.setObjectName("optionLabel")
        column.addWidget(label)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setOptionVisible(
            QgsProjectionSelectionWidget.CrsOption.CrsNotSet, True)
        stored = paths.target_crs()
        if stored:
            from qgis.core import QgsCoordinateReferenceSystem

            self.crs_widget.setCrs(QgsCoordinateReferenceSystem(stored))
        self.crs_widget.crsChanged.connect(self._on_crs_changed)
        self._style_crs_widget()
        column.addWidget(self.crs_widget)

        hint = QLabel("The portal serves EPSG:4326. Leave this unset to keep the "
                      "data exactly as downloaded - no warp, no resampling.")
        hint.setObjectName("modalSubtitle")
        hint.setWordWrap(True)
        set_px(hint, 11)
        column.addWidget(hint)
        return column

    def _mosaic_options(self):
        """Merging, levelling and colour - the part a browser could not offer.

        Merging is only meaningful for more than one file, so it is disabled
        (with the reason in the tooltip) for a single tile and for the two COG
        products, which arrive as one clip by definition.  Levelling hangs off
        merging because it only makes sense as a step before it.
        """
        column = QVBoxLayout()
        column.setSpacing(px(6))

        title = QLabel("After downloading")
        title.setObjectName("optionLabel")
        column.addWidget(title)

        mergeable = not self.product.mosaic and len(self.selection) > 1
        self.merge_check = QCheckBox("Merge the tiles into a single GeoTIFF")
        self.merge_check.setChecked(paths.merge_tiles() and mergeable)
        self.merge_check.setEnabled(mergeable)
        if not mergeable:
            self.merge_check.setToolTip(
                "This download is a single file already."
                if self.product.mosaic else
                "Select more than one tile to merge.")
        column.addWidget(self.merge_check)

        self.balance_check = QCheckBox(
            "Match elevations across the tile seams before merging")
        self.balance_check.setChecked(paths.balance_seams())
        self.balance_check.setToolTip(
            "Measures every shared edge and shifts each tile by the amount that "
            "best closes all the seams at once. Tiles from one mission usually "
            "agree already, and then nothing is rewritten.")
        column.addWidget(self.balance_check)

        self.ramp_check = QCheckBox("Render it with one balanced elevation ramp")
        self.ramp_check.setChecked(paths.colour_ramp())
        self.ramp_check.setToolTip(
            "Excludes void values and clips to the 0.1st-99.9th percentile, so "
            "one spike cannot flatten the rest of the range into a single shade.")
        column.addWidget(self.ramp_check)

        self.merge_check.toggled.connect(self.balance_check.setEnabled)
        self.balance_check.setEnabled(self.merge_check.isChecked())
        return column

    def _style_crs_widget(self):
        """Dress the CRS row like the rest of the dialog - child by child.

        QgsProjectionSelectionWidget shows a combo and a small button, and it
        parents QGIS's CRS Selector to *itself*.  A stylesheet set on the widget
        would therefore reach the selector as well; one set on each child cannot,
        because the selector is their sibling rather than their descendant.  That
        is the whole reason this styles the two children instead of the widget,
        and why the selector opens in QGIS's own theme, readable.
        """
        combo = self.crs_widget.findChild(QComboBox)
        if combo is not None:
            combo.setStyleSheet(scale_lengths("""
                QComboBox {
                    background: %s; border: 1px solid %s; border-radius: 6px;
                    padding: 7px 10px; color: %s; font-size: 12px;
                }
                QComboBox:focus, QComboBox:on { border: 1px solid %s; }
                QComboBox::drop-down { border: none; width: 20px; }
                QComboBox QAbstractItemView {
                    background: %s; border: 1px solid %s; color: %s;
                    selection-background-color: %s; outline: none;
                }
            """ % (C["bg_deep"], C["border"], C["text"], C["accent"],
                   C["bg_deep"], C["border"], C["text"], C["accent_deep"])))

        for button in self.crs_widget.findChildren(QToolButton):
            button.setStyleSheet(scale_lengths("""
                QToolButton {
                    background: %s; border: 1px solid %s; border-radius: 6px;
                    padding: 4px;
                }
                QToolButton:hover { border: 1px solid %s; }
            """ % (C["bg_deep"], C["border"], C["accent"])))

    def _on_crs_changed(self, crs):
        self.keep_check.setEnabled(crs.isValid())

    def _buttons(self):
        row = QHBoxLayout()
        row.setSpacing(px(7))
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("btnSecondary")
        self.cancel_btn.setFixedWidth(px(110))
        self.cancel_btn.clicked.connect(self.reject)
        self.start_btn = QPushButton("Download")
        self.start_btn.setObjectName("btnPrimary")
        self.start_btn.setFixedWidth(px(150))
        self.start_btn.clicked.connect(self.accept)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.start_btn)
        return row

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a download folder", self.folder_input.text())
        if chosen:
            self.folder_input.setText(chosen)

    # ── results ──────────────────────────────────────────────────────────
    def accept(self):
        folder = self.folder_input.text().strip()
        if not folder:
            self.status.setText("Choose a folder to download into.")
            return
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except OSError as exc:
            self.status.setText("That folder cannot be created: %s" % exc)
            return

        paths.set_download_dir(folder)
        paths.set_auto_load(self.load_check.isChecked())
        paths.set_keep_originals(self.keep_check.isChecked())
        paths.set_merge_tiles(self.merge_check.isChecked())
        paths.set_balance_seams(self.balance_check.isChecked())
        paths.set_colour_ramp(self.ramp_check.isChecked())
        crs = self.crs_widget.crs()
        paths.set_target_crs(crs.authid() if crs.isValid() else "")
        super().accept()

    @property
    def folder(self):
        return self.folder_input.text().strip()

    @property
    def crs_definition(self):
        crs = self.crs_widget.crs()
        return crs.authid() if crs.isValid() else ""

    @property
    def add_to_project(self):
        return self.load_check.isChecked()

    @property
    def keep_originals(self):
        return self.keep_check.isChecked()

    @property
    def merge_tiles(self):
        return self.merge_check.isChecked() and self.merge_check.isEnabled()

    @property
    def balance_seams(self):
        return self.balance_check.isChecked() and self.merge_tiles

    @property
    def colour_ramp(self):
        return self.ramp_check.isChecked()

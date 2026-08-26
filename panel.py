"""The panel itself - the web app's sidebar rebuilt out of Qt widgets.

The layout follows index.html band for band: header, stats, tabs, a scrolling
tab pane, the two action buttons, the credit footer, and the map filling the
rest.  Paddings are the CSS paddings (``.sidebar-header`` is 18/16/14, so the
header's contents margins are 16, 18, 16, 14) so that the two line up when you
put screenshots side by side.
"""

import json
import os

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QCursor, QDesktopServices, QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QSplitter,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask

from . import geometry as geom
from . import icons
from . import net
from . import layers as layer_utils
from . import paths
from .dialog import DownloadDialog
from .downloader import DownloadTask
from .maptools import ClickTool, PolygonTool, RectangleTool
from .mapview import PortalMap
from .products import (
    PRODUCTS,
    format_size,
    products_for,
    resolutions_for,
    tile_name,
)
from .theme import C, H, SIDEBAR_WIDTH, px, scale_lengths, set_px, stylesheet
from .widgets import (
    KeyChip,
    OptionLabel,
    ResOption,
    SectionTitle,
    StatItem,
    TabButton,
    ToolCard,
    UploadArea,
)

VERSION = "1.4"

#: The nine products, with the labels the page's <select> shows.
DSM_SOURCES = [
    ("SRTMGL1", "SRTM GL1 - 1 arc-sec (~30 m)"),
    ("NASADEM", "NASADEM - void-filled SRTM (~30 m)"),
    ("ALOS", "ALOS AW3D30 - JAXA (~30 m)"),
    ("COP30", "Copernicus GLO-30 - ESA (~30 m)"),
    ("MAPZEN", "Mapzen (Tilezen) - SRTM-derived (~30 m)"),
]
DTM_SOURCES = [
    ("ANADEM", "ANADEM - South America, ML (~30 m)"),
    ("GEDTM30", "GEDTM30 - global, ML (~30 m)"),
]

COVERAGE_ROWS = [
    ("SRTM GL1", "DSM", "~30 m", "56°S - 60°N"),
    ("NASADEM", "DSM", "~30 m", "56°S - 60°N"),
    ("ALOS AW3D30", "DSM", "~30 m", "82°S - 82°N"),
    ("Copernicus GLO-30", "DSM", "~30 m", "global"),
    ("Mapzen (Tilezen)", "DSM", "~30 m", "56°S - 60°N*"),
    ("ANADEM (ML)", "DTM", "~30 m", "South America"),
    ("GEDTM30 (ML)", "DTM", "~30 m", "global†"),
    ("SRTM GL3", "DSM", "~90 m", "56°S - 60°N"),
    ("Copernicus GLO-90", "DSM", "~90 m", "global"),
]


class SearchTask(QgsTask):
    """Look a country up on Nominatim without blocking the GUI.

    ``found`` carries (result-or-None, error-message).  Nominatim's usage policy
    asks for an identifying User-Agent, which net.get supplies.
    """

    found = pyqtSignal(object, str)

    def __init__(self, query):
        super().__init__("Looking up %s" % query, QgsTask.Flag.CanCancel)
        self.query = query
        self._item = None
        self._error = ""

    def run(self):
        import urllib.parse

        url = ("https://nominatim.openstreetmap.org/search?q=%s&format=json"
               "&polygon_geojson=1&limit=5&featuretype=country"
               % urllib.parse.quote(self.query))
        try:
            body = net.get(url, headers={"Accept-Language": "en"})
            entries = json.loads(body.decode("utf-8"))
        except Exception as exc:                     # noqa: BLE001 - offline, DNS, 429
            self._error = str(exc)
            return True
        # Prefer an administrative boundary, as the page does, then anything
        # that at least carries a geometry.
        for entry in entries:
            if entry.get("geojson") and (entry.get("type") == "administrative"
                                         or entry.get("class") == "boundary"):
                self._item = entry
                break
        else:
            for entry in entries:
                if entry.get("geojson"):
                    self._item = entry
                    break
        return True

    def finished(self, ok):  # noqa: N802 - QGIS API
        self.found.emit(self._item, self._error)


class PortalPanel(QWidget):
    """Sidebar + map, the whole panel."""

    #: message, Qgis.MessageLevel - the dock relays these to the QGIS message bar.
    notified = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setStyleSheet(stylesheet())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        self.map = PortalMap(self)
        # A floor for the map as well as the sidebar.  Without one the panel's
        # minimum width was just the sidebar's, so a narrow dock gave the map
        # zero pixels - the half you select tiles in vanished entirely rather
        # than the two halves sharing what space there was.
        self.map.setMinimumWidth(160)

        # A splitter rather than a fixed 320px column: the design is laid out
        # for 320, so that is the opening width, but the user can widen the
        # sidebar (useful for the tile chips and the datasets table) or narrow
        # it to give the map more room.  The handle is 1px, painted like the
        # border it replaces.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.map)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([SIDEBAR_WIDTH, 580])
        layout.addWidget(self.splitter)

        self._wire()

    # ── Sidebar ──────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        # Not a fixed width any more - the splitter drives it.  The floor keeps
        # the datasets table and the tool grid from being clipped.
        sidebar.setMinimumWidth(300)
        sidebar.setMaximumWidth(720)

        column = QVBoxLayout(sidebar)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        column.addWidget(self._build_header())
        column.addWidget(self._build_stats())
        column.addWidget(self._build_tabs())
        column.addWidget(self._build_pages(), 1)
        column.addWidget(self._build_actions())
        column.addWidget(self._build_footer())
        return sidebar

    def _build_header(self):
        header = QFrame()
        header.setObjectName("header")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(px(16), px(18), px(16), px(14))  # .sidebar-header
        layout.setSpacing(px(5))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(px(8))                        # h1 { gap: 8px }
        title = QLabel("🗻 Global DEM Portal")
        title.setObjectName("title")
        badge = QLabel("v" + VERSION)
        badge.setObjectName("versionBadge")
        row.addWidget(title)
        row.addWidget(badge)
        row.addStretch(1)

        subtitle = QLabel(
            "Select tiles on the map, then download DSM or DTM data as GeoTIFF"
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        set_px(subtitle, 11.5)   # .sidebar-header p

        layout.addLayout(row)
        layout.addWidget(subtitle)
        return header

    def _build_stats(self):
        stats = QFrame()
        stats.setObjectName("stats")
        stats.setFixedHeight(H["stats"])
        layout = QHBoxLayout(stats)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stat_tiles = StatItem("0", "Selected")
        self.stat_size = StatItem("—", "Est. Size")
        self.stat_res = StatItem("30m", "Resolution")

        for index, item in enumerate((self.stat_tiles, self.stat_size, self.stat_res)):
            if index:
                divider = QFrame()
                divider.setObjectName("statDivider")
                divider.setFixedWidth(1)
                layout.addWidget(divider)
            layout.addWidget(item, 1)
        return stats

    def _build_tabs(self):
        bar = QFrame()
        bar.setObjectName("tabBar")
        bar.setFixedHeight(H["tabBar"])
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = []
        for index, name in enumerate(("Tools", "Tiles", "Info")):
            button = TabButton(name)
            button.setFixedHeight(H["tab"])
            button.clicked.connect(lambda _, i=index: self.pages.setCurrentIndex(i))
            layout.addWidget(button, 1)
            self.tabs.append(button)
        self.tabs[0].setChecked(True)
        return bar

    def _build_pages(self):
        self.pages = QStackedWidget()
        for build in (self._page_tools, self._page_tiles, self._page_info):
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = build()
            page.setObjectName("tabPage")
            # No ceiling: the scroll area sizes the page to its viewport, and
            # the coverage table's own fixed width sets the floor.  Pinning a
            # maximum here would stop the pages following a widened sidebar.
            area.setWidget(page)
            self.pages.addWidget(area)
        return self.pages

    @staticmethod
    def _page_layout(page):
        layout = QVBoxLayout(page)
        layout.setContentsMargins(px(14), px(14), px(14), px(10))  # .tab-content
        layout.setSpacing(px(18))                    # .section margin-bottom
        return layout

    def _page_tools(self):
        """The Tools tab.

        Built with explicit gaps rather than a uniform layout spacing, because
        the CSS gaps are not uniform: a section is followed by 18px, a section
        title by 8px, the tool grid by 6px, the search row by 5px, and the
        Type/Resolution/Source wrappers by 10px.  Every number below was read
        off the live page with getBoundingClientRect(), which is why the two
        panes line up control for control instead of only band for band.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(px(14), px(14), px(14), px(10))  # .tab-content
        layout.setSpacing(0)

        def title(text):
            label = SectionTitle(text)
            label.setFixedHeight(px(13))             # .section-title line box
            layout.addWidget(label)
            layout.addSpacing(px(8))                 # .section-title margin-bottom

        def option(text):
            label = OptionLabel(text)
            label.setFixedHeight(px(14))             # .option-label line box
            layout.addWidget(label)
            layout.addSpacing(px(5))                 # .option-label margin-bottom

        # ── How to select tiles ──
        title("How to Select Tiles")
        grid = QGridLayout()
        grid.setSpacing(px(7))                       # .tool-grid gap
        grid.setContentsMargins(0, 0, 0, 0)
        self.tool_click = ToolCard("cursor", "Click Tiles")
        self.tool_rect = ToolCard("square", "Rectangle")
        self.tool_poly = ToolCard("polygon", "Polygon")
        self.tool_view = ToolCard("viewport", "Visible Area")
        for card in (self.tool_click, self.tool_rect, self.tool_poly, self.tool_view):
            card.setFixedHeight(H["toolBtn"])
        grid.addWidget(self.tool_click, 0, 0)
        grid.addWidget(self.tool_rect, 0, 1)
        grid.addWidget(self.tool_poly, 1, 0)
        grid.addWidget(self.tool_view, 1, 1)
        self.tool_click.setChecked(True)
        layout.addLayout(grid)
        layout.addSpacing(px(6))                     # .kbd-hint margin-top

        hints = QWidget()
        hints.setFixedHeight(px(13))                 # .kbd-hint line box
        hint_row = QHBoxLayout(hints)
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.setSpacing(px(10))                  # the &nbsp; between pairs
        hint_row.addStretch(1)
        for key, label in (("C", "Click"), ("R", "Rect"), ("P", "Polygon"),
                           ("Esc", "Reset")):
            hint_row.addWidget(KeyChip(key, label))
        hint_row.addStretch(1)
        layout.addWidget(hints)
        layout.addSpacing(px(18))                        # .section margin-bottom

        # ── Select by country ──
        title("Select by Country")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(px(7))                        # .search-row gap
        self.country_input = QLineEdit()
        self.country_input.setObjectName("searchInput")
        self.country_input.setPlaceholderText("e.g. Nepal, Kenya…")
        self.country_input.setFixedHeight(H["searchInput"])
        self.country_btn = QPushButton("Select")
        self.country_btn.setObjectName("searchBtn")
        self.country_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.country_btn.setFixedHeight(H["searchBtn"])
        row.addWidget(self.country_input, 1)
        row.addWidget(self.country_btn)
        layout.addLayout(row)
        layout.addSpacing(px(5))                     # #country-status margin-top

        self.country_status = QLabel("")
        self.country_status.setObjectName("hintText")
        self.country_status.setFixedHeight(px(15))   # #country-status min-height
        self.country_status.setWordWrap(True)
        layout.addWidget(self.country_status)
        layout.addSpacing(px(18))

        # ── Upload GeoJSON ──
        title("Upload GeoJSON Geometry")
        self.upload = UploadArea()
        self.upload.setFixedHeight(H["uploadArea"])
        layout.addWidget(self.upload)
        layout.addSpacing(px(18))

        # ── Dataset options ──
        title("Dataset Options")
        option("Type")
        self.type_select = QComboBox()
        self.type_select.setObjectName("productSelect")
        self.type_select.addItem("DSM - Digital Surface Model", "DSM")
        self.type_select.addItem("DTM - Digital Terrain Model (bare earth)", "DTM")
        self.type_select.setFixedHeight(H["productSelect"])
        layout.addWidget(self.type_select)
        layout.addSpacing(px(10))                        # the wrapper's margin-bottom

        option("Resolution")
        res_row = QHBoxLayout()
        res_row.setContentsMargins(0, 0, 0, 0)
        res_row.setSpacing(px(7))                    # .resolution-options gap
        self.res_30 = ResOption("30m", "1 arc-sec")
        self.res_90 = ResOption("90m", "3 arc-sec")
        for opt in (self.res_30, self.res_90):
            opt.setFixedHeight(H["resOption"])
            res_row.addWidget(opt, 1)
        self.res_30.set_active(True)
        self.res_30.clicked.connect(lambda: self._set_resolution(self.res_30))
        self.res_90.clicked.connect(lambda: self._set_resolution(self.res_90))
        layout.addLayout(res_row)
        layout.addSpacing(px(10))

        option("Source")
        self.source_select = QComboBox()
        self.source_select.setObjectName("productSelect")
        for key, label in DSM_SOURCES:
            self.source_select.addItem(label, key)
        self.source_select.setFixedHeight(H["productSelect"])
        layout.addWidget(self.source_select)
        layout.addSpacing(px(18))

        # ── View ──
        title("View")
        self.zoom_btn = QPushButton("  Zoom to Selection")
        self.zoom_btn.setObjectName("zoomSelectBtn")
        self.zoom_btn.setIcon(icons.icon("search", px(14), C["muted"]))
        self.zoom_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.zoom_btn.setFixedHeight(H["zoomBtn"])
        layout.addWidget(self.zoom_btn)

        layout.addStretch(1)
        return page

    def _page_tiles(self):
        """The Tiles tab: a count and one chip per selected tile, click to remove."""
        page = QWidget()
        layout = self._page_layout(page)

        self.tiles_count = QLabel("")
        self.tiles_count.setObjectName("tilesCount")
        self.tiles_count.setVisible(False)
        layout.addWidget(self.tiles_count)

        self.tiles_empty = QLabel("No tiles selected yet.\nUse the tools in the "
                                  "Tools tab to\nselect SRTM tiles on the map.")
        self.tiles_empty.setObjectName("tilesEmpty")
        self.tiles_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(px(24))                    # .tiles-empty padding
        layout.addWidget(self.tiles_empty)

        self.tiles_chips = QWidget()
        self.tiles_chip_grid = QGridLayout(self.tiles_chips)
        self.tiles_chip_grid.setContentsMargins(0, 0, 0, 0)
        self.tiles_chip_grid.setSpacing(px(4))       # .tiles-chips gap
        self.tiles_chips.setVisible(False)
        layout.addWidget(self.tiles_chips)

        layout.addStretch(1)
        return page

    def _page_info(self):
        page = QWidget()
        layout = self._page_layout(page)

        def heading(text):
            label = QLabel(text)
            label.setObjectName("infoHeading")
            return label

        def body(text):
            label = QLabel(text)
            label.setObjectName("infoBody")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            return label

        about = QVBoxLayout()
        about.setSpacing(px(6))
        about.addWidget(heading("About"))
        about.addWidget(body(
            "Select 1°×1° tiles and download open global elevation data. Nine free "
            "DEMs are available under <b style='color:%s'>Dataset Options</b> - pick "
            "a <b style='color:%s'>Type</b> (DSM: surface, includes canopy/buildings; "
            "or DTM: bare-earth terrain) and then a <b style='color:%s'>Source</b>. "
            "Coverage differs by mission, so the red boundary lines on the map move "
            "when you switch products." % (C["text"], C["text"], C["text"])))
        layout.addLayout(about)

        datasets = QVBoxLayout()
        datasets.setSpacing(px(7))
        datasets.addWidget(heading("Datasets"))
        datasets.addWidget(self._coverage_table())
        footnote = body(
            "*Mapzen's own data is truly global, but this portal caps it to the SRTM "
            "band so it only offers tiles already on the land-mask grid.<br>"
            "\u2020GEDTM30 fills gaps other DEMs miss (Central Africa, SE Asia, "
            "boreal forest) but is an ensemble product, not a verified survey - check "
            "residuals near steep terrain.")
        set_px(footnote, 10.5)
        datasets.addWidget(footnote)
        layout.addLayout(datasets)

        for title_text, text in (
            ("Sources",
             "<b style='color:%s'>SRTM / NASADEM</b> - NASA/USGS radar (2000).<br>"
             "<b style='color:%s'>ALOS AW3D30</b> - JAXA optical stereo (2006-2011)."
             "<br><b style='color:%s'>Copernicus GLO-30/90</b> - ESA's open DEM "
             "edited from Airbus <i>TanDEM-X</i> radar (2011-2015).<br>"
             "<b style='color:%s'>Mapzen (Tilezen)</b> - SRTM-derived \u201cskadi\u201d "
             "tiles from the defunct Mapzen project, a static AWS Open Data snapshot; "
             "not updated since 2016.<br>"
             "<b style='color:%s'>ANADEM</b> - machine-learning bare-earth model for "
             "South America, trained on Copernicus GLO-30, GEDI lidar and Landsat-8/"
             "Sentinel-2 (<a style='color:%s' href='https://doi.org/10.5069/G9736P4G'>"
             "DOI</a>).<br>"
             "<b style='color:%s'>GEDTM30</b> - global ensemble ML terrain model "
             "trained on ~30 billion GEDI/ICESat-2 lidar points plus Copernicus GLO-30 "
             "and ALOS AW3D30 (<a style='color:%s' href='https://doi.org/10.5069/"
             "G9BV7DT1'>DOI</a>). Both released 2025."
             % ((C["text"],) * 5 + (C["link"], C["text"], C["link"]))),
            ("Tile Naming Convention",
             "Tiles are named by their lower-left corner. <b style='color:%s'>N37W122"
             "</b> = lat 37-38\u00b0N, lon 122-121\u00b0W. Each tile covers exactly "
             "1\u00b0\u00d71\u00b0. Filenames on disk follow each dataset's own "
             "convention." % C["text"]),
            ("Coverage Limits",
             "The grid is drawn only over cells that actually have a DEM tile, so open "
             "ocean stays blank and cannot be selected. Red dashed lines mark the "
             "selected product's latitude limit; global products show none. "
             "<b style='color:%s'>ANADEM</b> is also longitude-restricted to South "
             "America - cells outside it simply have no grid." % C["text"]),
            ("How downloads work",
             "Tiled products are fetched one file per selected cell, four at a time, "
             "straight from the OpenTopography mirror - no account, no API key. "
             "<b style='color:%s'>ANADEM</b> and <b style='color:%s'>GEDTM30</b> are "
             "single Cloud-Optimized GeoTIFFs of 66 GB and 403 GB; for those, only the "
             "selected window is read, over HTTP range requests. You choose the output "
             "folder and, optionally, a CRS to reproject into - leaving the CRS unset "
             "keeps the data exactly as published."
             % (C["text"], C["text"])),
            ("Merging and colour",
             "The download dialog can <b style='color:%s'>merge</b> the tiles into one "
             "tiled, overviewed GeoTIFF, with the fill value declared as nodata so "
             "voids stop dragging the stretch down. <b style='color:%s'>Matching "
             "elevations</b> measures every shared edge first and shifts each tile by "
             "the amount that best closes all the seams at once - tiles from one "
             "mission normally agree already, and the message bar says so rather than "
             "rewriting them. The <b style='color:%s'>elevation ramp</b> clips to the "
             "0.1st-99.9th percentile and gives every layer the same range, so a plateau "
             "tile and a mountain tile are shaded on one scale instead of each on its "
             "own. The downloaded tiles are always kept."
             % (C["text"], C["text"], C["text"])),
        ):
            section = QVBoxLayout()
            section.setSpacing(px(6))
            section.addWidget(heading(title_text))
            section.addWidget(body(text))
            layout.addLayout(section)

        layout.addStretch(1)
        return page

    def _coverage_table(self):
        """``.coverage-table`` - a grid of labels rather than a QTableWidget, so the
        cell padding and the 1px row rules can match the CSS exactly.

        The column widths are the ones the browser's table layout settles on for
        this content inside a 287px column, measured with getBoundingClientRect().
        Long product names wrap to a second line there, so the cells wrap here too.
        The widths and the cell type go through the panel scale like everything
        else, so the table keeps its proportions and stops being the widest thing
        in the sidebar.
        """
        widths = tuple(px(width) for width in (107, 45, 50, 86))

        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)

        headers = ("Product", "Type", "Res", "Extent")
        for column, text in enumerate(headers):
            cell = QLabel(text.upper())
            cell.setFixedWidth(widths[column])
            cell.setStyleSheet(scale_lengths(
                "background: %s; color: %s; font-size: 10px; font-weight: 500;"
                " padding: 5px 10px;" % (C["bg_deep"], C["dim"])
            ))
            grid.addWidget(cell, 0, column)

        for row, values in enumerate(COVERAGE_ROWS, start=1):
            last = row == len(COVERAGE_ROWS)
            for column, text in enumerate(values):
                cell = QLabel(text)
                cell.setFixedWidth(widths[column])
                # Only the product name wraps, as in the browser.  Qt's metrics
                # are a shade wider than Chromium's, so the three narrow columns
                # get 7px of side padding instead of 10 - otherwise "~30 m"
                # wraps onto a second line and the row doubles in height.
                cell.setWordWrap(column == 0)
                cell.setAlignment(Qt.AlignmentFlag.AlignLeft
                                  | Qt.AlignmentFlag.AlignVCenter)
                cell.setStyleSheet(scale_lengths(
                    "color: %s; font-size: 11px; padding: 6px %dpx;%s"
                    % (C["muted"], 10 if column == 0 else 7,
                       "" if last else " border-bottom: 1px solid %s;" % C["bg_panel"])
                ))
                grid.addWidget(cell, row, column)
        table.setFixedWidth(sum(widths))
        return table

    # ── Footer bands ─────────────────────────────────────────────────────
    def _build_actions(self):
        actions = QFrame()
        actions.setObjectName("actions")
        layout = QVBoxLayout(actions)
        layout.setContentsMargins(px(14), px(13), px(14), px(13))  # .action-buttons
        layout.setSpacing(px(7))

        self.btn_download = QPushButton("  Get Downloads")
        self.btn_download.setObjectName("btnPrimary")
        self.btn_download.setIcon(icons.icon("download", px(15), C["dimmer"], 2.5))
        self.btn_download.setEnabled(False)
        self.btn_download.setFixedHeight(H["btnPrimary"])
        set_px(self.btn_download, 13.5)          # .btn-primary font-size

        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.setObjectName("btnSecondary")
        self.btn_clear.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clear.setFixedHeight(H["btnSecondary"])
        set_px(self.btn_clear, 12.5)             # .btn-secondary font-size

        # Hidden until something is running.  Before this the only sign of life
        # was the button reading "Downloading...", which says nothing about
        # whether a 300-tile job is a minute or an hour from finishing, and left
        # no way to stop it short of QGIS's task manager.
        self.progress = QProgressBar()
        self.progress.setObjectName("downloadProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(px(6))
        self.progress.setVisible(False)

        layout.addWidget(self.btn_download)
        layout.addWidget(self.progress)
        layout.addWidget(self.btn_clear)
        return actions

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(H["footer"])
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(px(14), px(9), px(14), px(11))  # .sidebar-footer
        layout.setSpacing(px(4))

        credit = QLabel("© 2026 Sharad Gupta")
        credit.setObjectName("footerText")
        set_px(credit, 10.5)     # .sidebar-footer
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(credit)

        links = QHBoxLayout()
        links.setSpacing(px(14))                     # .footer-links gap
        links.addStretch(1)
        for name, label, url in (
            ("github", "GitHub", "https://github.com/globaldemportal"),
            ("linkedin", "LinkedIn", "https://www.linkedin.com/in/sharadkumargupta/"),
            ("mail", "Email", "mailto:sharadgupta27@gmail.com"),
        ):
            button = QToolButton()
            button.setObjectName("footerLink")
            button.setText(" " + label)
            button.setIcon(icons.icon(name, px(12), C["muted"]))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            button.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addWidget(button)
        links.addStretch(1)
        layout.addLayout(links)
        return footer

    # ══════════════════════════════════════════════════════════════════════
    # Behaviour
    # ══════════════════════════════════════════════════════════════════════
    def _wire(self):
        """Connect the widgets, build the map tools, and select a starting product."""
        self.selection = set()                       # {(lat, lon)}
        self.kind = "DSM"
        self.res = "1arc"
        self.product = PRODUCTS["SRTMGL1"]
        self.boundary_band = None
        self._task = None
        self._search_task = None

        # Map tools, one per selection mode.
        canvas = self.map.canvas
        self.tool_click_obj = ClickTool(canvas)
        self.tool_rect_obj = RectangleTool(canvas)
        self.tool_poly_obj = PolygonTool(canvas)
        self.tool_click_obj.toggled_cell.connect(self._toggle_cell)
        self.tool_rect_obj.selected_rect.connect(self._select_rect)
        self.tool_poly_obj.selected_polygon.connect(self._select_polygon)

        self.tool_click.clicked.connect(lambda: self._set_tool("click"))
        self.tool_rect.clicked.connect(lambda: self._set_tool("rect"))
        self.tool_poly.clicked.connect(lambda: self._set_tool("polygon"))
        self.tool_view.clicked.connect(self._select_visible)

        self.type_select.currentIndexChanged.connect(self._on_type_changed)
        self.source_select.currentIndexChanged.connect(self._on_source_changed)

        self.country_btn.clicked.connect(self._search_country)
        self.country_input.returnPressed.connect(self._search_country)
        self.upload.activated.connect(self._browse_geojson)
        self.upload.dropped.connect(self._load_geojson)

        self.zoom_btn.clicked.connect(self._zoom_to_selection)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_download.clicked.connect(self._download_clicked)

        # The page's keyboard shortcuts.
        for key, handler in (("C", lambda: self._set_tool("click", True)),
                             ("R", lambda: self._set_tool("rect", True)),
                             ("P", lambda: self._set_tool("polygon", True)),
                             ("Esc", self._clear)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

        self._rebuild_sources()
        self._set_tool("click")
        self._apply_product()

    # ── product interlock ────────────────────────────────────────────────
    def _on_type_changed(self):
        """DSM/DTM changes which resolutions and sources exist (DTM is 30m only)."""
        self.kind = self.type_select.currentData()
        allowed = resolutions_for(self.kind)
        if self.res not in allowed:
            self.res = allowed[0]
        self.res_30.set_active(self.res == "1arc")
        self.res_90.set_active(self.res == "3arc")
        # A resolution with no product of this type is shown greyed, as on the page.
        self.res_90.setEnabled("3arc" in allowed)
        self._rebuild_sources()

    def _set_resolution(self, chosen):
        wanted = "1arc" if chosen is self.res_30 else "3arc"
        if wanted not in resolutions_for(self.kind):
            return
        self.res = wanted
        self.res_30.set_active(chosen is self.res_30)
        self.res_90.set_active(chosen is self.res_90)
        self._rebuild_sources()

    def _rebuild_sources(self):
        """Refill the Source menu for the current Type + Resolution."""
        available = products_for(self.kind, self.res)
        self.source_select.blockSignals(True)
        self.source_select.clear()
        for product in available:
            self.source_select.addItem(product.label, product.key)
        self.source_select.blockSignals(False)
        if available:
            keys = [p.key for p in available]
            index = keys.index(self.product.key) if self.product.key in keys else 0
            self.source_select.setCurrentIndex(index)
            self.product = available[index]
        self._apply_product()

    def _on_source_changed(self):
        key = self.source_select.currentData()
        if key:
            self.product = PRODUCTS[key]
            self._apply_product()

    def _apply_product(self):
        """Push the product to the map and drop any selection it cannot serve."""
        self.map.overlay.set_product(self.product)
        outside = {cell for cell in self.selection
                   if not self.product.covers(cell[0], cell[1])}
        if outside:
            self.selection -= outside
            self._notify("%d tile%s dropped - outside %s coverage"
                         % (len(outside), "" if len(outside) == 1 else "s",
                            self.product.key))
        self._update_ui()

    # ── tools ────────────────────────────────────────────────────────────
    def _set_tool(self, name, sync_buttons=False):
        tools = {"click": (self.tool_click_obj, self.tool_click),
                 "rect": (self.tool_rect_obj, self.tool_rect),
                 "polygon": (self.tool_poly_obj, self.tool_poly)}
        tool, button = tools[name]
        self.map.canvas.setMapTool(tool)
        if sync_buttons or not button.isChecked():
            button.setChecked(True)

    def _select_visible(self):
        """'Visible Area' - every drawable cell in the current view."""
        extent = self.map.geographic_extent()
        if extent is None:
            return
        south, west, north, east = extent
        found = geom.tiles_in_rect(south, west, north, east,
                                   self.product, self.map.overlay.mask)
        self._add(found, "visible area")
        # Leave the previous tool active; the page treats this as a one-shot action.
        self.tool_view.setChecked(False)
        {"click": self.tool_click, "rect": self.tool_rect,
         "polygon": self.tool_poly}[self._active_tool_name()].setChecked(True)

    def _active_tool_name(self):
        current = self.map.canvas.mapTool()
        if current is self.tool_rect_obj:
            return "rect"
        if current is self.tool_poly_obj:
            return "polygon"
        return "click"

    def _toggle_cell(self, lat, lon):
        if not self.map.overlay.mask.has(lat, lon):
            return
        if not self.product.covers(lat, lon):
            self._notify("%s is outside %s coverage"
                         % (tile_name(lat, lon), self.product.key))
            return
        if (lat, lon) in self.selection:
            self.selection.discard((lat, lon))
        else:
            self.selection.add((lat, lon))
        self._update_ui()

    def _select_rect(self, south, west, north, east):
        self._add(geom.tiles_in_rect(south, west, north, east,
                                     self.product, self.map.overlay.mask), "rectangle")

    def _select_polygon(self, geometry):
        self._add(geom.tiles_from_geometry(geometry, self.product,
                                           self.map.overlay.mask), "polygon")

    def _add(self, cells, what):
        added = cells - self.selection
        self.selection |= cells
        self._update_ui()
        if added:
            self._notify("%d tile%s added from the %s"
                         % (len(added), "" if len(added) == 1 else "s", what))
        else:
            self._notify("No drawable tiles in that %s" % what)

    def _clear(self):
        self.selection.clear()
        self.tool_poly_obj.reset()
        self.map.clear_boundary()
        self.country_status.setText("")
        self._update_ui()

    # ── country search ───────────────────────────────────────────────────
    def _search_country(self):
        query = self.country_input.text().strip()
        if not query:
            return
        if query.lower() in ("india", "bharat", "hindustan") or query == "भारत":
            # India uses the official claimed boundary, not OSM's de-facto line -
            # the same special case the web app makes, for the same reason.
            boundary = geom.india_boundary()
            found = geom.tiles_from_geometry(boundary, self.product,
                                             self.map.overlay.mask)
            self.selection |= found
            self.map.show_boundary(boundary)
            self.map.zoom_to_geometry(boundary)
            self._update_ui()
            self._status_ok("%d tiles selected for India (official boundary)"
                            % len(found))
            return

        # Nominatim can take seconds, and a blocking network call on the GUI
        # thread freezes the whole of QGIS - so the lookup runs as a task.
        self.country_status.setText("Searching\u2026")
        self.country_btn.setEnabled(False)
        task = SearchTask(query)
        task.found.connect(self._on_country_found)
        self._search_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_country_found(self, item, error):
        self.country_btn.setEnabled(True)
        self._search_task = None
        if error:
            self._status_error("Search failed - %s" % error)
            return
        if not item:
            self._status_error("Country not found")
            return

        boundary = geom.geometry_from_geojson(item.get("geojson"))
        if boundary is None or boundary.isEmpty():
            self._status_error("No boundary returned for that place")
            return
        found = geom.tiles_from_geometry(boundary, self.product,
                                         self.map.overlay.mask)
        self.selection |= found
        self.map.show_boundary(boundary)
        self.map.zoom_to_geometry(boundary)
        self._update_ui()
        self._status_ok("%d tiles selected for %s"
                        % (len(found),
                           item.get("display_name", "").split(",")[0] or "that place"))

    def _status_ok(self, message):
        self.country_status.setText(
            "<span style='color:#22c55e'>\u2713 %s</span>" % message)
        self._notify(message)

    def _status_error(self, message):
        self.country_status.setText("<span style='color:#ef4444'>%s</span>" % message)

    # ── GeoJSON upload ───────────────────────────────────────────────────
    def _browse_geojson(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a GeoJSON polygon", paths.download_dir(),
            "GeoJSON (*.geojson *.json)")
        if path:
            self._load_geojson(path)

    def _load_geojson(self, path):
        try:
            if os.path.getsize(path) > 25 * 1024 * 1024:
                self._notify("File too large (max 25 MB)", Qgis.MessageLevel.Warning)
                return
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            boundary = geom.geometry_from_geojson(data)
        except Exception as exc:                     # noqa: BLE001 - bad JSON, encoding
            self._notify("Could not read that file: %s" % exc,
                         Qgis.MessageLevel.Warning)
            return

        if boundary is None or boundary.isEmpty():
            self._notify("No Polygon or MultiPolygon in that file",
                         Qgis.MessageLevel.Warning)
            return
        found = geom.tiles_from_geometry(boundary, self.product,
                                         self.map.overlay.mask)
        self.selection |= found
        self.map.show_boundary(boundary)
        self.map.zoom_to_geometry(boundary)
        self._update_ui()
        self._notify("%d tiles selected from %s"
                     % (len(found), os.path.basename(path)))

    # ── view ─────────────────────────────────────────────────────────────
    def _zoom_to_selection(self):
        box = geom.selection_bbox(self.selection)
        if box is None:
            self._notify("Nothing selected yet")
            return
        self.map.set_extent_latlon(box.yMinimum(), box.xMinimum(),
                                   box.yMaximum(), box.xMaximum())

    # ── UI refresh ───────────────────────────────────────────────────────
    def _update_ui(self):
        count = len(self.selection)
        self.stat_tiles.set_value("{:,}".format(count))
        self.stat_size.set_value(self._estimated_size(count))
        self.stat_res.set_value(self.product.resolution_label)
        if self._task is None:
            self.btn_download.setEnabled(count > 0)
            self.btn_download.setIcon(icons.icon(
                "download", px(15), C["text"] if count else C["disabled_fg"], 2.5))
        else:
            # The map stays live during a download, so a selection change must
            # not quietly turn the Cancel button back into a Download button.
            self.btn_download.setEnabled(True)
            self.btn_download.setIcon(icons.icon("close", px(15), C["text"], 2.5))
        self.map.overlay.selection = self.selection
        self.map.overlay.update()
        self._rebuild_chips()

    def _estimated_size(self, count):
        if count == 0:
            return "\u2014"
        if not self.product.mosaic:
            return format_size(count * self.product.size_mb)
        # A mosaic clip is written uncompressed at the source resolution: one
        # arc-second cells, four bytes each.  The page reads the real figures out
        # of the COG header; this is the same arithmetic with those constants.
        box = geom.selection_bbox(self.selection)
        pixels = (box.width() * 3600.0) * (box.height() * 3600.0)
        return "~" + format_size(pixels * 4 / (1024.0 * 1024.0))

    def _rebuild_chips(self):
        while self.tiles_chip_grid.count():
            item = self.tiles_chip_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        names = geom.names(self.selection)
        self.tiles_empty.setVisible(not names)
        self.tiles_chips.setVisible(bool(names))
        self.tiles_count.setVisible(bool(names))
        if not names:
            return
        self.tiles_count.setText("%d tile%s selected - click one to remove it"
                                 % (len(names), "" if len(names) == 1 else "s"))
        columns = 3
        for index, name in enumerate(names):
            chip = QPushButton(name)
            chip.setObjectName("tileChip")
            chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            chip.setToolTip("Remove %s" % name)
            chip.clicked.connect(lambda _, n=name: self._remove_tile(n))
            self.tiles_chip_grid.addWidget(chip, index // columns, index % columns)

    def _remove_tile(self, name):
        from .products import parse_tile_name

        self.selection.discard(parse_tile_name(name))
        self._update_ui()

    def _notify(self, message, level=Qgis.MessageLevel.Info):
        QgsMessageLog.logMessage(message, "Global DEM Portal", level)
        self.notified.emit(message, int(level))

    # ── download ─────────────────────────────────────────────────────────
    def _download_clicked(self):
        """The primary button starts a download, and stops one that is running."""
        if self._task is not None:
            self._task.cancel()
            self.btn_download.setText("  Cancelling…")
            self.btn_download.setEnabled(False)
            return
        self._start_download()

    def _start_download(self):
        if not self.selection:
            return
        # Parented to the QGIS main window, deliberately, not to this panel.
        #
        # A Qt stylesheet applies to its owner and to everything below it in the
        # object hierarchy - including dialogs, which are children of the widget
        # they are opened from.  QGIS parents its Coordinate Reference System
        # Selector to the projection widget inside the download dialog, so with
        # the dialog parented here the panel's dark rules reached all the way
        # into QGIS's own selector and left it unreadable: light text on its
        # light background.  Hanging the dialog off the main window instead
        # keeps the panel out of that chain, so anything QGIS opens from the
        # dialog is styled by QGIS, as it is everywhere else in the application.
        from qgis.utils import iface

        host = iface.mainWindow() if iface is not None else None
        dialog = DownloadDialog(self.product, self.selection, host)
        if not dialog.exec():
            return

        merge_tiles = dialog.merge_tiles
        balance_seams = dialog.balance_seams
        if merge_tiles:
            answer = self._confirm_mosaic(host)
            if answer == "cancel":
                return
            if answer == "skip":
                merge_tiles = balance_seams = False

        task = DownloadTask(self.product, set(self.selection), dialog.folder,
                            dialog.crs_definition, dialog.keep_originals,
                            merge_tiles=merge_tiles,
                            balance_seams=balance_seams)
        colour_ramp = dialog.colour_ramp
        add_to_project = dialog.add_to_project
        task.finished_with.connect(
            lambda paths_, errors, reprojected, skipped, t=task:
            self._on_downloaded(t, paths_, errors, reprojected, skipped,
                                add_to_project, colour_ramp))
        task.progressChanged.connect(self._on_progress)
        self._task = task
        self.btn_download.setText("  Cancel")
        self.btn_download.setIcon(icons.icon("close", px(15), C["text"], 2.5))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        QgsApplication.taskManager().addTask(task)

    def _confirm_mosaic(self, host):
        """Ask before merging a mosaic that is mostly hole, or simply enormous.

        The check happens here rather than in the task because the answer is
        known before a single byte is downloaded - the selection is the mosaic's
        footprint - and because a worker thread cannot put a dialog on screen.

        Returns "merge", "skip" or "cancel".  Parented to the QGIS main window,
        like the download dialog and for the same reason: the panel's stylesheet
        must not reach a dialog QGIS draws.
        """
        from . import mosaic

        estimate = mosaic.mosaic_estimate(self.selection,
                                          self.product.pixels_per_degree)
        if not mosaic.needs_confirmation(estimate):
            return "merge"

        rows, columns = estimate["degrees"]
        detail = ["The mosaic would be %s x %s pixels (%.1f gigapixels), "
                  "covering %d° by %d°."
                  % ("{:,}".format(estimate["width"]),
                     "{:,}".format(estimate["height"]),
                     estimate["pixels"] / 1e9, columns, rows)]
        if estimate["empty_cells"]:
            detail.append(
                "%s of the %s one-degree cells inside it are selected, so about "
                "%d%% of the file would be empty padding between them."
                % ("{:,}".format(estimate["cells"]),
                   "{:,}".format(estimate["box_cells"]),
                   round(100 * (1 - estimate["fill"]))))
            detail.append(
                "Empty area compresses to almost nothing on disk, but GDAL still "
                "has to write it and QGIS still has to open it.")

        box = QMessageBox(host)
        box.setWindowTitle("Merge into one raster?")
        box.setIcon(QMessageBox.Icon.Question)
        # Name whichever condition actually earned the question: a mosaic can be
        # mostly hole, or merely enormous, and saying the wrong one is worse
        # than saying nothing.
        box.setText("These tiles are spread out."
                    if estimate["fill"] < mosaic.MOSAIC_WARN_FILL
                    else "This is a large mosaic.")
        box.setInformativeText("\n\n".join(detail))
        merge = box.addButton("Merge anyway", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("Download without merging",
                             QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip)
        box.setEscapeButton(cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is merge:
            return "merge"
        if clicked is skip:
            return "skip"
        return "cancel"

    def _on_progress(self, value):
        self.progress.setValue(int(value))

    def _on_downloaded(self, task, files, errors, reprojected, skipped,
                       add_to_project, colour_ramp):
        self.progress.setVisible(False)
        self.btn_download.setText("  Get Downloads")
        self._task = None
        self._update_ui()                    # restores the button's icon and state

        if files and add_to_project:
            self._load_result(task, files, colour_ramp)

        merged = bool(task.merged_path)
        if merged:
            parts = ["merged into %s" % os.path.basename(task.merged_path)]
        else:
            parts = ["%d file%s downloaded"
                     % (len(files), "" if len(files) == 1 else "s")]
        if task.balance_note:
            parts.append(task.balance_note)
        if reprojected:
            parts.append("%d reprojected" % reprojected)
        if skipped:
            parts.append("%d already in that CRS" % skipped)
        if errors:
            parts.append("%d failed" % len(errors))
            for detail in errors[:5]:
                QgsMessageLog.logMessage(detail, "Global DEM Portal",
                                         Qgis.MessageLevel.Warning)
        self._notify(", ".join(parts),
                     Qgis.MessageLevel.Warning if errors else Qgis.MessageLevel.Success)

    def _load_result(self, task, files, colour_ramp):
        """Add what was downloaded to the project, rendered so it reads as one surface.

        The stretch is worked out once and given to every layer.  That is the
        whole point of the colour option: rendered with its own min and max, a
        plateau tile and a mountain tile look like different datasets even though
        they are neighbours in the same mission.
        """
        loaded, failed = layer_utils.add_rasters(
            files, "Global DEM Portal - %s" % self.product.key)
        for path in failed:
            QgsMessageLog.logMessage("could not load %s" % path,
                                     "Global DEM Portal", Qgis.MessageLevel.Warning)
        if not colour_ramp or not loaded:
            return
        try:
            from . import mosaic

            limits = (mosaic.percentiles(task.merged_path) if task.merged_path
                      else mosaic.pooled_percentiles(files))
            if limits is None:
                return
            for layer in loaded:
                mosaic.style_dem(layer, limits[0], limits[1])
        except Exception as exc:  # noqa: BLE001 - styling must never lose the data
            QgsMessageLog.logMessage("could not style the download: %s" % exc,
                                     "Global DEM Portal", Qgis.MessageLevel.Warning)

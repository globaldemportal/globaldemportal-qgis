"""The map half of the panel: a QgsMapCanvas dressed to look like the Leaflet map.

The web app draws its 1x1 degree grid, the coverage limits and the selection into
a canvas overlay.  This does the same with a transparent child widget over the
map canvas, using the very same colours and line widths read out of index.html:

    grid       rgba(148,163,184,0.22), 0.8px when zoomed in, 0.5px otherwise
    selection  #3b82f6 at 42% fill, #60a5fa 1.5px outline
    coverage   rgba(239,68,68,0.65), 1.5px

The land mask is the same 8100-byte bitmask the page embeds, extracted to
data/land_mask.bin - so exactly the same cells are drawable in both.
"""

import os

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QLabel, QToolButton, QWidget
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsLineSymbol,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsRubberBand

from . import icons
from .theme import C

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")

#: Same basemap the page defaults to - CARTO's dark raster, rendered from OSM.
BASEMAP_URL = "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
BASEMAP_LABEL = "© OpenStreetMap contributors, © CARTO"

#: The page constrains the map to a single world: no infinite horizontal wrap, no
#: zooming out past the globe, no panning off its edges (WORLD_BOUNDS, minZoom 3).
#: A Leaflet zoom level is a scale denominator of 559082264 / 2**z at 96 dpi, so
#: zoom 3 is the largest denominator the canvas is allowed to reach.
WORLD_SOUTH, WORLD_NORTH = -85.05112878, 85.05112878
WORLD_WEST, WORLD_EAST = -180.0, 180.0
MIN_ZOOM = 3
MAX_SCALE = 559082264.028 / 2 ** MIN_ZOOM

#: CARTO dark paints land #090909 and draws boundaries in rgb(40,40,40).  The
#: boundary corrector's own config for this basemap names that same line colour;
#: erasing a wrong line means overpainting it in the land colour.
BASEMAP_LAND = "#090909"
BOUNDARY_LINE = "#282828"
#: ne (Natural Earth) geometry is used to zoom 4, osm geometry from zoom 5 - the
#: crossover the corrector's cartodb-dark config specifies.
NE_OSM_CROSSOVER_SCALE = 559082264.028 / 2 ** 5

#: The opening view: India with a margin, given as (south, west, north, east).
#:
#: Not the web app's own opening bounds.  The page opens into a tall 580x820
#: browser column, and those bounds are shaped for it; a QGIS dock is usually
#: landscape, and fitting a tall rectangle into a wide viewport leaves the
#: country small between two oceans.  These bounds are close to square, so India
#: stays centred and wholly visible whatever shape the dock is given.  India
#: spans 6.5-37.1N and 68.1-97.4E including Kashmir; this is that plus ~1.5
#: degrees of air.
OPENING_VIEW = (5.0, 66.5, 38.5, 99.0)

#: Below this, in either direction, the canvas has not been laid out yet.
#:
#: A Qt child widget starts at 100x30 and only gets its real size when the
#: parent lays out.  Anything that reasons about *scale* before then is
#: reasoning about a viewport that does not exist yet - and, worse, can write
#: the result back.  Fitting the opening view to 100x30 put the scale far past
#: the zoom floor, so the clamp shrank the extent about its centre and the
#: framing was gone for good: the map opened zoomed into the middle of India
#: instead of showing the country.
#:
#: The size to test is the canvas's own ``mapSettings().outputSize()``, not the
#: widget's width and height.  setGeometry updates the widget immediately but
#: the canvas only learns its new size when Qt delivers the resize event, and
#: in between the widget reads 648x530 while the canvas is still deriving every
#: scale from 98x28.  That gap is precisely where this bug lived.
MIN_LAID_OUT = 64

#: Leaflet clamps the portal to zoom 3+; below that the 1 degree grid is unreadable.
#: The page switches line weight at zoom 6 and starts labelling cells at zoom 7.  A
#: Leaflet zoom level puts 256 * 2**z pixels around the world, so one degree spans
#: 256 * 2**z / 360 px - 45.5px at zoom 6, 91px at zoom 7.
MIN_CELL_PIXELS = 5
BOLD_CELL_PIXELS = 256 * 2 ** 6 / 360.0
LABEL_CELL_PIXELS = 256 * 2 ** 7 / 360.0

_DATA = os.path.join(os.path.dirname(__file__), "data", "land_mask.bin")


def _load_mask():
    """The 360x180 1-degree bitmask; bit index = (lat + 90) * 360 + (lon + 180)."""
    with open(_DATA, "rb") as handle:
        return handle.read()


class LandMask:
    """Which 1x1 degree cells actually have a DEM tile on the mirror."""

    def __init__(self):
        self._bits = _load_mask()

    def has(self, lat, lon):
        if lat < -90 or lat >= 90 or lon < -180 or lon >= 180:
            return False
        index = (lat + 90) * 360 + (lon + 180)
        return bool(self._bits[index >> 3] & (1 << (index & 7)))


def tile_name(lat, lon):
    """N28E077 - named for the lower-left corner, as the page does."""
    return "%s%02d%s%03d" % (
        "N" if lat >= 0 else "S", abs(lat),
        "E" if lon >= 0 else "W", abs(lon),
    )


def basemap_layer():
    """The dark XYZ basemap, as a QGIS raster layer."""
    uri = "type=xyz&url=%s&zmin=0&zmax=19" % BASEMAP_URL.replace("=", "%3D").replace(
        "&", "%26"
    )
    layer = QgsRasterLayer(uri, "CARTO Dark", "wms")
    return layer if layer.isValid() else None


def _line_layer(gpkg, table, colour, width, dashed=False):
    """One correction layer, styled to sit invisibly inside the basemap."""
    layer = QgsVectorLayer("%s|layername=%s" % (gpkg, table), table, "ogr")
    if not layer.isValid():
        return None
    symbol = QgsLineSymbol.createSimple({"color": colour})
    line = symbol.symbolLayer(0)
    line.setWidth(width)
    # Pixels, not millimetres: the corrector's widths are tile pixels, and a
    # pixel width keeps the line the same weight at every zoom, as it is in the
    # basemap's own raster tiles.
    line.setWidthUnit(QgsUnitTypes.RenderUnit.RenderPixels)
    if dashed:
        line.setPenStyle(Qt.PenStyle.DashLine)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def correction_layers():
    """India's boundary corrections, as canvas layers drawn over the basemap.

    The web app fixes the basemap by repainting each raster tile: it erases the
    wrong lines and draws the official ones from a PMTiles archive.  A QGIS
    canvas composites layers instead, so the same archive is shipped as vector
    geometry and the same two passes are done with layers - the ``to_del`` lines
    overpainted in the basemap's land colour, then the ``to_add`` lines drawn in
    the basemap's own boundary grey.  Same source data, same result: Jammu &
    Kashmir, Gilgit-Baltistan, Aksai Chin, Siachen and Arunachal Pradesh appear
    as India's official boundary rather than OSM's de-facto line.

    Returned top-first, the order QgsMapCanvas.setLayers expects.
    """
    gpkg = os.path.join(os.path.dirname(__file__), "data", "india_corrections.gpkg")
    if not os.path.isfile(gpkg):
        return []

    # (table, colour, width, dashed, ne-only)
    plan = [
        ("to_add_osm", BOUNDARY_LINE, 1.4, False, False),
        ("to_add_osm_disp", BOUNDARY_LINE, 1.4, False, False),
        ("to_add_osm_internal", BOUNDARY_LINE, 0.6, True, False),
        ("to_add_ne", BOUNDARY_LINE, 1.0, False, True),
        ("to_add_ne_disp", BOUNDARY_LINE, 1.0, False, True),
        ("to_add_ne_internal", BOUNDARY_LINE, 0.5, True, True),
        # The erase pass is wider than the line it covers, as delWidthFactor is
        # in the corrector's config, so antialiased edges disappear too.
        ("to_del_osm", BASEMAP_LAND, 3.0, False, False),
        ("to_del_osm_disp", BASEMAP_LAND, 3.0, False, False),
        ("to_del_osm_internal", BASEMAP_LAND, 3.0, False, False),
        ("to_del_ne", BASEMAP_LAND, 2.5, False, True),
        ("to_del_ne_disp", BASEMAP_LAND, 2.5, False, True),
        ("to_del_ne_internal", BASEMAP_LAND, 2.5, False, True),
    ]

    built = []
    for table, colour, width, dashed, ne_only in plan:
        layer = _line_layer(gpkg, table, colour, width, dashed)
        if layer is None:
            continue
        layer.setScaleBasedVisibility(True)
        if ne_only:
            layer.setMinimumScale(1e9)                    # zoomed out
            layer.setMaximumScale(NE_OSM_CROSSOVER_SCALE)
        else:
            layer.setMinimumScale(NE_OSM_CROSSOVER_SCALE)
            layer.setMaximumScale(0)                      # all the way in
        built.append(layer)
    return built


class GridOverlay(QWidget):
    """Transparent painter over the canvas: grid, coverage band, selection."""

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.mask = LandMask()
        self.selection = set()          # {(lat, lon)}
        self.product = None             # set by the panel; decides coverage
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        canvas.extentsChanged.connect(self.update)

    # -- geometry ---------------------------------------------------------
    def _transform(self):
        return QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(),
                                      QgsProject.instance())

    def _to_screen(self, transform, lat, lon):
        point = transform.transform(QgsPointXY(lon, lat))
        device = self.canvas.getCoordinateTransform().transform(point)
        return QPointF(device.x(), device.y())

    def _visible_cells(self, transform):
        """Integer lat/lon bounds of the visible extent, clipped to the world."""
        extent = self.canvas.extent()
        inverse = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(),
                                         WGS84, QgsProject.instance())
        try:
            geo = inverse.transformBoundingBox(extent)
        except Exception:  # noqa: BLE001 - an extent off the edge of the projection
            return None
        lat_lo = max(-90, int(geo.yMinimum()) - 1)
        lat_hi = min(89, int(geo.yMaximum()) + 1)
        lon_lo = max(-180, int(geo.xMinimum()) - 1)
        lon_hi = min(179, int(geo.xMaximum()) + 1)
        return lat_lo, lat_hi, lon_lo, lon_hi

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 - Qt naming
        transform = self._transform()
        bounds = self._visible_cells(transform)
        if not bounds:
            return
        lat_lo, lat_hi, lon_lo, lon_hi = bounds

        # One cell's size on screen decides whether the grid is drawn at all.
        origin = self._to_screen(transform, 0, 0)
        step = self._to_screen(transform, 1, 1)
        cell = abs(step.x() - origin.x())
        if cell < MIN_CELL_PIXELS:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        grid = QPen(QColor(148, 163, 184, 56))  # rgba(148,163,184,0.22)
        grid.setWidthF(0.8 if cell >= BOLD_CELL_PIXELS else 0.5)
        painter.setPen(grid)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Clip the loop to the product's coverage, as the page does: outside it
        # there is no data, so no grid and nothing selectable.
        product = self.product
        if product is not None:
            lat_lo = max(lat_lo, product.lat_min)
            lat_hi = min(lat_hi, product.lat_max - 1)
            lon_lo = max(lon_lo, product.lon_min)
            lon_hi = min(lon_hi, product.lon_max - 1)

        drawable = []
        for lat in range(lat_lo, lat_hi + 1):
            for lon in range(lon_lo, lon_hi + 1):
                if not self.mask.has(lat, lon):
                    continue
                lower = self._to_screen(transform, lat, lon)
                upper = self._to_screen(transform, lat + 1, lon + 1)
                rect = QRectF(lower, upper).normalized()
                painter.drawRect(rect)
                drawable.append((lat, lon, rect))

        if cell >= LABEL_CELL_PIXELS:
            painter.setPen(QColor(148, 163, 184, 140))   # rgba(148,163,184,0.55)
            font = QFont("Courier New")
            font.setPixelSize(9)
            painter.setFont(font)
            for lat, lon, rect in drawable:
                if (lat, lon) in self.selection:
                    continue
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                                 tile_name(lat, lon))

        self._paint_selection(painter, transform, cell)
        self._paint_coverage(painter, transform, lon_lo, lon_hi)
        painter.end()

    def _paint_selection(self, painter, transform, cell):
        if not self.selection:
            return
        fill = QColor(C["accent"])
        fill.setAlphaF(0.42)
        outline = QPen(QColor("#60a5fa"))
        outline.setWidthF(1.5)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        label_font = QFont()
        label_font.setPointSizeF(8.5)
        for lat, lon in sorted(self.selection):
            lower = self._to_screen(transform, lat, lon)
            upper = self._to_screen(transform, lat + 1, lon + 1)
            rect = QRectF(lower, upper).normalized()
            painter.fillRect(rect, fill)
            painter.setPen(outline)
            painter.drawRect(rect)
            if cell >= LABEL_CELL_PIXELS:
                painter.setFont(label_font)
                painter.setPen(QColor(C["text"]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tile_name(lat, lon))

    def set_product(self, product):
        self.product = product
        self.update()

    def _paint_coverage(self, painter, transform, lon_lo, lon_hi):
        """The product's latitude limits - the page's red dashed lines."""
        if self.product is None:
            return
        low, high = self.product.lat_min, self.product.lat_max
        if low <= -90 and high >= 90:
            return
        pen = QPen(QColor(239, 68, 68, 166))  # rgba(239,68,68,0.65)
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for lat in (low, high):
            left = self._to_screen(transform, lat, lon_lo)
            right = self._to_screen(transform, lat, lon_hi)
            painter.drawLine(left, right)


class _MapControl(QToolButton):
    """One of Leaflet's little white square buttons."""

    def __init__(self, text, parent=None, size=30, radius=""):
        super().__init__(parent)
        self.setObjectName("mapCtrl")
        self.setText(text)
        self.setFixedSize(size, size)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if radius:
            self.setStyleSheet(radius)


class ScaleBar(QWidget):
    """Leaflet's scale control: a metric bar over an imperial one.

    Leaflet picks the largest round distance (1, 2 or 5 times a power of ten)
    that fits in 100px, and draws a box whose width is that distance.  The same
    rounding is repeated here so the two read the same at the same zoom.
    """

    MAX_WIDTH = 150   # the page passes maxWidth: 150 to L.control.scale

    def __init__(self, canvas, parent=None):
        super().__init__(parent or canvas)
        self.metric = QLabel(self)
        self.imperial = QLabel(self)
        for index, label in enumerate((self.metric, self.imperial)):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                "background: rgba(255,255,255,0.5); color: #333333; font-size: 11px;"
                " border: 2px solid #777777; border-top: %s;"
                % ("2px solid #777777" if index == 0 else "none")
            )
        self.setFixedHeight(38)

    @staticmethod
    def _round(value):
        """Leaflet's getRoundNum: 1, 2 or 5 at the right order of magnitude."""
        power = 10 ** (len(str(int(value))) - 1)
        fraction = value / power
        fraction = (10 if fraction >= 10 else 5 if fraction >= 5
                    else 3 if fraction >= 3 else 2 if fraction >= 2 else 1)
        return power * fraction

    def recompute(self, canvas):
        metres_per_pixel = canvas.mapUnitsPerPixel()   # EPSG:3857 units are metres
        # Web Mercator metres are stretched by 1/cos(lat); correct at the centre.
        import math

        centre = canvas.extent().center()
        latitude = math.degrees(2 * math.atan(math.exp(math.radians(
            centre.y() / 20037508.34 * 180))) - math.pi / 2)
        metres_per_pixel *= math.cos(math.radians(latitude))

        metres = metres_per_pixel * self.MAX_WIDTH
        if metres <= 0:
            return
        rounded = self._round(metres)
        width = max(20, int(rounded / metres_per_pixel))
        self.metric.setText("%d km" % (rounded / 1000) if rounded >= 1000
                            else "%d m" % rounded)
        self.metric.setGeometry(0, 0, width, 19)

        feet = metres * 3.2808399
        if feet < 5280:
            rounded_i = self._round(feet)
            width_i = max(20, int(width * rounded_i / feet))
            self.imperial.setText("%d ft" % rounded_i)
        else:
            miles = feet / 5280
            rounded_i = self._round(miles)
            width_i = max(20, int(width * rounded_i / miles))
            self.imperial.setText("%d mi" % rounded_i)
        self.imperial.setGeometry(0, 19, width_i, 19)
        self.setFixedWidth(max(width, width_i))


class PortalMap(QWidget):
    """The canvas plus the floating controls, laid out like Leaflet's."""

    tile_clicked = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = QgsMapCanvas(self)
        self.canvas.setCanvasColor(QColor(C["bg_deep"]))
        self.canvas.setDestinationCrs(WEB_MERCATOR)
        self.canvas.enableAntiAliasing(True)

        # Deliberately *not* registered with QgsProject.
        #
        # These thirteen layers are the panel's own furniture: a basemap and the
        # boundary corrections.  Registering them put all thirteen into whatever
        # project the user happened to have open, where they showed up in every
        # layer chooser, were saved into the .qgz, and outlived the plugin being
        # unloaded.  A canvas renders unregistered layers perfectly well as long
        # as something keeps them alive, and these two attributes do exactly that
        # for as long as the map exists.
        self._basemap = basemap_layer()
        self._corrections = correction_layers()
        stack = list(self._corrections)
        if self._basemap:
            stack.append(self._basemap)          # last = bottom of the stack
        self.canvas.setLayers(stack)

        self.overlay = GridOverlay(self.canvas, self.canvas)
        self._boundary = None
        self._clamping = False
        self._opening_view = None
        self._build_controls()
        self.canvas.extentsChanged.connect(self._clamp_view)

        # Applied by resizeEvent, not here.  See MIN_LAID_OUT: at this point
        # the canvas is still 100x30, and framing anything against that size
        # produces a view the clamp then destroys.
        self._opening_view = OPENING_VIEW

    def _build_controls(self):
        rounded_top = ("QToolButton#mapCtrl { border-top-left-radius: 4px; "
                       "border-top-right-radius: 4px; border-bottom: none; }")
        rounded_bottom = ("QToolButton#mapCtrl { border-bottom-left-radius: 4px; "
                          "border-bottom-right-radius: 4px; }")
        self.zoom_in = _MapControl("+", self.canvas, radius=rounded_top)
        self.zoom_out = _MapControl("−", self.canvas, radius=rounded_bottom)
        self.layers = _MapControl("", self.canvas, size=44,
                                  radius="QToolButton#mapCtrl { border-radius: 4px; }")
        self.layers.setIcon(icons.icon("layers", 22, "#000000", 1.6))
        self.layers.setIconSize(QSize(22, 22))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoomByFactor(0.5))
        self.zoom_out.clicked.connect(lambda: self.canvas.zoomByFactor(2.0))

        self.scale = ScaleBar(self.canvas)
        self.canvas.extentsChanged.connect(self._update_scale)

        self.attribution = QLabel(BASEMAP_LABEL, self.canvas)
        self.attribution.setStyleSheet(
            "background: rgba(255,255,255,0.8); color: #333333; font-size: 11px;"
            " padding: 0px 5px;"
        )

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        self.canvas.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        if (self._opening_view
                and self.width() >= MIN_LAID_OUT
                and self.height() >= MIN_LAID_OUT):
            # Store the opening view at the first real resize.  The canvas may
            # not have caught up yet, and that is fine: setExtent records the
            # rectangle, and the canvas fits it to the viewport when it learns
            # its size.  What it must not do in the meantime is clamp it, which
            # is what laid_out() prevents.  Cleared here so later resizes leave
            # the user's own panning and zooming alone.
            view, self._opening_view = self._opening_view, None
            self.set_extent_latlon(*view)
        margin = 10
        self.zoom_in.move(self.width() - self.zoom_in.width() - margin, margin)
        self.zoom_out.move(self.zoom_in.x(), self.zoom_in.y() + self.zoom_in.height())
        self.layers.move(self.width() - self.layers.width() - margin,
                         self.zoom_out.y() + self.zoom_out.height() + 12)
        self.attribution.adjustSize()
        self.attribution.move(self.width() - self.attribution.width(),
                              self.height() - self.attribution.height())
        self._update_scale()
        super().resizeEvent(event)

    def _update_scale(self):
        """Reposition and relabel the scale bar, bottom-right above the credit."""
        self.scale.recompute(self.canvas)
        self.scale.move(self.width() - self.scale.width() - 10,
                        self.height() - self.scale.height()
                        - self.attribution.height() - 6)

    # ── keeping the view inside one world ────────────────────────────────
    def laid_out(self):
        """Does the canvas know its real size yet?  See :data:`MIN_LAID_OUT`."""
        size = self.canvas.mapSettings().outputSize()
        return size.width() >= MIN_LAID_OUT and size.height() >= MIN_LAID_OUT

    def world_extent(self):
        """The single world, in the canvas CRS."""
        transform = QgsCoordinateTransform(
            WGS84, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        return transform.transformBoundingBox(
            QgsRectangle(WORLD_WEST, WORLD_SOUTH, WORLD_EAST, WORLD_NORTH))

    def _clamp_view(self):
        """Stop the map zooming out past the globe or panning off its edges.

        Leaflet does this declaratively with maxBounds/minZoom; QgsMapCanvas has
        no equivalent, so the extent is corrected after the fact.  Two things
        matter here, and the first version got both wrong:

        * The correction must be computed, not applied in steps.  Calling
          zoomScale() and then setExtent() makes the canvas emit extentsChanged
          twice more, and the handler can chase its own tail - which locked the
          canvas up instead of merely limiting it.
        * The write must be signal-blocked and idempotent.  Signals are blocked
          around the single setExtent, and a correction smaller than half a
          percent is treated as no correction, so float noise cannot oscillate.
        """
        if self._clamping:
            return
        canvas = self.canvas
        if not self.laid_out():
            # Not laid out yet: any scale computed now is an artefact of the
            # placeholder size, and correcting it would corrupt the real view.
            # The stored extent is left alone, and the canvas re-derives the
            # scale from it once it knows how big it is.
            return
        extent = canvas.extent()
        if extent.isEmpty():
            return
        world = self.world_extent()

        width, height = extent.width(), extent.height()
        centre = extent.center()
        x, y = centre.x(), centre.y()

        # Zoom floor: shrink about the centre until the scale is within reach.
        scale = canvas.scale()
        if scale > MAX_SCALE:
            factor = MAX_SCALE / scale
            width *= factor
            height *= factor

        # Never show more than one world in either direction.
        width = min(width, world.width())
        height = min(height, world.height())

        # Then slide the centre so the view sits inside the world.
        half_w, half_h = width / 2.0, height / 2.0
        x = min(max(x, world.xMinimum() + half_w), world.xMaximum() - half_w)
        y = min(max(y, world.yMinimum() + half_h), world.yMaximum() - half_h)

        target = QgsRectangle(x - half_w, y - half_h, x + half_w, y + half_h)
        tolerance = 0.005 * max(width, height)
        if (abs(target.xMinimum() - extent.xMinimum()) < tolerance
                and abs(target.yMinimum() - extent.yMinimum()) < tolerance
                and abs(target.width() - extent.width()) < tolerance
                and abs(target.height() - extent.height()) < tolerance):
            return

        self._clamping = True
        try:
            blocked = canvas.blockSignals(True)
            canvas.setExtent(target)
            canvas.blockSignals(blocked)
            canvas.refresh()
            self.overlay.update()
        finally:
            self._clamping = False

    # ── geometry helpers used by the panel ───────────────────────────────
    def geographic_extent(self):
        """The visible extent as (south, west, north, east) in EPSG:4326."""
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance())
        try:
            geo = transform.transformBoundingBox(self.canvas.extent())
        except Exception:  # noqa: BLE001 - an extent past the projection's edge
            return None
        return (max(-90.0, geo.yMinimum()), max(-180.0, geo.xMinimum()),
                min(90.0, geo.yMaximum()), min(180.0, geo.xMaximum()))

    def show_boundary(self, geometry):
        """Outline the searched country / uploaded polygon, as the page does.

        Same styling as its Leaflet layer: amber, 1.6px, dashed, no fill.
        """
        self.clear_boundary()
        transform = QgsCoordinateTransform(
            WGS84, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        projected = QgsGeometry(geometry)
        if projected.transform(transform) != 0:
            return
        band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        band.setColor(QColor(245, 158, 11, 230))     # #f59e0b at 0.9
        band.setFillColor(QColor(0, 0, 0, 0))
        band.setWidth(2)
        band.setLineStyle(Qt.PenStyle.DashLine)
        band.setToGeometry(projected, None)
        self._boundary = band

    def clear_boundary(self):
        if getattr(self, "_boundary", None) is not None:
            self.canvas.scene().removeItem(self._boundary)
            self._boundary = None

    def zoom_to_geometry(self, geometry):
        box = geometry.boundingBox()
        self.set_extent_latlon(box.yMinimum(), box.xMinimum(),
                               box.yMaximum(), box.xMaximum())

    def set_extent_latlon(self, south, west, north, east):
        transform = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
        rect = transform.transformBoundingBox(QgsRectangle(west, south, east, north))
        self.canvas.setExtent(rect)
        self.canvas.refresh()

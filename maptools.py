"""The four selection tools, as QGIS map tools.

The web app binds these to a Leaflet canvas; here each is a QgsMapTool on the
panel's own canvas.  All of them emit lat/lon in EPSG:4326 - the canvas works in
Web Mercator, so every screen point is transformed back before it becomes a cell.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand

from .mapview import WGS84


class _GeoTool(QgsMapTool):
    """Shared plumbing: canvas point -> lat/lon."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _to_wgs84(self, event):
        point = self.toMapCoordinates(event.pos())
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance()
        )
        return transform.transform(point)


class ClickTool(_GeoTool):
    """Click a cell to add it, click it again to remove it."""

    toggled_cell = pyqtSignal(int, int)

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() != Qt.MouseButton.LeftButton:
            return
        import math

        point = self._to_wgs84(event)
        self.toggled_cell.emit(int(math.floor(point.y())), int(math.floor(point.x())))


class RectangleTool(_GeoTool):
    """Drag a box; every drawable cell it touches is selected."""

    selected_rect = pyqtSignal(float, float, float, float)   # S, W, N, E

    def __init__(self, canvas):
        super().__init__(canvas)
        self.band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.band.setColor(QColor(59, 130, 246, 60))
        self.band.setStrokeColor(QColor(96, 165, 250))
        self.band.setWidth(2)
        self._origin = None

    def canvasPressEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._origin = self.toMapCoordinates(event.pos())
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def canvasMoveEvent(self, event):  # noqa: N802 - Qt naming
        if self._origin is None:
            return
        current = self.toMapCoordinates(event.pos())
        rect = QgsRectangle(self._origin, current)
        self.band.setToGeometry(QgsGeometry.fromRect(rect), None)

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt naming
        if self._origin is None or event.button() != Qt.MouseButton.LeftButton:
            return
        current = self.toMapCoordinates(event.pos())
        rect = QgsRectangle(self._origin, current)
        self._origin = None
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance()
        )
        geo = transform.transformBoundingBox(rect)
        self.selected_rect.emit(geo.yMinimum(), geo.xMinimum(),
                                geo.yMaximum(), geo.xMaximum())

    def deactivate(self):
        self._origin = None
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        super().deactivate()


class PolygonTool(_GeoTool):
    """Click to add vertices; double-click or right-click closes the polygon."""

    selected_polygon = pyqtSignal(object)     # QgsGeometry in EPSG:4326

    def __init__(self, canvas):
        super().__init__(canvas)
        self.band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.band.setColor(QColor(59, 130, 246, 60))
        self.band.setStrokeColor(QColor(96, 165, 250))
        self.band.setWidth(2)
        self._points = []

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.RightButton:
            self._finish()
            return
        self._points.append(self.toMapCoordinates(event.pos()))
        self._redraw()

    def canvasDoubleClickEvent(self, event):  # noqa: N802 - Qt naming
        # The release for the second click has already appended a duplicate point.
        if len(self._points) > 1:
            self._points.pop()
        self._finish()

    def _redraw(self):
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        if len(self._points) < 2:
            return
        self.band.setToGeometry(
            QgsGeometry.fromPolygonXY([[QgsPointXY(p) for p in self._points]]), None)

    def _finish(self):
        points, self._points = self._points, []
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        if len(points) < 3:
            return
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance()
        )
        ring = [transform.transform(p) for p in points]
        ring.append(ring[0])
        self.selected_polygon.emit(QgsGeometry.fromPolygonXY([ring]))

    def reset(self):
        self._points = []
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def deactivate(self):
        self.reset()
        super().deactivate()

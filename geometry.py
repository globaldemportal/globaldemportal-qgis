"""Turning geometry into tile selections.

The rule is the web app's: a cell counts as inside a polygon when any point of a
3x3 sample grid within it falls inside.  Testing only the centre drops border
cells - Tawang, for instance, whose centre lands across the line - so the sample
grid matters for correctness, not just for generosity.
"""

import json
import os

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle

from .products import tile_name

_DATA = os.path.join(os.path.dirname(__file__), "data")

#: The page's sample offsets within a cell.
_SAMPLES = (0.15, 0.5, 0.85)


def india_boundary():
    """India's official claimed boundary, as the page embeds it.

    Includes Jammu & Kashmir, Pakistan-administered Kashmir and Gilgit-Baltistan,
    Aksai Chin, Siachen and Arunachal Pradesh.  Nominatim returns OSM's de-facto
    line instead, which is why the page special-cases the query and why this file
    is carried alongside rather than fetched.
    """
    with open(os.path.join(_DATA, "india_boundary.json"), encoding="utf-8") as handle:
        return geometry_from_geojson(json.load(handle))


def geometry_from_geojson(obj):
    """A QgsGeometry from a GeoJSON geometry, Feature, or FeatureCollection.

    Total by design: anything it cannot make sense of comes back as None, never
    as an exception.  Both callers hand it untrusted input - a file the user
    dropped on the upload zone, and whatever Nominatim replied with - and both
    already treat None as "no usable boundary".  Before this was total, a file
    with, say, ``"coordinates": "oops"`` raised IndexError out of the parser and
    the user got a traceback instead of the message the panel meant to show.
    """
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind == "FeatureCollection":
        features = obj.get("features")
        if not isinstance(features, (list, tuple)):
            return None
        parts = [geometry_from_geojson(f) for f in features]
        parts = [p for p in parts if p and not p.isEmpty()]
        if not parts:
            return None
        merged = parts[0]
        for part in parts[1:]:
            merged = merged.combine(part)
        return merged
    if kind == "Feature":
        return geometry_from_geojson(obj.get("geometry"))
    if kind not in ("Polygon", "MultiPolygon"):
        return None
    # QgsGeometry parses GeoJSON via its WKT-ish JSON reader; going through the
    # string form keeps this independent of the QGIS version's json helpers.
    wkt = _wkt_from_geojson(obj)
    return QgsGeometry.fromWkt(wkt) if wkt else None


def _ring(coords):
    """One WKT ring, or None if these are not at least three coordinate pairs."""
    if not isinstance(coords, (list, tuple)):
        return None
    points = []
    for point in coords:
        try:
            points.append("%.8f %.8f" % (float(point[0]), float(point[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    # A polygon ring needs three distinct corners; two points is a line, and
    # QGIS would build a degenerate geometry from it rather than refuse.
    return "(" + ", ".join(points) + ")" if len(points) >= 3 else None


def _wkt_from_geojson(obj):
    """WKT for a Polygon or MultiPolygon, or None if the coordinates are unusable."""
    coordinates = obj.get("coordinates")
    if not isinstance(coordinates, (list, tuple)):
        return None
    if obj.get("type") == "Polygon":
        rings = [ring for ring in (_ring(r) for r in coordinates) if ring]
        return "POLYGON (" + ", ".join(rings) + ")" if rings else None
    polygons = []
    for polygon in coordinates:
        if not isinstance(polygon, (list, tuple)):
            continue
        rings = [ring for ring in (_ring(r) for r in polygon) if ring]
        if rings:
            polygons.append("(" + ", ".join(rings) + ")")
    return "MULTIPOLYGON (" + ", ".join(polygons) + ")" if polygons else None


def geometry_bbox(geometry):
    box = geometry.boundingBox()
    return box.yMinimum(), box.xMinimum(), box.yMaximum(), box.xMaximum()


def tiles_from_geometry(geometry, product, mask):
    """Cells of ``mask`` covered by ``geometry``, clipped to the product's coverage.

    Returns a set of (lat, lon).  The geometry is prepared once - without that,
    a country with a few thousand vertices costs a full point-in-polygon walk per
    sample, and India alone is 2400 vertices times nine samples per cell.
    """
    if geometry is None or geometry.isEmpty():
        return set()

    south, west, north, east = geometry_bbox(geometry)
    lat_lo = max(int(_floor(south)), product.lat_min)
    lat_hi = min(int(_floor(north)), product.lat_max - 1)
    lon_lo = max(int(_floor(west)), product.lon_min)
    lon_hi = min(int(_floor(east)), product.lon_max - 1)

    engine = QgsGeometry.createGeometryEngine(geometry.constGet())
    engine.prepareGeometry()

    found = set()
    for lat in range(lat_lo, lat_hi + 1):
        for lon in range(lon_lo, lon_hi + 1):
            if not mask.has(lat, lon):
                continue
            for dy in _SAMPLES:
                hit = False
                for dx in _SAMPLES:
                    point = QgsGeometry.fromPointXY(QgsPointXY(lon + dx, lat + dy))
                    if engine.intersects(point.constGet()):
                        hit = True
                        break
                if hit:
                    found.add((lat, lon))
                    break
    return found


def tiles_in_rect(south, west, north, east, product, mask):
    """Every drawable cell overlapping a lat/lon rectangle."""
    found = set()
    lat_lo = max(int(_floor(south)), product.lat_min)
    lat_hi = min(int(_floor(north - 1e-9)), product.lat_max - 1)
    lon_lo = max(int(_floor(west)), product.lon_min)
    lon_hi = min(int(_floor(east - 1e-9)), product.lon_max - 1)
    for lat in range(lat_lo, lat_hi + 1):
        for lon in range(lon_lo, lon_hi + 1):
            if mask.has(lat, lon):
                found.add((lat, lon))
    return found


def selection_bbox(selection):
    """The lat/lon envelope of a set of (lat, lon) cells, each 1 degree square."""
    if not selection:
        return None
    lats = [lat for lat, _ in selection]
    lons = [lon for _, lon in selection]
    return QgsRectangle(min(lons), min(lats), max(lons) + 1, max(lats) + 1)


def names(selection):
    """Sorted tile names for a selection, the order the page lists them in."""
    return [tile_name(lat, lon) for lat, lon in sorted(selection)]


def _floor(value):
    import math

    return math.floor(value)

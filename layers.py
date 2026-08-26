"""Turning downloaded files into QGIS layers."""

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType, QVariant

#: Extensions the portal can put on disk (GeoTIFF tiles, raw SRTM .hgt, VRT mosaics).
RASTER_EXTS = (".tif", ".tiff", ".hgt", ".vrt")


def _field(name, kind):
    """Build a QgsField across the QVariant (QGIS 3.x) / QMetaType (3.38+, Qt6) split."""
    meta = {"string": QMetaType.Type.QString, "int": QMetaType.Type.Int}[kind]
    try:
        return QgsField(name, meta)
    except TypeError:
        legacy = {"string": QVariant.String, "int": QVariant.Int}[kind]
        return QgsField(name, legacy)


def is_raster(path):
    return path.lower().endswith(RASTER_EXTS)


def add_rasters(paths, group_name=None):
    """Add rasters to the project, optionally inside a new layer-tree group.

    Returns ``(loaded_layers, failed_paths)``.
    """
    project = QgsProject.instance()
    group = None
    if group_name and len(paths) > 1:
        group = project.layerTreeRoot().insertGroup(0, group_name)

    loaded, failed = [], []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsRasterLayer(path, name, "gdal")
        if not layer.isValid():
            failed.append(path)
            continue
        # addMapLayer(layer, False) keeps it out of the tree root so the group owns it.
        project.addMapLayer(layer, group is None)
        if group is not None:
            group.addLayer(layer)
        loaded.append(layer)
    return loaded, failed


#: What every product the portal serves is delivered in.
PORTAL_CRS = "EPSG:4326"


def _authid(spatial_ref):
    """"EPSG:4326" for a GDAL SpatialReference, or "" when it has no authority."""
    if spatial_ref is None:
        return ""
    authority = spatial_ref.GetAuthorityName(None)
    code = spatial_ref.GetAuthorityCode(None)
    return "%s:%s" % (authority, code) if authority and code else ""


#: A CRS read off a file and one built from "EPSG:4326" describe the same system
#: but carry different axis-order metadata, which IsSame() counts as a difference
#: unless told not to. Without this flag every comparison here says "different".
_SAME_CRS_OPTIONS = ["IGNORE_DATA_AXIS_TO_SRS_AXIS_MAPPING=YES"]


def _spatial_ref(definition):
    """A SpatialReference from an authority id or WKT, or None if unparseable."""
    from osgeo import osr

    osr.UseExceptions()
    ref = osr.SpatialReference()
    try:
        if ref.SetFromUserInput(definition) != 0:
            return None
    except RuntimeError:
        return None
    return ref


def crs_matches(spatial_ref, target):
    """Is a raster's CRS already `target`? The check that prevents a pointless warp.

    Compares the definitions rather than their authority tags, so it still holds
    when the file names no EPSG code (the portal's own client-side mosaic clips)
    or when the target is custom WKT with no code to compare against.
    """
    if spatial_ref is None:
        return False
    authid = _authid(spatial_ref)
    if authid and ":" in target and authid.upper() == target.upper():
        return True  # the common case, settled without parsing anything
    goal = _spatial_ref(target)
    if goal is None:
        return False
    return bool(spatial_ref.IsSame(goal, _SAME_CRS_OPTIONS))


def same_crs_definition(first, second):
    """Do two CRS definitions (authority id or WKT) describe the same CRS?"""
    left, right = _spatial_ref(first), _spatial_ref(second)
    if left is None or right is None:
        return False
    return bool(left.IsSame(right, _SAME_CRS_OPTIONS))


def reproject_raster(path, target, resample="bilinear"):
    """Warp a downloaded raster into `target`, returning (path, was_reprojected).

    Every product the portal offers arrives in EPSG:4326, so a target CRS means a
    real warp rather than a relabelling - simply stamping a different CRS on the
    file would move the terrain, not project it. The result is written beside the
    original as ``<name>_<crs>.tif`` and the download is left untouched.

    `target` is an authority id, or WKT for a custom CRS. Bilinear is the default
    resampling because nearest-neighbour visibly terraces a warped elevation
    surface; pass resample="near" to keep exact source values.
    """
    from osgeo import gdal

    gdal.UseExceptions()
    dataset = gdal.Open(path)
    if dataset is None:
        raise RuntimeError("GDAL could not open %s" % os.path.basename(path))
    try:
        if crs_matches(dataset.GetSpatialRef(), target):
            # Already in the requested CRS - warping it to itself would cost minutes
            # per folder of tiles and produce a byte-shuffled copy of the same data.
            return path, False
        suffix = target.replace(":", "").lower() if ":" in target else "reprojected"
        out_path = "%s_%s.tif" % (os.path.splitext(path)[0], suffix)
        gdal.Warp(
            out_path,
            dataset,
            dstSRS=target,
            resampleAlg=resample,
            multithread=True,
            creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
        )
    finally:
        dataset = None
    return out_path, True


#: GDAL writes these next to a raster; they belong to it and go with it.
SIDECAR_SUFFIXES = (".aux.xml", ".ovr", ".xml", ".tfw", ".prj")


def discard_original(path, only_within=None):
    """Delete a raster that has been superseded by a reprojected copy.

    `only_within` is a safety fence: files outside that folder are left alone, so
    "Import downloaded DEMs" pointed at someone's own data can never delete it -
    only files the plugin itself put in its output folder are ever removed.
    Returns True if the file (and its GDAL sidecars) went away.
    """
    if only_within:
        try:
            common = os.path.commonpath([os.path.abspath(path), os.path.abspath(only_within)])
        except ValueError:  # different drives on Windows
            return False
        if common != os.path.abspath(only_within):
            return False
    try:
        os.remove(path)
    except OSError:
        return False
    stem = os.path.splitext(path)[0]
    for suffix in SIDECAR_SUFFIXES:
        for candidate in (path + suffix, stem + suffix):
            if os.path.isfile(candidate):
                try:
                    os.remove(candidate)
                except OSError:
                    pass
    return True


def is_readable_raster(path):
    """True if GDAL can open the file and it has at least one band."""
    from osgeo import gdal

    gdal.UseExceptions()
    try:
        dataset = gdal.Open(path)
    except RuntimeError:
        return False
    if dataset is None:
        return False
    ok = dataset.RasterCount > 0 and dataset.RasterXSize > 0
    dataset = None
    return ok


def build_vrt(paths, out_path):
    """Mosaic rasters into a GDAL VRT. Returns the path written."""
    from osgeo import gdal

    gdal.UseExceptions()
    vrt = gdal.BuildVRT(out_path, list(paths))
    if vrt is None:
        raise RuntimeError("GDAL could not build a VRT from those files")
    vrt.FlushCache()
    del vrt  # closing the dataset is what actually writes the .vrt
    return out_path


def tile_grid_layer(tile_names, layer_name):
    """A memory polygon layer of 1°x1° cells for the portal's current selection.

    Tile names are the usual ``N28E077`` / ``S09W073`` form: hemisphere letter,
    two-digit latitude, hemisphere letter, three-digit longitude, naming the
    south-west corner of the cell.
    """
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", layer_name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(
        [_field("tile", "string"), _field("min_lat", "int"), _field("min_lon", "int")]
    )
    layer.updateFields()

    features = []
    for name in tile_names:
        try:
            lat = (1 if name[0] == "N" else -1) * int(name[1:3])
            lon = (1 if name[3] == "E" else -1) * int(name[4:7])
        except (IndexError, ValueError):
            continue
        rect = [
            QgsPointXY(lon, lat),
            QgsPointXY(lon + 1, lat),
            QgsPointXY(lon + 1, lat + 1),
            QgsPointXY(lon, lat + 1),
        ]
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolygonXY([rect]))
        feature.setAttributes([name, lat, lon])
        features.append(feature)

    provider.addFeatures(features)
    layer.updateExtents()
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    return layer, len(features)

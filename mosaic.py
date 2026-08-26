"""Merging downloaded tiles into one raster, and balancing how it reads.

A folder of DEM tiles is awkward to work with: QGIS stretches each one over its
own min/max, so neighbouring tiles come out at visibly different brightnesses,
SRTM's void value (-32768) drags any stretch that includes it down into the
seabed, and thirty layers in the tree is thirty layers in the tree.  This module
turns a download into a single file that reads as one surface.

"Balancing" here means three different things, because a DEM mosaic can be
uneven in three different ways:

* **Voids.**  A tile's fill value is data as far as a naive stretch is
  concerned.  Declaring it nodata is what makes the rest of the range usable,
  and it is by far the biggest single improvement to how a mosaic looks.
* **Seams.**  Tiles from different missions - or the same mission on different
  passes - can sit a metre or two apart in elevation, which shows up as a
  visible step along the join.  :func:`seam_differences` measures every shared
  edge and :func:`solve_offsets` finds the per-tile shift that best closes them
  all at once.  For tiles from one product this usually measures zero, and that
  is a useful thing to be told rather than a wasted pass.
* **Stretch.**  One colour ramp across the whole mosaic, clipped to percentiles
  so a single spike cannot flatten everything else into one shade.

GDAL does the heavy lifting throughout; nothing here re-implements what a warp
or a VRT already does well.
"""

import os

#: Fill values DEM producers use for "no measurement here".  SRTM and NASADEM
#: write -32768; Copernicus and ALOS write -9999 in places.  A merge declares
#: whichever of these the source already carries, and otherwise offers -32768
#: for the integer products, because a void that is not nodata poisons the
#: stretch of everything around it.
VOID_VALUES = (-32768.0, -32767.0, -9999.0, -999.0)

#: Below this, a seam is agreement rather than a step worth rewriting a file for.
SEAM_TOLERANCE_M = 0.05

#: Sample budget for statistics: a decimated read, so a 40-tile mosaic is a
#: fraction of a second rather than a full pass over gigabytes.
STATS_SAMPLES = 1200

#: Where the colour stretch is clipped, as percentiles of the real elevations.
#:
#: Gentle on purpose.  The clip exists to survive an *undeclared* sentinel that
#: slipped past the void filter, not to compress the terrain - and on a scene
#: with real relief a 2-98 clip is brutal: over four merged SRTM tiles it kept
#: only 41% of the range, rendering everything above 1205 m of a 2648 m mosaic
#: as the same white.  0.1-99.9 keeps about three quarters of the relief while
#: still clamping fewer than one pixel in 500.
CLIP_LOW, CLIP_HIGH = 0.1, 99.9


#: When a merge is worth asking about first.
#:
#: A mosaic is a rectangle, so it always spans the bounding box of the tiles
#: chosen - and every cell inside that box which was *not* selected still has to
#: exist in the file as nodata.  Pick two tiles a continent apart and the mosaic
#: between them is mostly emptiness.  It is not wrong, and it compresses to
#: almost nothing on disk, but the pixel dimensions are real: GDAL still has to
#: write and index them, and QGIS still has to open them.
#:
#: Two ways to earn a question, because there are two ways to be surprised: a
#: mosaic that is mostly hole, and a mosaic that is simply enormous.
MOSAIC_WARN_FILL = 0.6            # under 60% of the bounding box has data
MOSAIC_WARN_PIXELS = 2e9          # or the thing is over two gigapixels


def mosaic_estimate(cells, pixels_per_degree):
    """What merging these 1-degree cells would produce.

    ``cells`` is the selection as {(lat, lon)}.  Returns a dict describing the
    mosaic that would be written - its size in pixels, how much of it would
    actually contain data, and how much would be nodata padding.
    """
    if not cells:
        return None
    lats = [lat for lat, _ in cells]
    lons = [lon for _, lon in cells]
    rows = max(lats) - min(lats) + 1
    columns = max(lons) - min(lons) + 1
    box_cells = rows * columns
    width = columns * pixels_per_degree + 1
    height = rows * pixels_per_degree + 1
    return {
        "width": width,
        "height": height,
        "pixels": float(width) * height,
        "cells": len(cells),
        "box_cells": box_cells,
        "empty_cells": box_cells - len(cells),
        "fill": float(len(cells)) / box_cells,
        "degrees": (rows, columns),
    }


def needs_confirmation(estimate):
    """Is this mosaic sparse enough, or big enough, to be worth a question?"""
    if not estimate:
        return False
    return (estimate["fill"] < MOSAIC_WARN_FILL
            or estimate["pixels"] > MOSAIC_WARN_PIXELS)


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def _open(path):
    """Open a raster for inspection, or None if GDAL cannot read it.

    ``gdal.UseExceptions()`` is on throughout this module - it is how the merge
    surfaces a real failure instead of returning a silent None - but it also
    means ``gdal.Open`` *raises* on an unreadable file rather than returning
    None.  That turned every ``if dataset is None: continue`` guard below into
    dead code, and let a single truncated tile abort the seam measurement and
    the stretch for an entire download.  The analysis passes are advisory, so
    they skip what they cannot read; :func:`merge` deliberately still fails
    loudly, because silently dropping a tile from a mosaic loses data.
    """
    gdal = _gdal()
    try:
        return gdal.Open(path)
    except RuntimeError:
        return None


def readable(paths):
    """Just the rasters GDAL can actually open, in the order given."""
    kept = []
    for path in paths:
        dataset = _open(path)
        if dataset is not None:
            dataset = None
            kept.append(path)
    return kept


def _extent(dataset):
    """(ulx, uly, lrx, lry) in the dataset's own CRS."""
    gt = dataset.GetGeoTransform()
    return (gt[0], gt[3],
            gt[0] + gt[1] * dataset.RasterXSize,
            gt[3] + gt[5] * dataset.RasterYSize)


def _pixel_size(dataset):
    gt = dataset.GetGeoTransform()
    return abs(gt[1]), abs(gt[5])


def detected_nodata(paths):
    """The nodata value these rasters already declare, or None if they declare none."""
    for path in paths:
        dataset = _open(path)
        if dataset is None:
            continue
        value = dataset.GetRasterBand(1).GetNoDataValue()
        dataset = None
        if value is not None:
            return value
    return None


def likely_void(paths, sample=3):
    """A fill value present in the data but not declared as nodata.

    Only values from :data:`VOID_VALUES` are ever returned - this looks for a
    known sentinel, it does not guess that the minimum of a tile is a void.
    """
    import numpy

    for path in paths[:sample]:
        dataset = _open(path)
        if dataset is None:
            continue
        band = dataset.GetRasterBand(1)
        array = band.ReadAsArray(
            buf_xsize=min(dataset.RasterXSize, 512),
            buf_ysize=min(dataset.RasterYSize, 512))
        dataset = None
        if array is None:
            continue
        low = float(numpy.min(array))
        for candidate in VOID_VALUES:
            if abs(low - candidate) < 0.5:
                return candidate
    return None


# ── seams ────────────────────────────────────────────────────────────────
def seam_differences(paths):
    """Median elevation difference along every shared edge.

    Returns ``[(i, j, difference, samples), ...]`` indexing into ``paths``.  A
    positive difference means tile ``i`` sits that far above tile ``j`` where
    they meet.

    Tiles are compared through a one-pixel read of the strip they share, sampled
    onto a common grid by GDAL, so this does not assume the tiles are on the same
    grid, the same size, or named in any particular way - only that they overlap
    or abut.
    """
    gdal = _gdal()
    import numpy

    datasets = [_open(path) for path in paths]

    found = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            left, right = datasets[i], datasets[j]
            if left is None or right is None:
                continue
            strip = _shared_windows(left, right)
            if strip is None:
                continue
            window_a, window_b, width, height = strip
            try:
                a = _read_window(gdal, left, window_a, width, height)
                b = _read_window(gdal, right, window_b, width, height)
            except RuntimeError:
                continue
            if a is None or b is None:
                continue
            valid = numpy.isfinite(a) & numpy.isfinite(b)
            for void in VOID_VALUES:
                valid &= (numpy.abs(a - void) > 0.5) & (numpy.abs(b - void) > 0.5)
            count = int(numpy.count_nonzero(valid))
            if count < 8:
                continue
            difference = float(numpy.median(a[valid] - b[valid]))
            found.append((i, j, difference, count))

    for dataset in datasets:
        del dataset
    return found


def _shared_windows(left, right):
    """The two windows to compare, one per raster.

    Neighbouring DEM tiles come in two flavours and they need different reads:

    * **Overlapping** - SRTM and friends repeat the edge row, so the tiles share
      a strip of real ground.  Both rasters are read over that same strip, and
      the difference is exact.
    * **Abutting** - the tiles meet on a line and share no ground at all.  Then
      the comparison is the last row inside one against the first row inside the
      other: adjacent ground rather than identical ground, which is the standard
      way to detect a step and is good to a fraction of the terrain's own slope.

    Never reads outside either raster.  The first version widened an abutment by
    a pixel in each direction, which ran off the edge of both tiles; GDAL filled
    the missing rows with zero, and those zeros went straight into the median.
    Returns ``(window_a, window_b, width, height)`` or None if the two are not
    neighbours at all.
    """
    a_ulx, a_uly, a_lrx, a_lry = _extent(left)
    b_ulx, b_uly, b_lrx, b_lry = _extent(right)
    px, py = _pixel_size(left)

    ulx, lrx = max(a_ulx, b_ulx), min(a_lrx, b_lrx)
    uly, lry = min(a_uly, b_uly), max(a_lry, b_lry)
    overlap_x, overlap_y = lrx - ulx, uly - lry

    # Half a pixel of tolerance, because an exactly-one-pixel overlap is a float
    # comparison that can land on either side of an exact test.
    if overlap_x < -px * 0.5 or overlap_y < -py * 0.5:
        return None                     # a gap: not neighbours
    thin_x, thin_y = overlap_x < px * 0.5, overlap_y < py * 0.5
    if thin_x and thin_y:
        return None                     # a corner touch carries no information

    window_a = [ulx, uly, lrx, lry]
    window_b = [ulx, uly, lrx, lry]
    if thin_x:
        join = (ulx + lrx) / 2.0
        a_left = a_lrx <= b_lrx
        window_a[0], window_a[2] = ((join - px, join) if a_left else (join, join + px))
        window_b[0], window_b[2] = ((join, join + px) if a_left else (join - px, join))
    if thin_y:
        join = (uly + lry) / 2.0
        a_above = a_uly >= b_uly
        window_a[1], window_a[3] = ((join, join - py) if a_above else (join + py, join))
        window_b[1], window_b[3] = ((join + py, join) if a_above else (join, join - py))

    width = max(1, int(round((window_a[2] - window_a[0]) / px)))
    height = max(1, int(round((window_a[1] - window_a[3]) / py)))
    # A whole-tile overlap is not a seam; cap the read so this stays cheap.
    return window_a, window_b, min(width, 4096), min(height, 4096)


def _read_window(gdal, dataset, window, width, height):
    """Read one georeferenced window of a raster as float, on a given grid."""
    import numpy

    clip = gdal.Translate("", dataset, format="MEM", projWin=list(window),
                          width=width, height=height, outputType=gdal.GDT_Float64)
    if clip is None:
        return None
    array = clip.GetRasterBand(1).ReadAsArray()
    nodata = clip.GetRasterBand(1).GetNoDataValue()
    clip = None
    if array is None:
        return None
    array = array.astype("float64")
    if nodata is not None:
        array[numpy.abs(array - nodata) < 0.5] = numpy.nan
    return array


def solve_offsets(count, differences):
    """The per-tile shift that best closes every measured seam at once.

    Each seam gives one equation ``offset[i] - offset[j] = -difference``.  The
    system is under-determined on its own - adding a constant to every tile
    satisfies it just as well - so one more equation pins the mean offset at
    zero.  The mosaic is then levelled without being moved off its own datum,
    which matters: these are elevations, not brightness values.

    Weighted by the number of pixels each seam was measured over, so a long
    shared edge counts for more than a corner touch.
    """
    import numpy

    if not differences:
        return [0.0] * count

    rows = len(differences) + 1
    matrix = numpy.zeros((rows, count))
    vector = numpy.zeros(rows)
    for row, (i, j, difference, samples) in enumerate(differences):
        weight = min(1.0, samples / 1000.0) ** 0.5
        matrix[row, i] = weight
        matrix[row, j] = -weight
        vector[row] = -difference * weight
    matrix[-1, :] = 1.0                     # mean offset = 0
    vector[-1] = 0.0

    solution, _, _, _ = numpy.linalg.lstsq(matrix, vector, rcond=None)
    return [float(value) for value in solution]


def apply_offsets(paths, offsets, directory, nodata=None):
    """Write offset-corrected copies of the tiles that need one.

    Tiles inside :data:`SEAM_TOLERANCE_M` of level are passed through untouched -
    rewriting a 26 MB tile to add 4 mm to it would be a waste of a minute and a
    gigabyte.  Returns ``(paths, adjusted_count)``.

    A corrected tile is written as float32 whatever it started as: the shifts are
    sub-metre, and rounding them back into an int16 grid would put the seam
    straight back.  A mosaic that needed levelling is therefore larger than one
    that did not, which is the honest cost of the correction.
    """
    gdal = _gdal()
    import numpy

    corrected = []
    adjusted = 0
    for path, offset in zip(paths, offsets):
        if abs(offset) < SEAM_TOLERANCE_M:
            corrected.append(path)
            continue
        source = _open(path)
        if source is None:
            corrected.append(path)
            continue
        band = source.GetRasterBand(1)
        array = band.ReadAsArray().astype("float32")
        fill = band.GetNoDataValue()
        if fill is None:
            fill = nodata
        mask = None
        if fill is not None:
            mask = numpy.abs(array - fill) < 0.5
        array += offset
        if mask is not None:
            array[mask] = fill

        out_path = os.path.join(
            directory, "%s_levelled.tif"
            % os.path.splitext(os.path.basename(path))[0])
        driver = gdal.GetDriverByName("GTiff")
        target = driver.Create(out_path, source.RasterXSize, source.RasterYSize,
                               1, gdal.GDT_Float32,
                               options=["COMPRESS=DEFLATE", "PREDICTOR=3",
                                        "TILED=YES", "BIGTIFF=IF_SAFER"])
        target.SetGeoTransform(source.GetGeoTransform())
        target.SetProjection(source.GetProjection())
        out_band = target.GetRasterBand(1)
        if fill is not None:
            out_band.SetNoDataValue(float(fill))
        out_band.WriteArray(array)
        out_band.FlushCache()
        target = source = None
        corrected.append(out_path)
        adjusted += 1
    return corrected, adjusted


# ── merging ──────────────────────────────────────────────────────────────
def merge(paths, out_path, nodata=None, progress=None):
    """Mosaic rasters into one tiled, compressed, overviewed GeoTIFF.

    A VRT first, then one translate: GDAL reads each source exactly once and
    writes the result straight out, which is both faster and far lighter on
    memory than merging arrays by hand.  Overviews are built afterwards because
    a merged DEM is usually opened zoomed out, and without them QGIS reads every
    pixel of every tile to draw a thumbnail.
    """
    gdal = _gdal()
    if not paths:
        raise RuntimeError("nothing to merge")

    vrt_path = os.path.splitext(out_path)[0] + ".vrt"
    build = {"resolution": "highest"}
    if nodata is not None:
        build["srcNodata"] = nodata
        build["VRTNodata"] = nodata
    vrt = gdal.BuildVRT(vrt_path, list(paths), options=gdal.BuildVRTOptions(**build))
    if vrt is None:
        raise RuntimeError("GDAL could not mosaic those files")
    vrt.FlushCache()

    band = vrt.GetRasterBand(1)
    # PREDICTOR is not one-size-fits-all: 2 is horizontal differencing for
    # integers, 3 is the floating-point variant.  Using 2 on float data makes
    # the file *larger*, which is a quiet way to waste a lot of disk.
    floating = band.DataType in (gdal.GDT_Float32, gdal.GDT_Float64)
    creation = ["COMPRESS=DEFLATE", "PREDICTOR=%d" % (3 if floating else 2),
                "TILED=YES", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"]

    result = gdal.Translate(out_path, vrt, creationOptions=creation,
                            callback=progress)
    vrt = None
    if result is None:
        raise RuntimeError("GDAL could not write the merged raster")
    result = None
    try:
        os.remove(vrt_path)
    except OSError:
        pass

    dataset = gdal.Open(out_path, gdal.GA_Update)
    if dataset is not None:
        levels = [2, 4, 8, 16, 32]
        try:
            dataset.BuildOverviews("AVERAGE", levels)
        except RuntimeError:
            pass                            # overviews are a nicety, not the file
        dataset = None
    return out_path


def merge_name(product_key, paths):
    """``srtmgl1_mosaic_12_tiles.tif`` - says what it is without being read."""
    return "%s_mosaic_%d_tile%s.tif" % (product_key.lower(), len(paths),
                                        "" if len(paths) == 1 else "s")


# ── stretch and colour ───────────────────────────────────────────────────
def _samples(path):
    """Real elevations from a decimated read of one raster, voids dropped."""
    import numpy

    dataset = _open(path)
    if dataset is None:
        return None
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray(
        buf_xsize=min(dataset.RasterXSize, STATS_SAMPLES),
        buf_ysize=min(dataset.RasterYSize, STATS_SAMPLES))
    nodata = band.GetNoDataValue()
    dataset = None
    if array is None:
        return None

    values = array.astype("float64").ravel()
    keep = numpy.isfinite(values)
    if nodata is not None:
        keep &= numpy.abs(values - nodata) > 0.5
    for void in VOID_VALUES:
        keep &= numpy.abs(values - void) > 0.5
    return values[keep]


def percentiles(path, low=CLIP_LOW, high=CLIP_HIGH):
    """(low, high) elevation percentiles, voids excluded, from a decimated read.

    Percentiles rather than min/max because one unmasked void, one spike off a
    radar return, or a single -9999 in a corner is enough to push a min/max
    stretch into uselessness - which is exactly the symptom this is here to fix.
    See :data:`CLIP_LOW` for why the clip is as gentle as it is.
    """
    import numpy

    values = _samples(path)
    if values is None or values.size < 16:
        return None
    return (float(numpy.percentile(values, low)),
            float(numpy.percentile(values, high)))


def pooled_percentiles(paths, low=CLIP_LOW, high=CLIP_HIGH, limit=24):
    """One stretch for a whole set of tiles, pooled across them.

    This is what balances a download that was *not* merged.  Rendered on its own
    each tile gets its own min/max, so a plateau tile and a mountain tile come
    out looking like different datasets; giving every layer the same range makes
    the folder read as one surface, which is the same end the merge reaches by a
    different route.  Samples are pooled rather than each file's percentiles
    averaged, because the average of percentiles is not a percentile.
    """
    import numpy

    pool = []
    step = max(1, len(paths) // limit)
    for path in paths[::step][:limit]:
        values = _samples(path)
        if values is not None and values.size:
            pool.append(values)
    if not pool:
        return None
    values = numpy.concatenate(pool)
    if values.size < 16:
        return None
    return (float(numpy.percentile(values, low)),
            float(numpy.percentile(values, high)))


#: A hypsometric ramp: the convention topographic maps have used for a century -
#: greens for lowland, tan and brown for hills, grey and white for peaks.  Read
#: as fractions of the stretched range.
LAND_RAMP = (
    (0.00, "#4a7a3f"),   # lowland green - olive rather than teal, so a plain
    (0.12, "#7ba05b"),   # that fills most of the range still reads as land
    (0.28, "#a8b76b"),
    (0.42, "#d6c982"),
    (0.55, "#d1a563"),
    (0.68, "#b8834a"),
    (0.80, "#97673d"),
    (0.90, "#9c9186"),   # rock
    (1.00, "#ffffff"),   # snow
)
#: Sea level and below.  Only used when the mosaic actually contains it.
WATER_COLOUR = "#1d3f6e"


def colour_stops(low, high):
    """The ramp's stops in elevation units, given the stretched range."""
    stops = []
    span = high - low
    if span <= 0:
        return [(low, LAND_RAMP[0][1])]
    if low < 0 < high:
        # Anchor the land ramp at sea level rather than at the minimum, so the
        # green-to-brown progression means the same thing on a coastal mosaic as
        # on an inland one.
        stops.append((low, WATER_COLOUR))
        stops.append((0.0, WATER_COLOUR))
        land_low, land_span = 0.0, high
    else:
        land_low, land_span = low, span
    for fraction, colour in LAND_RAMP:
        stops.append((land_low + fraction * land_span, colour))
    return stops


def style_dem(layer, low=None, high=None):
    """Render an elevation raster with one balanced ramp across the whole file.

    Returns the (low, high) actually used, or None if the layer could not be
    measured.  Called on the merged file, this is what makes a mosaic read as
    one surface instead of a patchwork.
    """
    from qgis.PyQt.QtGui import QColor
    from qgis.core import (
        QgsColorRampShader,
        QgsRasterShader,
        QgsSingleBandPseudoColorRenderer,
    )

    if low is None or high is None:
        measured = percentiles(layer.source())
        if measured is None:
            return None
        low, high = measured

    ramp = QgsColorRampShader(low, high)
    ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(value, QColor(colour), _label(value))
        for value, colour in colour_stops(low, high)
    ])

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return low, high


def _label(value):
    return "%d m" % round(value)

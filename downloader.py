"""Fetching the selected data, off the GUI thread.

Two shapes of work, matching the two shapes of product:

* **Tiled products** - one HTTP GET per selected cell, four at a time (the page
  uses the same concurrency).  Mapzen's ``.hgt.gz`` is gunzipped on arrival.
* **Mosaic products** (ANADEM, GEDTM30) - a single windowed read.  The page does
  this with geotiff.js over HTTP range requests; GDAL does the same thing through
  ``/vsicurl/``, reading only the tiles of the COG that intersect the selection
  rather than the 66 GB or 403 GB the file actually is.

Optional reprojection reuses :mod:`layers`, which already knows to skip the warp
when the data is in the requested CRS to begin with.
"""

import os
import gzip
import shutil
import threading

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import Qgis, QgsMessageLog, QgsTask

from . import layers
from . import net
from .products import tile_name

#: Parallel network requests, the same number the web app uses.
CONCURRENCY = 4


def log(message, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(message, "Global DEM Portal", level)


#: Formats whose georeferencing lives in the file *name*.  GDAL's SRTMHGT driver
#: reads N28E077.hgt's corner out of the name and from nowhere else - the file
#: itself is a bare grid of big-endian int16 with no header at all.
NAME_IS_GEOREFERENCE = (".hgt", ".hgt.gz")


def unique_path(directory, filename):
    """``name.tif`` -> ``name (2).tif`` rather than overwriting a previous download."""
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    if filename.lower().endswith(NAME_IS_GEOREFERENCE):
        # Overwrite instead.  Renaming a Mapzen tile to "N28E077 (2).hgt" does
        # not make a second copy, it makes an unopenable file: GDAL refuses it
        # outright, so the download would be reported as successful and then be
        # unreadable by the merge, by QGIS, and by everything else.  The same
        # tile fetched twice from the same mirror is the same bytes, so there is
        # nothing to preserve by keeping both.
        return candidate
    stem, ext = os.path.splitext(filename)
    index = 2
    while True:
        candidate = os.path.join(directory, "%s (%d)%s" % (stem, index, ext))
        if not os.path.exists(candidate):
            return candidate
        index += 1


def gunzip(path):
    """Expand a .hgt.gz in place, returning the expanded path."""
    target = path[:-3] if path.lower().endswith(".gz") else path + ".hgt"
    with gzip.open(path, "rb") as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink)
    os.remove(path)
    return target


class DownloadTask(QgsTask):
    """Downloads a selection and reports what landed on disk.

    ``finished_with`` carries (paths, errors, reprojected, skipped).
    """

    finished_with = pyqtSignal(list, list, int, int)

    def __init__(self, product, selection, directory, target_crs="",
                 keep_originals=True, merge_tiles=False, balance_seams=False):
        super().__init__("Downloading %s" % product.label, QgsTask.Flag.CanCancel)
        self.product = product
        self.selection = sorted(selection)
        self.directory = directory
        self.target_crs = target_crs or ""
        self.keep_originals = keep_originals
        self.merge_tiles = merge_tiles
        self.balance_seams = balance_seams
        self.paths = []
        self.errors = []
        self.reprojected = 0
        self.skipped = 0
        #: Set when the tiles were mosaicked, so the panel can load and style it.
        self.merged_path = ""
        #: Human-readable account of what the balancing pass found and did.
        self.balance_note = ""
        self._lock = threading.Lock()
        self._done = 0

    # ── QgsTask ──────────────────────────────────────────────────────────
    def run(self):
        try:
            if not os.path.isdir(self.directory):
                os.makedirs(self.directory)
            if self.product.mosaic:
                self._run_mosaic()
            else:
                self._run_tiles()
            # Merge before reprojecting, not after: warping one mosaic is both
            # faster than warping thirty tiles and cleaner at the joins, because
            # the resampling kernel never has to stop at a tile edge.
            if not self.isCanceled():
                self._merge()
            if not self.isCanceled():
                self._reproject()
        except Exception as exc:  # noqa: BLE001 - report, never crash the task thread
            self.errors.append(str(exc))
            log("Download failed: %s" % exc, Qgis.MessageLevel.Critical)
        return not self.isCanceled()

    def finished(self, ok):  # noqa: N802 - QGIS API
        self.finished_with.emit(self.paths, self.errors, self.reprojected,
                                self.skipped)

    # ── tiled products ───────────────────────────────────────────────────
    def _run_tiles(self):
        names = [tile_name(lat, lon) for lat, lon in self.selection]
        total = len(names)
        queue = list(names)
        queue_lock = threading.Lock()

        def worker():
            while not self.isCanceled():
                with queue_lock:
                    if not queue:
                        return
                    name = queue.pop(0)
                self._fetch_tile(name, total)

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(CONCURRENCY, max(1, total)))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _fetch_tile(self, name, total):
        url = self.product.url(name)
        try:
            data = net.get(url, accept_missing=True)
        except Exception as exc:  # noqa: BLE001 - network, DNS, proxy, timeouts
            with self._lock:
                self.errors.append("%s: %s" % (name, exc))
            self._advance(total)
            return
        if data is None:
            # A cell on the land mask that this particular mission never filled.
            with self._lock:
                self.errors.append("%s: no tile on the mirror (404)" % name)
            self._advance(total)
            return

        path = unique_path(self.directory, self.product.filename(name))
        with open(path, "wb") as handle:
            handle.write(data)
        if path.lower().endswith(".gz"):
            path = gunzip(path)
        with self._lock:
            self.paths.append(path)
        self._advance(total)

    def _advance(self, total):
        with self._lock:
            self._done += 1
            done = self._done
        # Fetching is the whole job unless a merge follows, in which case it is
        # the first two thirds - so the bar keeps moving instead of sitting at
        # 100% through a mosaic that can take a minute.
        span = 65.0 if self.merge_tiles else 100.0
        self.setProgress(span * done / max(1, total))

    # ── mosaic products ──────────────────────────────────────────────────
    def _run_mosaic(self):
        """Read just the selected window out of a single huge COG."""
        from osgeo import gdal

        from .geometry import selection_bbox

        box = selection_bbox(set(self.selection))
        if box is None:
            self.errors.append("Nothing selected")
            return

        source = "/vsicurl/" + self.product.source_url
        name = "%s_%s.tif" % (self.product.key.lower(), _bbox_slug(box))
        path = unique_path(self.directory, name)

        gdal.UseExceptions()
        self.setProgress(5)
        try:
            # projWin is (ulx, uly, lrx, lry), and it defaults to the *source's*
            # CRS - which is not always EPSG:4326: ANADEM is published in
            # EPSG:4674 (SIRGAS 2000).  The two agree to well under a pixel, so
            # this worked by luck; naming the window's CRS makes GDAL convert it
            # properly instead, and keeps working if a source is ever reprojected.
            result = gdal.Translate(
                path, source,
                projWin=[box.xMinimum(), box.yMaximum(),
                         box.xMaximum(), box.yMinimum()],
                projWinSRS="EPSG:4326",
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"],
                callback=self._gdal_progress,
            )
        except Exception as exc:  # noqa: BLE001 - surface the GDAL message verbatim
            self.errors.append("%s: %s" % (self.product.key, exc))
            return
        if result is None:
            self.errors.append("%s: the clip returned nothing" % self.product.key)
            return
        result = None                      # flush and close before anything reads it
        self.paths.append(path)
        self.setProgress(100)

    def _gdal_progress(self, complete, message, data):  # noqa: ARG002 - GDAL signature
        self.setProgress(max(5.0, complete * 100.0))
        return 0 if self.isCanceled() else 1

    # ── merging and balancing ────────────────────────────────────────────
    def _merge(self):
        """Mosaic the downloaded tiles into one raster, optionally levelled first.

        Left alone unless there is something to merge: one file is already a
        mosaic, and the two COG products arrive as a single clip by definition.
        """
        if not self.merge_tiles or len(self.paths) < 2:
            return
        from . import mosaic

        try:
            # A tile that GDAL cannot read - a truncated download, a mirror that
            # served an error page - is dropped from the mosaic and reported,
            # rather than being allowed to fail the merge for everything else.
            sources = mosaic.readable(self.paths)
            unreadable = len(self.paths) - len(sources)
            if unreadable:
                self.errors.append("%d file%s could not be read and were left "
                                   "out of the mosaic"
                                   % (unreadable, "" if unreadable == 1 else "s"))
            if len(sources) < 2:
                return
            nodata = mosaic.detected_nodata(sources)
            if nodata is None:
                nodata = mosaic.likely_void(sources)

            if self.balance_seams:
                sources = self._balance(mosaic, sources, nodata)
            if self.isCanceled():
                return

            out_path = unique_path(self.directory,
                                   mosaic.merge_name(self.product.key, self.paths))
            self.setProgress(70)
            mosaic.merge(sources, out_path, nodata=nodata,
                         progress=self._merge_progress)
            self.merged_path = out_path
            # The tiles stay on disk: they are the download, and a mosaic is a
            # derived convenience.  Only the levelled intermediates are cleaned
            # up, because nothing else will ever want them.
            for path in sources:
                if path not in self.paths and path.endswith("_levelled.tif"):
                    layers.discard_original(path, only_within=self.directory)
            self.paths = [out_path]
        except Exception as exc:  # noqa: BLE001 - a failed merge must not lose the tiles
            self.errors.append("merge failed (%s)" % exc)
            log("Merge failed: %s" % exc, Qgis.MessageLevel.Warning)

    def _balance(self, mosaic, sources, nodata):
        """Level the tiles to each other, and record what that took."""
        differences = mosaic.seam_differences(sources)
        if not differences:
            self.balance_note = "no shared edges to balance"
            return sources
        worst = max(abs(value) for _, _, value, _ in differences)
        offsets = mosaic.solve_offsets(len(sources), differences)
        if worst < mosaic.SEAM_TOLERANCE_M:
            # Tiles from one mission normally agree exactly, and saying so is
            # more useful than silently rewriting every file to add a millimetre.
            self.balance_note = ("seams already agree (largest step %.3f m across "
                                 "%d edges)" % (worst, len(differences)))
            return sources
        levelled, adjusted = mosaic.apply_offsets(sources, offsets, self.directory,
                                                  nodata=nodata)
        self.balance_note = ("%d of %d tiles levelled, largest step was %.2f m"
                             % (adjusted, len(sources), worst))
        return levelled

    def _merge_progress(self, complete, message, data):  # noqa: ARG002 - GDAL signature
        self.setProgress(70.0 + complete * 25.0)
        return 0 if self.isCanceled() else 1

    # ── reprojection ─────────────────────────────────────────────────────
    def _reproject(self):
        if not self.target_crs or not self.paths:
            return
        converted = []
        for path in self.paths:
            if self.isCanceled():
                return
            try:
                # reproject_raster returns (path, whether it actually warped) -
                # it makes the same "is this already in that CRS?" check itself,
                # and reporting it back is what lets the count below be right.
                new_path, warped = layers.reproject_raster(path, self.target_crs)
                if not warped:
                    # Already in the requested CRS: warping 4326 to 4326 would
                    # cost minutes and change nothing, so the file is left alone.
                    self.skipped += 1
                    converted.append(path)
                    continue
                self.reprojected += 1
                converted.append(new_path)
                if self.merged_path == path:
                    self.merged_path = new_path
                if not self.keep_originals:
                    layers.discard_original(path, only_within=self.directory)
            except Exception as exc:  # noqa: BLE001 - keep the original on any failure
                self.errors.append("%s: reprojection failed (%s)"
                                   % (os.path.basename(path), exc))
                converted.append(path)
        self.paths = converted


def _bbox_slug(box):
    """S06W074_N12W034 - names a clip after the corners it covers."""
    def corner(lat, lon):
        return "%s%02d%s%03d" % ("N" if lat >= 0 else "S", abs(int(lat)),
                                 "E" if lon >= 0 else "W", abs(int(lon)))

    return "%s_%s" % (corner(box.yMinimum(), box.xMinimum()),
                      corner(box.yMaximum(), box.xMaximum()))

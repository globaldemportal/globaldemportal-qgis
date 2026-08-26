"""The nine DEM products, transcribed from the web app's PRODUCTS table.

Labels, coverage bands, per-tile size estimates and - most importantly - the URL
and filename builders are the same as index.html's, so this panel fetches
byte-for-byte the same files from the same mirrors.

Two of the nine are not tiled at all.  ANADEM (66 GB) and GEDTM30 (403 GB) are
single continent- and globe-spanning Cloud-Optimized GeoTIFFs; ``mosaic`` routes
those to a windowed read of the selected bounding box instead of a tile list.
"""

OT_BASE = "https://opentopography.s3.sdsc.edu/raster"
#: Frozen AWS Open Data snapshot of SRTM-derived "skadi" tiles; raw gzip .hgt.
MAPZEN_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"


def tile_name(lat, lon):
    """N28E077 - tiles are named for their lower-left corner."""
    return "%s%02d%s%03d" % (
        "N" if lat >= 0 else "S", abs(lat),
        "E" if lon >= 0 else "W", abs(lon),
    )


def parse_tile_name(name):
    """N28E077 -> (28, 77).  The inverse of :func:`tile_name`."""
    lat = int(name[1:3]) * (1 if name[0] == "N" else -1)
    lon = int(name[4:7]) * (1 if name[3] == "E" else -1)
    return lat, lon


def _cop_name(res, name):
    """Copernicus encodes lat in 2 digits, lon in 3, with _00 sub-degree fields."""
    lat, lon = parse_tile_name(name)
    return "Copernicus_DSM_%s_%s%02d_00_%s%03d_00_DEM.tif" % (
        res, "N" if lat >= 0 else "S", abs(lat), "E" if lon >= 0 else "W", abs(lon),
    )


def _alos_name(name):
    """ALOS encodes lat in 3 digits, lon in 3."""
    lat, lon = parse_tile_name(name)
    return "ALPSMLC30_%s%03d%s%03d_DSM.tif" % (
        "N" if lat >= 0 else "S", abs(lat), "E" if lon >= 0 else "W", abs(lon),
    )


def _gl3_url(name):
    """SRTM GL3 is filed under three latitude directories on the mirror."""
    lat, _ = parse_tile_name(name)
    folder = "South" if lat < 0 else "North/North_0_29" if lat < 30 else "North/North_30_60"
    return "%s/SRTM_GL3/SRTM_GL3_srtm/%s/%s.tif" % (OT_BASE, folder, name)


class Product(object):
    """One entry of the page's PRODUCTS table."""

    def __init__(self, key, label, res, kind, lat_min, lat_max, size_mb=0,
                 url=None, filename=None, mosaic=False, source_url="",
                 source_size_gb=0, lon_min=-180, lon_max=180, note="", doi=""):
        self.key = key
        self.label = label
        self.res = res                  # '1arc' | '3arc'
        self.kind = kind                # 'DSM' | 'DTM'
        self.lat_min = lat_min
        self.lat_max = lat_max
        self.lon_min = lon_min
        self.lon_max = lon_max
        self.size_mb = size_mb
        self._url = url
        self._filename = filename
        self.mosaic = mosaic
        self.source_url = source_url
        self.source_size_gb = source_size_gb
        self.note = note
        self.doi = doi

    def url(self, name):
        return self._url(name)

    def filename(self, name):
        return self._filename(name)

    def covers(self, lat, lon):
        """Whether a 1x1 degree cell is inside this product's coverage."""
        return (self.lat_min <= lat < self.lat_max
                and self.lon_min <= lon < self.lon_max)

    @property
    def resolution_label(self):
        return "90m" if self.res == "3arc" else "30m"

    @property
    def pixels_per_degree(self):
        """3600 at 1 arc-second, 1200 at 3 - the grid the tiles are posted on.

        A tile is one more than this across, because the edge row is repeated;
        a mosaic of n tiles is therefore ``n * pixels_per_degree + 1``.
        """
        return 1200 if self.res == "3arc" else 3600


PRODUCTS = {
    "SRTMGL1": Product(
        "SRTMGL1", "SRTM GL1 - 1 arc-sec (~30 m)", "1arc", "DSM", -56, 60, 14,
        url=lambda t: "%s/SRTM_GL1/SRTM_GL1_srtm/%s.tif" % (OT_BASE, t),
        filename=lambda t: "%s.tif" % t),
    "NASADEM": Product(
        "NASADEM", "NASADEM - void-filled SRTM (~30 m)", "1arc", "DSM", -56, 60, 14,
        url=lambda t: "%s/NASADEM/NASADEM_be/NASADEM_HGT_%s.tif" % (OT_BASE, t.lower()),
        filename=lambda t: "NASADEM_HGT_%s.tif" % t.lower()),
    "ALOS": Product(
        "ALOS", "ALOS AW3D30 - JAXA (~30 m)", "1arc", "DSM", -82, 82, 20,
        url=lambda t: "%s/AW3D30/AW3D30_global/%s" % (OT_BASE, _alos_name(t)),
        filename=_alos_name),
    "COP30": Product(
        "COP30", "Copernicus GLO-30 - TanDEM-X (~30 m)", "1arc", "DSM", -90, 90, 50,
        url=lambda t: "%s/COP30/COP30_hh/%s" % (OT_BASE, _cop_name("10", t)),
        filename=lambda t: _cop_name("10", t)),
    "MAPZEN": Product(
        # Capped to the old SRTM band so it only offers cells already on the grid.
        "MAPZEN", "Mapzen (Tilezen) - SRTM Skadi .hgt.gz (~30 m)", "1arc", "DSM",
        -56, 60, 5,
        url=lambda t: "%s/%s/%s.hgt.gz" % (MAPZEN_BASE, t[:3], t),
        filename=lambda t: "%s.hgt.gz" % t),
    "ANADEM": Product(
        "ANADEM", "ANADEM - ML terrain, South America (~30 m)", "1arc", "DTM",
        -56, 13, lon_min=-82, lon_max=-34, mosaic=True,
        source_url="%s/ANADEM/ANADEM_be/anadem_v1_compressed_COG.tif" % OT_BASE,
        source_size_gb=66, doi="https://doi.org/10.5069/G9736P4G",
        note="South America only. Machine learning from Copernicus GLO-30, GEDI "
             "lidar, and Landsat-8/Sentinel-2 spectral indices."),
    "GEDTM30": Product(
        "GEDTM30", "GEDTM30 - Global Ensemble ML terrain (~30 m)", "1arc", "DTM",
        -90, 90, mosaic=True,
        source_url="%s/GEDTM30/GEDTM30_be/gedtm_rf_m_30m_s_20060101_20151231_go_"
                   "epsg.4326.3855_v1.2.tif" % OT_BASE,
        source_size_gb=403, doi="https://doi.org/10.5069/G9BV7DT1",
        note="Near-global. Machine learning from ~30 billion GEDI/ICESat-2 lidar "
             "points, Copernicus GLO-30 and ALOS AW3D30."),
    "SRTMGL3": Product(
        "SRTMGL3", "SRTM GL3 - 3 arc-sec (~90 m)", "3arc", "DSM", -56, 60, 2,
        url=_gl3_url, filename=lambda t: "%s.tif" % t),
    "COP90": Product(
        "COP90", "Copernicus GLO-90 - TanDEM-X (~90 m)", "3arc", "DSM", -90, 90, 6,
        url=lambda t: "%s/COP90/COP90_hh/%s" % (OT_BASE, _cop_name("30", t)),
        filename=lambda t: _cop_name("30", t)),
}

#: Which products each resolution toggle offers, in menu order.
RES_PRODUCTS = {
    "1arc": ["SRTMGL1", "NASADEM", "ALOS", "COP30", "MAPZEN", "ANADEM", "GEDTM30"],
    "3arc": ["SRTMGL3", "COP90"],
}


def products_for(kind, res):
    """The menu for a Type + Resolution pair, as the page's applyProduct builds it."""
    return [PRODUCTS[key] for key in RES_PRODUCTS[res] if PRODUCTS[key].kind == kind]


def resolutions_for(kind):
    """Which resolution toggles have any product of this type - DTM is 1 arc-sec only."""
    return [res for res in ("1arc", "3arc") if products_for(kind, res)]


def format_size(megabytes):
    """The page's formatSize: MB below a gigabyte, GB above."""
    if megabytes <= 0:
        return "—"
    if megabytes < 1024:
        return "%d MB" % round(megabytes)
    return "%.1f GB" % (megabytes / 1024.0)

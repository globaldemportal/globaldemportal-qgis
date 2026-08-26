"""Where downloads go, and what happens to them - stored in QGIS settings.

Deliberately a separate settings group from the web-view build, so the two
plugins can be installed side by side without fighting over one folder.
"""

import os

from qgis.core import QgsSettings

#: Settings live under the plugin's own group.
GROUP = "global_dem_portal/"


def default_download_dir():
    return os.path.join(os.path.expanduser("~"), "GlobalDEMPortal")


def download_dir():
    value = QgsSettings().value(GROUP + "download_dir", "")
    return value or default_download_dir()


def set_download_dir(path):
    QgsSettings().setValue(GROUP + "download_dir", path or "")


def target_crs():
    """Empty means 'leave the data in its own CRS' - the portal serves EPSG:4326."""
    return QgsSettings().value(GROUP + "target_crs", "") or ""


def set_target_crs(definition):
    QgsSettings().setValue(GROUP + "target_crs", definition or "")


def keep_originals():
    value = QgsSettings().value(GROUP + "keep_originals", True)
    return value in (True, "true", "True", 1, "1")


def set_keep_originals(enabled):
    QgsSettings().setValue(GROUP + "keep_originals", bool(enabled))


def auto_load():
    value = QgsSettings().value(GROUP + "auto_load", True)
    return value in (True, "true", "True", 1, "1")


def set_auto_load(enabled):
    QgsSettings().setValue(GROUP + "auto_load", bool(enabled))


def _flag(name, default=True):
    value = QgsSettings().value(GROUP + name, default)
    return value in (True, "true", "True", 1, "1")


def merge_tiles():
    """Whether a multi-tile download is mosaicked into one raster afterwards."""
    return _flag("merge_tiles", True)


def set_merge_tiles(enabled):
    QgsSettings().setValue(GROUP + "merge_tiles", bool(enabled))


def balance_seams():
    """Whether the tiles are levelled to each other before they are merged."""
    return _flag("balance_seams", True)


def set_balance_seams(enabled):
    QgsSettings().setValue(GROUP + "balance_seams", bool(enabled))


def colour_ramp():
    """Whether what is loaded gets one stretched hypsometric ramp."""
    return _flag("colour_ramp", True)


def set_colour_ramp(enabled):
    QgsSettings().setValue(GROUP + "colour_ramp", bool(enabled))

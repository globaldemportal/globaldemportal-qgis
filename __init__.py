"""Global DEM Portal - a QGIS panel carrying the web app's design."""


def classFactory(iface):  # noqa: N802 - QGIS API
    from .plugin import GlobalDemPortal

    return GlobalDemPortal(iface)

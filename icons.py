"""The sidebar's icons, copied verbatim from the web app's inline SVG.

Same viewBox, same paths, same 2px stroke - so the shapes match rather than
merely resemble.  ``currentColor`` is substituted before rendering, since Qt's
SVG renderer has no notion of inherited colour.
"""

from qgis.PyQt.QtCore import QByteArray, QRectF, Qt
from qgis.PyQt.QtGui import QIcon, QPainter, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer

_WRAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" '
    'stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

#: body, and whether the shape is stroked or filled - matching the page's markup.
SHAPES = {
    # .tool-btn icons
    "cursor": ('<path d="M4 2l16 10-7 1.5-3 7.5z"/>', False),
    "square": ('<rect x="3" y="3" width="18" height="18" rx="2"/>', False),
    "polygon": ('<polygon points="12,3 21,9 18,20 6,20 3,9"/>', False),
    "viewport": ('<rect x="2" y="5" width="20" height="14" rx="2"/>'
                 '<circle cx="12" cy="12" r="3"/>', False),
    # upload zone / search / zoom / primary action
    "upload": ('<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
               False),
    "download": ('<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
                 False),
    "search": ('<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>', False),
    # Not on the page - the panel can cancel a download, which a browser tab cannot.
    "close": ('<path d="M18 6L6 18M6 6l12 12"/>', False),
    # Leaflet's layer-switcher glyph: three stacked plates.
    "layers": ('<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 12l10 5 10-5"/>'
               '<path d="M2 17l10 5 10-5"/>', False),
    # footer links
    "mail": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
             False),
    "github": (
        '<path d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.86 10.92c.58.1.79-.25'
        '.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.34-1.3-1.7-1.3-1.7-1.06-.72.08-.71'
        '.08-.71 1.17.08 1.79 1.2 1.79 1.2 1.04 1.79 2.73 1.27 3.4.97.1-.76.41-1.27.74'
        '-1.56-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11'
        '-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63'
        ' 1.59.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.1 0 4.42-2.7 5.4-5.26 5.68.42.36'
        '.79 1.07.79 2.16v3.2c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0'
        ' 12 .5z"/>', True),
    "linkedin": (
        '<path d="M20.45 20.45h-3.56v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45'
        '-2.14 2.94v5.67H9.34V9h3.42v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27'
        ' 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13z'
        'M7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.8 0 0 .78 0 1.75v20.5C0 23.22.8'
        ' 24 1.77 24h20.45c.98 0 1.78-.78 1.78-1.75V1.75C24 .78 23.2 0 22.22 0z"/>',
        True),
}


def pixmap(name, size, color, stroke_width=2):
    """Render one icon at ``size`` px square in ``color``, honouring devicePixelRatio."""
    body, filled = SHAPES[name]
    svg = _WRAP.format(
        body=body,
        fill=color if filled else "none",
        stroke="none" if filled else color,
        width=stroke_width,
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    # Render at 2x and mark the ratio, so the strokes stay crisp on HiDPI screens.
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name, size, color, stroke_width=2):
    return QIcon(pixmap(name, size, color, stroke_width))

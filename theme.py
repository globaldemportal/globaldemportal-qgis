"""The portal's visual design, translated from CSS to Qt.

Every value here was read out of the ``<style>`` block in the web app's
``index.html`` - the colours, the radii, the paddings and the font sizes are the
same numbers, not an approximation.  Where CSS and Qt disagree the difference is
noted next to the token, because those are the places a future edit will drift.

Qt Style Sheets are a subset of CSS 2.1.  These properties have no Qt equivalent
and are applied in Python instead of here:

``text-transform``   uppercase the string yourself (see ``widgets.SectionTitle``)
``letter-spacing``   ``QFont.setLetterSpacing``
``transition``       Qt has no implicit animation; hover states just snap
``gap``              layout ``setSpacing``
``flex`` / ``grid``  QHBoxLayout / QVBoxLayout / QGridLayout stretch factors
``box-shadow``       ``QGraphicsDropShadowEffect``
"""

import re

#: Palette, straight out of the stylesheet.  Named for the role the CSS gives
#: them rather than the colour, so the mapping back to index.html stays obvious.
C = {
    "bg_deep": "#0f172a",      # body, map backdrop, inset controls
    "bg_panel": "#1e293b",     # the sidebar itself, modals
    "border": "#334155",       # every 1px divider in the design
    "text": "#f1f5f9",         # body text
    "text_bright": "#f8fafc",  # the h1 only
    "muted": "#94a3b8",        # secondary text, tool-button labels
    "dim": "#64748b",          # section titles, stat labels, hints
    "dimmer": "#475569",       # placeholder text, disabled label
    "accent": "#3b82f6",       # stat values, active tab, primary button
    "accent_deep": "#1d4ed8",  # the Select button
    "accent_hover": "#2563eb",
    "accent_soft": "#93c5fd",  # active tool-button text
    "link": "#60a5fa",
    "danger": "#ef4444",
    "danger_soft": "#fca5a5",
    # .btn-primary:disabled is #1e3a5f/#475569 *at opacity .7*.  QSS has no
    # widget opacity, so these are the composited results over the sidebar -
    # they render identically, and were checked against the page's own pixels.
    "disabled_bg": "#1e3554",
    "disabled_fg": "#3b485b",
}

#: How much of the design's own size the sidebar is drawn at.
#:
#: The web app is laid out for a browser viewport; a QGIS dock is typically half
#: that tall, so the Tools tab needed far more scrolling here than it does on the
#: page.  Every type size, icon, padding and box height is multiplied by this one
#: factor, so the design's proportions are kept exactly - it is the same sidebar,
#: drawn smaller, not a different layout.  Set it to 1.0 to get the web app's own
#: metrics back.
UI_SCALE = 0.85


def px(value):
    """A design pixel as a whole panel pixel.

    Everything that occupies vertical space goes through here, which is what
    makes the scale a single number rather than a hunt through three modules.
    """
    return int(round(value * UI_SCALE))


#: The declarations whose lengths are scaled.  Borders are deliberately absent:
#: a hairline divider stays a hairline at any scale, and the tab's 2px underline
#: is an accent by design rather than a measurement.  Scaling them would blur
#: both into something Qt rounds inconsistently.
_SCALED_PROPERTIES = ("font-size", "padding", "border-radius", "width",
                      "height", "min-height", "margin")
_DECLARATION = re.compile(r"\b(%s)\s*:\s*([^;{}]+);"
                          % "|".join(_SCALED_PROPERTIES))
_LENGTH = re.compile(r"(\d+(?:\.\d+)?)px")

#: Type has a floor that boxes do not.  The design's smallest captions are 10px,
#: and 10 x 0.85 rounds to 8 - a fifth smaller, and small enough that Segoe UI
#: starts dropping stem detail.  Padding may round freely; a caption may not.
MIN_FONT_PX = 9


def scale_lengths(sheet):
    """Apply :data:`UI_SCALE` to the lengths in a finished stylesheet.

    Done as one pass over the completed sheet rather than by parameterising
    forty numbers, so the QSS below stays readable as a transcription of the
    page's CSS - the sizes you read here are the sizes in ``index.html``.
    """
    if UI_SCALE == 1.0:
        return sheet

    def scale(match):
        floor = MIN_FONT_PX if match.group(1) == "font-size" else 1
        return "%s: %s;" % (match.group(1), _LENGTH.sub(
            lambda length: "%dpx" % max(floor, px(float(length.group(1)))),
            match.group(2)))

    return _DECLARATION.sub(scale, sheet)


#: Geometry.  CSS px and Qt px are both device-independent, so these carry over
#: 1:1 - a 320px sidebar is 320 Qt px at any device pixel ratio.  The width is
#: *not* scaled: a narrower column would wrap the labels onto more lines, which
#: is the opposite of what the scale is for.
SIDEBAR_WIDTH = 320
TAB_HEIGHT = px(34)
STAT_PADDING = px(11)

#: The web app's font stack resolves to Segoe UI on Windows, which is also Qt's
#: default UI font there.  The stack is repeated so Linux and macOS match too.
FONT_STACK = '"Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif'
MONO_STACK = '"Courier New", monospace'


def stylesheet():
    """The whole panel's QSS.  Selectors are object names, set in widgets.py."""
    return scale_lengths("""
    QWidget#root, QWidget#tabPage {{ background: {bg_panel}; }}
    QWidget {{ font-family: {font}; color: {text}; }}

    /* ── Sidebar ───────────────────────────────────────────── */
    QFrame#sidebar {{
        background: {bg_panel};
        border: none;
    }}
    /* The splitter handle draws the rule that used to be the sidebar's right
       border, so the seam stays 1px however the two panes are sized. */
    QSplitter#mainSplitter::handle {{ background: {border}; }}

    /* ── Header ────────────────────────────────────────────── */
    QFrame#header {{ border: none; border-bottom: 1px solid {border}; }}
    QLabel#title {{ font-size: 17px; font-weight: 700; color: {text_bright}; }}
    QLabel#versionBadge {{
        font-size: 10px;
        font-weight: 600;
        background: {accent};
        color: #ffffff;
        border-radius: 9px;
        padding: 2px 7px;
    }}
    QLabel#subtitle {{ font-size: 12px; color: {muted}; }}

    /* ── Stats bar ─────────────────────────────────────────── */
    QFrame#stats {{
        background: {bg_deep};
        border: none;
        border-bottom: 1px solid {border};
    }}
    QFrame#statDivider {{ background: {border}; border: none; }}
    QLabel#statValue {{ font-size: 20px; font-weight: 700; color: {accent}; }}
    QLabel#statLabel {{ font-size: 10px; color: {dim}; }}

    /* ── Tabs ──────────────────────────────────────────────── */
    QFrame#tabBar {{ border: none; border-bottom: 1px solid {border}; }}
    QToolButton#tab {{
        font-size: 12px;
        font-weight: 500;
        color: {dim};
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        padding: 9px 4px;
    }}
    QToolButton#tab:hover {{ color: {muted}; }}
    QToolButton#tab:checked {{ color: {accent}; border-bottom: 2px solid {accent}; }}

    /* ── Scroll areas: a 4px thumb, no track, like ::-webkit-scrollbar ── */
    QScrollArea {{ background: {bg_panel}; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 4px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {border}; border-radius: 2px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    /* ── Section titles ────────────────────────────────────── */
    QLabel#sectionTitle {{ font-size: 10px; font-weight: 600; color: {dim}; }}
    QLabel#optionLabel {{ font-size: 11px; color: {dim}; }}
    QLabel#hintText {{ font-size: 11px; color: {dim}; }}

    /* ── Tool buttons (the 2x2 grid) ───────────────────────── */
    QToolButton#toolBtn {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 8px;
        color: {muted};
        font-size: 11px;
        font-weight: 500;
        padding: 11px 6px;
    }}
    QToolButton#toolBtn:hover {{ border: 1px solid {accent}; color: {text}; }}
    QToolButton#toolBtn:checked {{
        background: rgba(29, 78, 216, 0.35);
        border: 1px solid {accent};
        color: {accent_soft};
    }}

    /* ── Keyboard hint chips ───────────────────────────────── */
    QLabel#keyChip {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 3px;
        color: {dim};
        font-size: 10px;
        padding: 1px 4px;
    }}

    /* ── Inputs ────────────────────────────────────────────── */
    QLineEdit#searchInput {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 7px 10px;
        color: {text};
        font-size: 12px;
    }}
    QLineEdit#searchInput:focus {{ border: 1px solid {accent}; }}
    QPushButton#searchBtn {{
        background: {accent_deep};
        border: none;
        border-radius: 6px;
        padding: 7px 13px;
        color: #ffffff;
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton#searchBtn:hover {{ background: {accent_hover}; }}

    /* ── Upload drop zone ──────────────────────────────────── */
    QFrame#uploadArea {{
        border: 2px dashed {border};
        border-radius: 8px;
        background: transparent;
    }}
    QFrame#uploadArea:hover {{ border: 2px dashed {accent}; }}
    QLabel#uploadText {{ font-size: 12px; color: {dim}; }}
    QLabel#uploadHint {{ font-size: 10px; color: {dimmer}; }}

    /* ── Selects ───────────────────────────────────────────── */
    QComboBox#productSelect {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 8px 10px;
        color: {text};
        font-size: 12px;
    }}
    QComboBox#productSelect:focus, QComboBox#productSelect:on {{ border: 1px solid {accent}; }}
    QComboBox#productSelect::drop-down {{ border: none; width: 20px; }}
    QComboBox#productSelect QAbstractItemView {{
        background: {bg_deep};
        border: 1px solid {border};
        color: {text};
        selection-background-color: {accent_deep};
        outline: none;
    }}

    /* ── Resolution options ────────────────────────────────── */
    QFrame#resOption {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 8px;
    }}
    QFrame#resOption:hover {{ border: 1px solid {accent}; }}
    QFrame#resOption[active="true"] {{
        background: rgba(59, 130, 246, 0.12);
        border: 1px solid {accent};
    }}
    QLabel#resValue {{ font-size: 17px; font-weight: 700; color: {text}; }}
    QLabel#resLabel {{ font-size: 10px; color: {dim}; }}

    /* ── Zoom-to-selection button ──────────────────────────── */
    QPushButton#zoomSelectBtn {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 8px;
        color: {muted};
        font-size: 12px;
        padding: 9px;
    }}
    QPushButton#zoomSelectBtn:hover {{ border: 1px solid {accent}; color: {text}; }}

    /* ── Tiles tab ─────────────────────────────────────────── */
    QLabel#tilesEmpty {{ font-size: 12px; color: {dim}; }}
    QLabel#tilesCount {{ font-size: 11px; color: {dim}; }}
    QPushButton#tileChip {{
        background: {bg_deep};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 3px 7px;
        font-family: {mono};
        font-size: 11px;
        color: {muted};
    }}
    QPushButton#tileChip:hover {{ border: 1px solid {danger}; color: {danger_soft}; }}

    /* ── Info tab ──────────────────────────────────────────── */
    QLabel#infoHeading {{ font-size: 13px; font-weight: 600; color: {text}; }}
    QLabel#infoBody {{ font-size: 12px; color: {muted}; }}

    /* ── Action buttons ────────────────────────────────────── */
    QFrame#actions {{ border: none; border-top: 1px solid {border}; }}
    QPushButton#btnPrimary {{
        background: {accent};
        border: none;
        border-radius: 8px;
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
        padding: 11px;
    }}
    QPushButton#btnPrimary:hover:enabled {{ background: {accent_hover}; }}
    QPushButton#btnPrimary:disabled {{ background: {disabled_bg}; color: {disabled_fg}; }}
    /* A hairline of progress under the primary button while a job runs. */
    QProgressBar#downloadProgress {{
        background: {bg_deep};
        border: none;
        border-radius: 3px;
    }}
    QProgressBar#downloadProgress::chunk {{
        background: {accent};
        border-radius: 3px;
    }}
    QPushButton#btnSecondary {{
        background: transparent;
        border: 1px solid {border};
        border-radius: 8px;
        color: {muted};
        font-size: 13px;
        font-weight: 500;
        padding: 9px;
    }}
    QPushButton#btnSecondary:hover {{ border: 1px solid {dimmer}; color: {text}; }}

    /* ── Footer ────────────────────────────────────────────── */
    QFrame#footer {{ border: none; border-top: 1px solid {border}; }}
    QLabel#footerText {{ font-size: 11px; color: {dim}; }}
    QToolButton#footerLink {{
        background: transparent;
        border: none;
        color: {muted};
        font-size: 11px;
        padding: 0px;
    }}
    QToolButton#footerLink:hover {{ color: {text}; }}

    /* ── Map-side floating controls (Leaflet's zoom / layers boxes) ── */
    QToolButton#mapCtrl {{
        background: #ffffff;
        border: 1px solid rgba(0, 0, 0, 0.2);
        color: #000000;
        font-size: 18px;
    }}
    QToolButton#mapCtrl:hover {{ background: #f4f4f4; }}
    """.format(
        font=FONT_STACK,
        mono=MONO_STACK,
        bg_deep=C["bg_deep"],
        bg_panel=C["bg_panel"],
        border=C["border"],
        text=C["text"],
        text_bright=C["text_bright"],
        muted=C["muted"],
        dim=C["dim"],
        dimmer=C["dimmer"],
        accent=C["accent"],
        accent_deep=C["accent_deep"],
        accent_hover=C["accent_hover"],
        accent_soft=C["accent_soft"],
        danger=C["danger"],
        danger_soft=C["danger_soft"],
        disabled_bg=C["disabled_bg"],
        disabled_fg=C["disabled_fg"],
    ))


#: Heights measured off the live page with getBoundingClientRect(), in CSS px.
#: Qt's font metrics carry more leading than the CSS line boxes, so every one of
#: these boxes came out 3-8px taller than the web app until they were pinned.
#: Keep them in sync with the stylesheet, not with what Qt happens to compute.
H = {name: px(height) for name, height in {
    "stats": 59,
    "tabBar": 36,
    "tab": 35,
    "toolBtn": 59,
    "searchInput": 29,
    "searchBtn": 29,
    "uploadArea": 103,
    "productSelect": 33,
    "resOption": 59,
    "zoomBtn": 34,
    "btnPrimary": 38,
    "btnSecondary": 34,
    "footer": 56,
}.items()}


def set_px(widget, size):
    """CSS px font sizing, including the design's half-pixel sizes.

    QSS only takes whole px, and several of the page's sizes are fractional
    (11.5px subtitle, 13.5px primary button).  Points are fractional, and at the
    standard 96 dpi 1px == 0.75pt, so this expresses them exactly.

    ``size`` is a design px, so :data:`UI_SCALE` is applied here too - and being
    fractional, these sizes scale without the rounding the QSS pass has to do.
    """
    font = widget.font()
    font.setPointSizeF(max(MIN_FONT_PX, size * UI_SCALE) * 0.75)
    widget.setFont(font)

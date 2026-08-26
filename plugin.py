"""QGIS plugin entry point for the Global DEM Portal panel."""

import os

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget
from qgis.core import Qgis

from .panel import PortalPanel

PREFERRED_WIDTH = 900     # the width the web app's layout is designed around
PREFERRED_HEIGHT = 820

#: The same file metadata.txt names, so the toolbar button, the menu entry and
#: the Plugin Manager listing all show one mark.
ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icon.png")


class PortalDock(QDockWidget):
    """A dock that opens at the size the design expects, and stays resizable."""

    def __init__(self, iface, parent=None):
        super().__init__("Global DEM Portal", parent)
        self.setObjectName("GlobalDemPortalDock")
        self.iface = iface
        self.panel = PortalPanel(self)
        self.panel.notified.connect(self._relay)
        self.setWidget(self.panel)

    def _relay(self, message, level):
        """Put the panel's messages on the QGIS message bar.

        The level arrives as a plain int: a pyqtSignal flattens the enum on the
        way through, and QgsMessageBar.pushMessage rejects that, so it is coerced
        back before use.
        """
        self.iface.messageBar().pushMessage(
            "Global DEM Portal", message, level=Qgis.MessageLevel(level), duration=4)

    def sizeHint(self):  # noqa: N802 - Qt naming
        """Open at the size the design targets, without pinning a minimum.

        The web-view build of this plugin shipped once with a minimum size on
        the view, which made the dock unresizable; only a size *hint* is set
        here, so QGIS opens it roomy and the user can still shrink it.
        """
        return QSize(PREFERRED_WIDTH, PREFERRED_HEIGHT)


class GlobalDemPortal:
    """The object QGIS instantiates through classFactory."""

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):  # noqa: N802 - QGIS API
        # An icon *and* a toolbar button.  The first version did neither: it
        # only called addPluginToMenu with a bare QAction, so the plugin had no
        # button on the Plugins toolbar at all and its menu entry carried no
        # mark - the one place a user looks for it first was the one place it
        # was not.
        self.action = QAction(QIcon(ICON_PATH), "Global DEM Portal",
                              self.iface.mainWindow())
        self.action.setToolTip(
            "Global DEM Portal - select tiles and download elevation data")
        # Checkable, and kept in step with the dock below, so the button reads
        # as a panel toggle rather than doing nothing when the panel is open.
        self.action.setCheckable(True)
        self.action.triggered.connect(self._toggle_panel)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Global DEM Portal", self.action)

    def _place(self, dock):
        """Dock across the top, at a size that leaves room for the map.

        Not the right-hand column, which is where this used to go.  A dock added
        to the side takes the width that column already has - typically ~320px,
        set by the Layers or Processing panel - and QMainWindow will not widen
        the column to satisfy a mere size *hint*.  The panel therefore opened at
        320px with its splitter giving the sidebar everything and the map zero
        pixels: the half of the plugin that does the selecting was simply not
        drawn.

        The top area spans the full width between the left and right docks, so
        the panel opens at the shape it is designed for.  resizeDocks is what
        actually sets the height; a size hint alone is only a suggestion, and on
        a short screen it would be ignored.
        """
        window = self.iface.mainWindow()
        self.iface.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, dock)
        # Never take more than three quarters of the window: QGIS's own canvas
        # has to stay usable underneath, and on a 768px-tall laptop the design
        # height would swallow it whole.
        height = min(PREFERRED_HEIGHT, int(window.height() * 0.75))
        window.resizeDocks([dock], [height], Qt.Orientation.Vertical)

    def _toggle_panel(self, checked):
        if checked:
            self.show_panel()
        elif self.dock is not None:
            self.dock.hide()

    def show_panel(self):
        if self.dock is None:
            self.dock = PortalDock(self.iface, self.iface.mainWindow())
            self._place(self.dock)
            # Closing the dock by its own X must leave the button unchecked.
            # visibilityChanged only ever calls setChecked, which emits toggled
            # rather than triggered, so this cannot loop back on itself.
            if self.action is not None:
                self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.show()
        self.dock.raise_()
        if self.action is not None:
            self.action.setChecked(True)

    def unload(self):
        # The dock goes first: it holds a connection to the action, and taking
        # it down while the action is still alive keeps that ordering safe.
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu("&Global DEM Portal", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

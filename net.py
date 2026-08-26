"""HTTP through QGIS's own network stack.

Using ``urllib`` here would work, but it ignores everything the user configured
in QGIS: proxy host, proxy credentials, authentication configs, cache settings,
and the network timeout.  Anyone behind a corporate proxy would find the panel
simply unable to download.  ``QgsBlockingNetworkRequest`` goes through
``QgsNetworkAccessManager``, so it inherits all of that.

It also removes a static-analysis finding rather than papering over one: bandit
flags bare ``urlopen`` calls (B310) because ``urllib`` will happily open
``file://`` and ``ftp://`` URLs.  No scanner-suppression comment is used anywhere
in this package - a suppression inside a plugin reads as hiding something, and
the plugin repository downgrades uploads that carry one.

Blocking calls belong on a worker thread; every caller here is inside a QgsTask.
"""

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import QgsBlockingNetworkRequest

USER_AGENT = b"QGIS Global DEM Portal plugin"


class HttpError(Exception):
    """A request that did not come back with a body."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def get(url, headers=None, accept_missing=False):
    """GET ``url`` and return its body as bytes.

    ``accept_missing`` returns None for a 404 instead of raising, which is how a
    tile that a mission never filled is distinguished from a real failure.
    """
    if not url.startswith("https://"):
        raise HttpError("refusing a non-https URL: %s" % url)

    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", USER_AGENT)
    for name, value in (headers or {}).items():
        request.setRawHeader(name.encode("utf-8"), value.encode("utf-8"))

    fetcher = QgsBlockingNetworkRequest()
    code = fetcher.get(request, forceRefresh=True)
    reply = fetcher.reply()
    status = reply.attribute(
        QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0

    if status == 404 and accept_missing:
        return None
    if code != QgsBlockingNetworkRequest.ErrorCode.NoError:
        detail = fetcher.errorMessage() or "network error"
        raise HttpError(detail, status)
    if status and not 200 <= status < 300:
        raise HttpError("HTTP %s" % status, status)
    return bytes(reply.content())

"""Run the QGIS Plugin Repository's own checks against the built zip.

Every rule in the first section is a transcription of ``validator.py`` from the
plugins.qgis.org codebase (qgis/QGIS-Django, ``qgis-app/plugins/validator.py``),
including the constants, the regular expressions and the exact wording of the
messages - so that a failure here reads the way the upload page would read, and
a pass means the upload page has nothing left to object to.  The form-level
rules that live in ``forms.py`` and ``models.py`` are checked too, since they
reject an upload just as firmly as the validator does.

The second section is extra: things the repository does not test but a reviewer
will notice, and things this package has been bitten by before.

Usage::

    python validate.py                     # checks dist/global_dem_portal.zip
    python validate.py path/to/plugin.zip
    python validate.py --new               # also apply the stricter new-plugin rule
    python validate.py --offline           # skip the URL reachability checks
"""

import configparser
import io
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ZIP = os.path.join(HERE, "dist", "global_dem_portal.zip")

# ── the repository's own constants, verbatim ─────────────────────────────
PLUGIN_MAX_UPLOAD_SIZE = 25000000                       # 25 mb
PLUGIN_REQUIRED_METADATA = (
    "name", "description", "version", "qgisMinimumVersion", "author", "email",
    "about", "tracker", "repository",
)
PLUGIN_OPTIONAL_METADATA = (
    "homepage", "changelog", "qgisMaximumVersion", "tags", "deprecated",
    "experimental", "external_deps", "server",
)
PLUGIN_BOOLEAN_METADATA = ("experimental", "deprecated", "server")
FORBIDDEN_DIRS = ("__MACOSX", ".git", "__pycache__")
#: The placeholder URLs the repository refuses, from _check_url_link.
FORBIDDEN_URLS = {"tracker": "http://bugs", "repository": "http://repo",
                  "homepage": "http://homepage"}

errors = []
warnings = []


def ok(label, detail=""):
    print("  ok    %s%s" % (label, ("  -  " + detail) if detail else ""))


def fail(label, message):
    errors.append(label)
    print("  FAIL  %s\n          %s" % (label, message))


def warn(label, message):
    warnings.append(label)
    print("  warn  %s\n          %s" % (label, message))


def check(label, condition, message, detail=""):
    if condition:
        ok(label, detail)
    else:
        fail(label, message)
    return condition


# ══════════════════════════════════════════════════════════════════════════
# 1. validator.py
# ══════════════════════════════════════════════════════════════════════════
def run_repository_validator(path, is_new=False, offline=False):
    print("QGIS Plugin Repository validator  (qgis-app/plugins/validator.py)")
    print("-" * 72)

    size = os.path.getsize(path)
    if not check("file size within the limit",
                 size <= PLUGIN_MAX_UPLOAD_SIZE,
                 "File is too big. Max size is %s Megabytes"
                 % (PLUGIN_MAX_UPLOAD_SIZE / 1000000),
                 "%.2f MB of %d MB" % (size / 1000000.0,
                                       PLUGIN_MAX_UPLOAD_SIZE / 1000000)):
        return None

    try:
        archive = zipfile.ZipFile(path)
    except Exception:
        fail("the file unzips", "Could not unzip file.")
        return None
    ok("the file unzips")

    namelist = archive.namelist()

    # -- path information, .pyc, forbidden directories --
    offenders = [n for n in namelist
                 if n.find("..") != -1 or n.find(os.path.sep) == 0 or n.find("/") == 0]
    check("no path information in the archive", not offenders,
          "For security reasons, zip file cannot contain path information "
          "(found '%s')" % (offenders[0] if offenders else ""))

    check("no .pyc files", not [n for n in namelist if n.find(".pyc") != -1],
          "For security reasons, zip file cannot contain .pyc file")

    for forbidden in FORBIDDEN_DIRS:
        hits = [n for n in namelist if forbidden in n.split("/")]
        check("no '%s' directory" % forbidden, not hits,
              "For security reasons, zip file cannot contain '%s' directory. "
              "However, it has been found at '%s'."
              % (forbidden, hits[0] if hits else ""))

    bad = archive.testzip()
    check("no CRC errors", bad is None,
          "Bad zip (maybe a CRC error) on file %s" % bad)

    # -- one top-level folder --
    parents = sorted({name.split("/")[0] for name in namelist})
    if len(parents) > 1:
        warn("a single top-level folder",
             "The repository warns on multiple parent folders: %s"
             % ", ".join(parents))
    else:
        ok("a single top-level folder", parents[0])

    try:
        package_name = namelist[0][: namelist[0].index("/")]
    except Exception:
        fail("a folder inside the package",
             "Cannot find a folder inside the compressed package: this does "
             "not seems a valid plugin")
        return None

    if is_new:
        check("top-level directory is PEP 8 compliant",
              bool(re.match(r"^[a-z_][a-z0-9_]*$", package_name)),
              "The name of the top level directory inside the zip package must "
              "be PEP 8 compliant: lowercase with words separated by "
              "underscores, and must start with a letter or underscore.",
              package_name)

    check("top-level directory name is acceptable",
          bool(re.match(r"^[A-Za-z][A-Za-z0-9-_]+$", package_name)),
          "The name of the top level directory inside the zip package must "
          "start with an ASCII letter and can only contain ASCII letters, "
          "digits and the signs '-' and '_'.", package_name)

    # -- required files --
    initname = package_name + "/__init__.py"
    metadataname = package_name + "/metadata.txt"
    check("__init__.py or metadata.txt present",
          initname in namelist or metadataname in namelist,
          "Cannot find __init__.py or metadata.txt in the compressed package: "
          "this does not seems a valid plugin (I searched for %s and %s)"
          % (initname, metadataname))
    check("__init__.py present", initname in namelist,
          "Cannot find __init__.py in plugin package.")

    licensename = package_name + "/LICENSE"
    check("LICENSE present", licensename in namelist,
          "Cannot find LICENSE in the plugin package. This file is required, "
          "please consider adding it to the plugin package.")

    # -- metadata --
    metadata = []
    if metadataname in namelist:
        try:
            parser = configparser.ConfigParser()
            parser.optionxform = str
            parser.read_file(io.StringIO(
                archive.read(metadataname).decode("utf8")))
            if not parser.has_section("general"):
                fail("metadata has a [general] section",
                     "Cannot find a section named 'general' in %s" % metadataname)
                return None
            ok("metadata has a [general] section")
            metadata.extend(parser.items("general"))
        except Exception as exc:
            fail("metadata parses", "Errors parsing %s. %s" % (metadataname, exc))
            return None
        metadata.append(("metadata_source", "metadata.txt"))

    found = dict(metadata)
    missing = [field for field in PLUGIN_REQUIRED_METADATA if field not in found]
    check("all required metadata present", not missing,
          "Cannot find metadata %s in metadata source metadata.txt"
          % ", ".join(missing),
          ", ".join(PLUGIN_REQUIRED_METADATA))

    # -- the icon: soft in the validator, but a missing one is a blank listing --
    icon = found.get("icon", "")
    if icon:
        icon_path = icon[2:] if icon.startswith("./") else icon
        try:
            body = archive.read(package_name + "/" + icon_path)
            ok("icon reads back", "%s, %d bytes" % (icon_path, len(body)))
        except Exception:
            warn("icon reads back",
                 "metadata names icon=%s but the archive has no such file; the "
                 "repository stores no icon and the listing shows a blank tile"
                 % icon)
    else:
        warn("icon declared", "no icon= in metadata; the listing shows a blank tile")

    # -- booleans --
    for flag in PLUGIN_BOOLEAN_METADATA:
        if flag in found:
            value = found[flag].lower()
            check("%s parses as a boolean" % flag,
                  value in ("true", "false", "1", "0"),
                  "The repository reads %s as (value.lower() == 'true' or == '1'); "
                  "'%s' would silently become False" % (flag, found[flag]),
                  "%s -> %s" % (found[flag], value in ("true", "1")))

    # -- author --
    if "author" in found:
        check("author has no slashes", bool(re.match(r"^[^/]+$", found["author"])),
              "Author name cannot contain slashes.", found["author"])

    # -- metadata is UTF-8 and strippable --
    try:
        for key, value in metadata:
            if key not in PLUGIN_BOOLEAN_METADATA:
                value.strip()
        ok("metadata converts to UTF-8")
    except Exception as exc:
        fail("metadata converts to UTF-8",
             "There was an error converting metadata to UTF-8. Reported error "
             "was: %s" % exc)

    # -- the pre-1.8 rule --
    minimum = found.get("qgisMinimumVersion", "")
    if minimum:
        older = tuple(minimum.split(".")) < tuple("1.8".split("."))
        check("qgisMinimumVersion does not trigger the pre-1.8 rule", not older,
              "qgisMinimumVersion is set to less than 1.8 (%s), so the required "
              "metadata must also be readable from __init__.py" % minimum,
              minimum)

    # -- URLs --
    check_urls(found, offline)

    archive.close()
    return found


def check_urls(found, offline):
    """tracker / repository / homepage: valid, not the placeholder, reachable."""
    from urllib.parse import urlparse

    for key, forbidden in FORBIDDEN_URLS.items():
        url = found.get(key)
        if not url:
            continue
        parsed = urlparse(url)
        check("%s is a valid URL" % key,
              url != forbidden and all([parsed.scheme, parsed.netloc]),
              "Please provide valid url link for the following key(s) in the "
              "metadata source: %s." % key, url)

    if offline:
        print("  skip  URL reachability (--offline)")
        return

    import urllib.error
    import urllib.request

    # The repository sends a browser User-Agent because some sites answer 403
    # to anything else, and treats >= 400 as unreachable.
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/56.0.2924.76 Safari/537.36"}
    for key in FORBIDDEN_URLS:
        url = found.get(key)
        if not url:
            continue
        try:
            request = urllib.request.Request(url, headers=headers, method="HEAD")
            status = urllib.request.urlopen(request, timeout=30).status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:
            fail("%s is reachable" % key,
                 "Please provide valid url link for %s. The website(s) cannot "
                 "be reached. (%s)" % (key, exc))
            continue
        check("%s is reachable" % key, status < 400,
              "Please provide valid url link for %s. The website(s) cannot be "
              "reached." % key, "HTTP %s" % status)


# ══════════════════════════════════════════════════════════════════════════
# 2. forms.py / models.py - the upload form rejects on these too
# ══════════════════════════════════════════════════════════════════════════
def run_form_checks(found):
    print()
    print("Upload form  (qgis-app/plugins/forms.py, models.py)")
    print("-" * 72)

    version = found.get("version", "")
    # PluginVersion.clean_version: a version containing a space is truncated to
    # its last token, which is rarely what the author meant.
    cleaned = version.rsplit(" ")[-1] if version.rfind(" ") > 0 else version
    check("version survives clean_version intact", cleaned == version,
          "clean_version() would store '%s' rather than '%s'" % (cleaned, version),
          version)

    check("version looks like a version",
          bool(re.match(r"^[0-9]+(\.[0-9]+)*$", cleaned)),
          "The repository sorts versions numerically; '%s' will sort oddly "
          "against digits-and-dots versions" % cleaned, cleaned)

    print("  note  a version already published cannot be re-uploaded: "
          "\"A plugin with this name and version number already exists.\"")
    print("  note  on an update the top-level folder must equal the existing "
          "plugin's package_name, or the upload is refused for a folder "
          "name mismatch.")

    minimum = found.get("qgisMinimumVersion", "")
    maximum = found.get("qgisMaximumVersion", "")
    check("qgisMinimumVersion is digits and dots",
          bool(re.match(r"^[0-9]+(\.[0-9]+)*$", minimum)),
          "min_qg_version is stored as a version string", minimum)
    if maximum:
        check("qgisMaximumVersion is digits and dots",
              bool(re.match(r"^[0-9]+(\.[0-9]+)*$", maximum)),
              "max_qg_version is stored as a version string", maximum)
    else:
        print("  note  no qgisMaximumVersion: the repository fills in '%s.99'"
              % (minimum.split(".")[0] if minimum else "?"))


# ══════════════════════════════════════════════════════════════════════════
# 3. Beyond the validator - what a reviewer sees
# ══════════════════════════════════════════════════════════════════════════
#: Comments that switch a security scanner off.  A suppression inside a plugin
#: reads as hiding something, and an upload carrying one has been marked down
#: before; the fix is always to remove the finding, not the warning.
SUPPRESSIONS = (r"#\s*nosec", r"#\s*noqa:\s*S\d", r"#\s*type:\s*ignore",
                r"detect-secrets", r"bandit:\s*skip")
#: Rough credential shapes, the kind a secrets scan reports.
SECRETS = (
    (r"(?i)\b(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"]{8,}",
     "a literal credential"),
    (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
    (r"gh[pousr]_[A-Za-z0-9]{16,}", "a GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
)


def run_extra_checks(path):
    print()
    print("Beyond the validator")
    print("-" * 72)

    archive = zipfile.ZipFile(path)
    names = archive.namelist()
    sources = [n for n in names if n.endswith(".py")]

    # -- every module compiles under the interpreter QGIS ships --
    broken = []
    for name in sources:
        try:
            compile(archive.read(name).decode("utf-8"), name, "exec")
        except SyntaxError as exc:
            broken.append("%s: %s" % (name, exc))
    check("every module compiles", not broken, "; ".join(broken),
          "%d modules" % len(sources))

    # -- scanner suppressions and credentials --
    suppressed, leaked = [], []
    for name in sources:
        text = archive.read(name).decode("utf-8", "replace")
        for pattern in SUPPRESSIONS:
            if re.search(pattern, text):
                suppressed.append("%s (%s)" % (name, pattern))
        for pattern, what in SECRETS:
            if re.search(pattern, text):
                leaked.append("%s (%s)" % (name, what))
    check("no scanner suppressions", not suppressed, "; ".join(suppressed))
    check("no credential-shaped literals", not leaked, "; ".join(leaked))

    # -- scanner configuration does not belong in a package --
    configs = [n for n in names if os.path.basename(n) in
               (".secrets.baseline", ".bandit", "bandit.yaml", ".semgrepignore",
                "sonar-project.properties")]
    check("no scanner configuration shipped", not configs, ", ".join(configs))

    # -- developer leftovers --
    junk = [n for n in names if os.path.basename(n) in
            (".DS_Store", "Thumbs.db", ".gitignore", ".gitmodules")
            or n.endswith((".orig", ".rej", ".bak", ".swp"))]
    check("no editor or VCS leftovers", not junk, ", ".join(junk))

    # -- absolute paths from the machine that built it --
    embedded = []
    for name in sources:
        text = archive.read(name).decode("utf-8", "replace")
        if re.search(r"[A-Za-z]:\\\\Users\\\\|/home/[a-z]+/|/Users/[a-z]+/", text):
            embedded.append(name)
    check("no build-machine paths embedded", not embedded, ", ".join(embedded))

    # -- the entry point QGIS calls --
    init = [n for n in names if n.endswith("/__init__.py")
            and n.count("/") == 1]
    if init:
        text = archive.read(init[0]).decode("utf-8")
        check("__init__.py defines classFactory", "def classFactory" in text,
              "QGIS calls classFactory(iface) to instantiate the plugin")

    archive.close()


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    path = argv[0] if argv else DEFAULT_ZIP
    if not os.path.isfile(path):
        sys.exit("No such file: %s" % path)

    print("Checking %s" % path)
    print()
    found = run_repository_validator(path, is_new="--new" in flags,
                                     offline="--offline" in flags)
    if found is not None:
        run_form_checks(found)
    run_extra_checks(path)

    print()
    print("=" * 72)
    print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
    if errors:
        print("would be REJECTED: %s" % ", ".join(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

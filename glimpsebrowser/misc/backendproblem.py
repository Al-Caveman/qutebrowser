# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2017-2019 Florian Bruhin (The Compiler) <mail@glimpsebrowser.org>
#
# This file is part of glimpsebrowser.
#
# glimpsebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# glimpsebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with glimpsebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Dialogs shown when there was a problem with a backend choice."""

import os
import sys
import functools
import html
import ctypes
import ctypes.util
import enum
import shutil

import attr
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QApplication, QDialog, QPushButton, QHBoxLayout,
                             QVBoxLayout, QLabel, QMessageBox)
from PyQt5.QtNetwork import QSslSocket

from glimpsebrowser.config import config, configfiles
from glimpsebrowser.utils import (usertypes, objreg, version, qtutils, log, utils,
                               standarddir)
from glimpsebrowser.misc import objects, msgbox


class _Result(enum.IntEnum):

    """The result code returned by the backend problem dialog."""

    quit = QDialog.Accepted + 1
    restart = QDialog.Accepted + 2
    restart_webkit = QDialog.Accepted + 3
    restart_webengine = QDialog.Accepted + 4


@attr.s
class _Button:

    """A button passed to BackendProblemDialog."""

    text = attr.ib()
    setting = attr.ib()
    value = attr.ib()
    default = attr.ib(default=False)


def _other_backend(backend):
    """Get the other backend enum/setting for a given backend."""
    other_backend = {
        usertypes.Backend.QtWebKit: usertypes.Backend.QtWebEngine,
        usertypes.Backend.QtWebEngine: usertypes.Backend.QtWebKit,
    }[backend]
    other_setting = other_backend.name.lower()[2:]
    return (other_backend, other_setting)


def _error_text(because, text, backend):
    """Get an error text for the given information."""
    other_backend, other_setting = _other_backend(backend)
    if other_backend == usertypes.Backend.QtWebKit:
        warning = ("<i>Note that QtWebKit hasn't been updated since "
                   "July 2017 (including security updates).</i>")
    else:
        warning = ""
    return ("<b>Failed to start with the {backend} backend!</b>"
            "<p>glimpsebrowser tried to start with the {backend} backend but "
            "failed because {because}.</p>{text}"
            "<p><b>Forcing the {other_backend.name} backend</b></p>"
            "<p>This forces usage of the {other_backend.name} backend by "
            "setting the <i>backend = '{other_setting}'</i> option "
            "(if you have a <i>config.py</i> file, you'll need to set "
            "this manually). {warning}</p>".format(
                backend=backend.name, because=because, text=text,
                other_backend=other_backend, other_setting=other_setting,
                warning=warning))


class _Dialog(QDialog):

    """A dialog which gets shown if there are issues with the backend."""

    def __init__(self, because, text, backend, buttons=None, parent=None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)

        other_backend, other_setting = _other_backend(backend)
        text = _error_text(because, text, backend)

        label = QLabel(text, wordWrap=True)
        label.setTextFormat(Qt.RichText)
        vbox.addWidget(label)

        hbox = QHBoxLayout()
        buttons = [] if buttons is None else buttons

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(lambda: self.done(_Result.quit))
        hbox.addWidget(quit_button)

        backend_text = "Force {} backend".format(other_backend.name)
        if other_backend == usertypes.Backend.QtWebKit:
            backend_text += ' (not recommended)'
        backend_button = QPushButton(backend_text)
        backend_button.clicked.connect(functools.partial(
            self._change_setting, 'backend', other_setting))
        hbox.addWidget(backend_button)

        for button in buttons:
            btn = QPushButton(button.text, default=button.default)
            btn.clicked.connect(functools.partial(
                self._change_setting, button.setting, button.value))
            hbox.addWidget(btn)

        vbox.addLayout(hbox)

    def _change_setting(self, setting, value):
        """Change the given setting and restart."""
        config.instance.set_obj(setting, value, save_yaml=True)
        save_manager = objreg.get('save-manager')
        save_manager.save_all(is_exit=True)

        if setting == 'backend' and value == 'webkit':
            self.done(_Result.restart_webkit)
        elif setting == 'backend' and value == 'webengine':
            self.done(_Result.restart_webengine)
        else:
            self.done(_Result.restart)


def _show_dialog(*args, **kwargs):
    """Show a dialog for a backend problem."""
    cmd_args = objreg.get('args')
    if cmd_args.no_err_windows:
        text = _error_text(*args, **kwargs)
        print(text, file=sys.stderr)
        sys.exit(usertypes.Exit.err_init)

    dialog = _Dialog(*args, **kwargs)

    status = dialog.exec_()
    quitter = objreg.get('quitter')

    if status in [_Result.quit, QDialog.Rejected]:
        pass
    elif status == _Result.restart_webkit:
        quitter.restart(override_args={'backend': 'webkit'})
    elif status == _Result.restart_webengine:
        quitter.restart(override_args={'backend': 'webengine'})
    elif status == _Result.restart:
        quitter.restart()
    else:
        raise utils.Unreachable(status)

    sys.exit(usertypes.Exit.err_init)


def _nvidia_shader_workaround():
    """Work around QOpenGLShaderProgram issues.

    NOTE: This needs to be called before _handle_nouveau_graphics, or some
    setups will segfault in version.opengl_vendor().

    See https://bugs.launchpad.net/ubuntu/+source/python-qt4/+bug/941826
    """
    assert objects.backend == usertypes.Backend.QtWebEngine, objects.backend

    if os.environ.get('QUTE_SKIP_LIBGL_WORKAROUND'):
        return

    libgl = ctypes.util.find_library("GL")
    if libgl is not None:
        ctypes.CDLL(libgl, mode=ctypes.RTLD_GLOBAL)


def _handle_nouveau_graphics():
    """Force software rendering when using the Nouveau driver.

    WORKAROUND for https://bugreports.qt.io/browse/QTBUG-41242
    Should be fixed in Qt 5.10 via https://codereview.qt-project.org/#/c/208664/
    """
    assert objects.backend == usertypes.Backend.QtWebEngine, objects.backend

    if os.environ.get('QUTE_SKIP_NOUVEAU_CHECK'):
        return

    if version.opengl_vendor() != 'nouveau':
        return

    if (os.environ.get('LIBGL_ALWAYS_SOFTWARE') == '1' or
            # qt.force_software_rendering = 'software-opengl'
            'QT_XCB_FORCE_SOFTWARE_OPENGL' in os.environ or
            # qt.force_software_rendering = 'chromium', also see:
            # https://build.opensuse.org/package/view_file/openSUSE:Factory/libqt5-qtwebengine/disable-gpu-when-using-nouveau-boo-1005323.diff?expand=1
            'QT_WEBENGINE_DISABLE_NOUVEAU_WORKAROUND' in os.environ):
        return

    button = _Button("Force software rendering", 'qt.force_software_rendering',
                     'chromium')
    _show_dialog(
        backend=usertypes.Backend.QtWebEngine,
        because="you're using Nouveau graphics",
        text="<p>There are two ways to fix this:</p>"
             "<p><b>Forcing software rendering</b></p>"
             "<p>This allows you to use the newer QtWebEngine backend (based "
             "on Chromium) but could have noticeable performance impact "
             "(depending on your hardware). "
             "This sets the <i>qt.force_software_rendering = 'chromium'</i> "
             "option (if you have a <i>config.py</i> file, you'll need to set "
             "this manually).</p>",
        buttons=[button],
    )

    raise utils.Unreachable


def _handle_wayland():
    assert objects.backend == usertypes.Backend.QtWebEngine, objects.backend

    if os.environ.get('QUTE_SKIP_WAYLAND_CHECK'):
        return

    platform = QApplication.instance().platformName()
    if platform not in ['wayland', 'wayland-egl']:
        return

    has_qt511 = qtutils.version_check('5.11', compiled=False)
    if has_qt511 and config.val.qt.force_software_rendering == 'chromium':
        return

    if qtutils.version_check('5.11.2', compiled=False):
        return

    buttons = []
    text = "<p>You can work around this in one of the following ways:</p>"

    if 'DISPLAY' in os.environ:
        # XWayland is available, but QT_QPA_PLATFORM=wayland is set
        buttons.append(_Button("Force XWayland", 'qt.force_platform', 'xcb'))
        text += ("<p><b>Force Qt to use XWayland</b></p>"
                 "<p>This allows you to use the newer QtWebEngine backend "
                 "(based on Chromium). "
                 "This sets the <i>qt.force_platform = 'xcb'</i> option "
                 "(if you have a <i>config.py</i> file, you'll need to set "
                 "this manually).</p>")
    else:
        text += ("<p><b>Set up XWayland</b></p>"
                 "<p>This allows you to use the newer QtWebEngine backend "
                 "(based on Chromium). ")

    if has_qt511:
        buttons.append(_Button("Force software rendering",
                               'qt.force_software_rendering',
                               'chromium'))
        text += ("<p><b>Forcing software rendering</b></p>"
                 "<p>This allows you to use the newer QtWebEngine backend "
                 "(based on Chromium) but could have noticeable performance "
                 "impact (depending on your hardware). This sets the "
                 "<i>qt.force_software_rendering = 'chromium'</i> option "
                 "(if you have a <i>config.py</i> file, you'll need to set "
                 "this manually).</p>")

    _show_dialog(backend=usertypes.Backend.QtWebEngine,
                 because="you're using Wayland", text=text, buttons=buttons)


@attr.s
class BackendImports:

    """Whether backend modules could be imported."""

    webkit_available = attr.ib(default=None)
    webengine_available = attr.ib(default=None)
    webkit_error = attr.ib(default=None)
    webengine_error = attr.ib(default=None)


def _try_import_backends():
    """Check whether backends can be imported and return BackendImports."""
    # pylint: disable=unused-import
    results = BackendImports()

    try:
        from PyQt5 import QtWebKit
        from PyQt5 import QtWebKitWidgets
    except ImportError as e:
        results.webkit_available = False
        results.webkit_error = str(e)
    else:
        if qtutils.is_new_qtwebkit():
            results.webkit_available = True
        else:
            results.webkit_available = False
            results.webkit_error = "Unsupported legacy QtWebKit found"

    try:
        from PyQt5 import QtWebEngineWidgets
    except ImportError as e:
        results.webengine_available = False
        results.webengine_error = str(e)
    else:
        results.webengine_available = True

    assert results.webkit_available is not None
    assert results.webengine_available is not None
    if not results.webkit_available:
        assert results.webkit_error is not None
    if not results.webengine_available:
        assert results.webengine_error is not None

    return results


def _handle_ssl_support(fatal=False):
    """Check for full SSL availability.

    If "fatal" is given, show an error and exit.
    """
    text = ("Could not initialize QtNetwork SSL support. If you use "
            "OpenSSL 1.1 with a PyQt package from PyPI (e.g. on Archlinux "
            "or Debian Stretch), you need to set LD_LIBRARY_PATH to the path "
            "of OpenSSL 1.0. This only affects downloads.")

    if QSslSocket.supportsSsl():
        return

    if fatal:
        errbox = msgbox.msgbox(parent=None,
                               title="SSL error",
                               text="Could not initialize SSL support.",
                               icon=QMessageBox.Critical,
                               plain_text=False)
        errbox.exec_()
        sys.exit(usertypes.Exit.err_init)

    assert not fatal
    log.init.warning(text)


def _check_backend_modules():
    """Check for the modules needed for QtWebKit/QtWebEngine."""
    imports = _try_import_backends()

    if imports.webkit_available and imports.webengine_available:
        return
    elif not imports.webkit_available and not imports.webengine_available:
        text = ("<p>glimpsebrowser needs QtWebKit or QtWebEngine, but neither "
                "could be imported!</p>"
                "<p>The errors encountered were:<ul>"
                "<li><b>QtWebKit:</b> {webkit_error}"
                "<li><b>QtWebEngine:</b> {webengine_error}"
                "</ul></p>".format(
                    webkit_error=html.escape(imports.webkit_error),
                    webengine_error=html.escape(imports.webengine_error)))
        errbox = msgbox.msgbox(parent=None,
                               title="No backend library found!",
                               text=text,
                               icon=QMessageBox.Critical,
                               plain_text=False)
        errbox.exec_()
        sys.exit(usertypes.Exit.err_init)
    elif objects.backend == usertypes.Backend.QtWebKit:
        if imports.webkit_available:
            return
        assert imports.webengine_available
        _show_dialog(
            backend=usertypes.Backend.QtWebKit,
            because="QtWebKit could not be imported",
            text="<p><b>The error encountered was:</b><br/>{}</p>".format(
                html.escape(imports.webkit_error))
        )
    elif objects.backend == usertypes.Backend.QtWebEngine:
        if imports.webengine_available:
            return
        assert imports.webkit_available
        _show_dialog(
            backend=usertypes.Backend.QtWebEngine,
            because="QtWebEngine could not be imported",
            text="<p><b>The error encountered was:</b><br/>{}</p>".format(
                html.escape(imports.webengine_error))
        )

    raise utils.Unreachable


def _handle_cache_nuking():
    """Nuke the QtWebEngine cache if the Qt version changed.

    WORKAROUND for https://bugreports.qt.io/browse/QTBUG-72532
    """
    if not configfiles.state.qt_version_changed:
        return

    # Only nuke the cache in cases where we know there are problems.
    # It seems these issues started with Qt 5.12.
    # They should be fixed with Qt 5.12.5:
    # https://codereview.qt-project.org/c/qt/qtwebengine-chromium/+/265408
    affected = (qtutils.version_check('5.12', compiled=False) and not
                qtutils.version_check('5.12.5', compiled=False))
    if not affected:
        return

    log.init.info("Qt version changed, nuking QtWebEngine cache")
    cache_dir = os.path.join(standarddir.cache(), 'webengine')
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)


def init():
    """Check for various issues related to QtWebKit/QtWebEngine."""
    _check_backend_modules()
    if objects.backend == usertypes.Backend.QtWebEngine:
        _handle_ssl_support()
        _handle_wayland()
        _nvidia_shader_workaround()
        _handle_nouveau_graphics()
        _handle_cache_nuking()
    else:
        assert objects.backend == usertypes.Backend.QtWebKit, objects.backend
        _handle_ssl_support(fatal=True)

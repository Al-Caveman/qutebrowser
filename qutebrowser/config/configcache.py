# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2018 Jay Kamat <jaygkamat@gmail.com>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""A 'high-performance' cache for the config system.

Useful for areas which call out to the config system very frequently, DO NOT
modify the value returned, DO NOT require per-url settings, do not change
frequently, and do not require partially 'expanded' config paths.

If any of these requirements are broken, you will get incorrect or slow
behavior.
"""

from qutebrowser.config import config


class ConfigCache():

    def __init__(self) -> None:
        self.cache = {}
        config.instance.changed.connect(self.config_changed)

    def config_changed(self, attr: str) -> None:
        if attr in self.cache:
            self.cache[attr] = config.instance.get(attr)

    def __setitem__(self, attr):
        raise Exception("ConfigCache cannot be used to set values.")

    def __getitem__(self, attr: str):
        if attr not in self.cache:
            assert not config.instance.get_opt(attr).supports_pattern
            self.cache[attr] = config.instance.get(attr)
        return self.cache[attr]

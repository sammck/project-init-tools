#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard GitHub gh CLI installer/upgrader"""

from .installer import (
    install_gh,
    gh_is_installed,
    get_gh_version,
    get_gh_prog,
  )

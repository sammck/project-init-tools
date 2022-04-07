#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard Docker CLI installer/upgrader"""

from .installer import (
    install_docker,
    docker_is_installed,
    get_docker_version,
    get_docker_prog,
  )

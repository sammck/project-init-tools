#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard docker-compose CLI installer/upgrader"""

from .installer import (
    install_docker_compose,
    docker_compose_is_installed,
    get_docker_compose_version,
    get_docker_compose_prog,
  )

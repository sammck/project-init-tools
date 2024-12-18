#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard AWS CLI installer/upgrader"""

from .installer import (
    install_aws_cli,
    aws_cli_is_installed,
    get_aws_cli_version,
    get_aws_cli_prog,
  )

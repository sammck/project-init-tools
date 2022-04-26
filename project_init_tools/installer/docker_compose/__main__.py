#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard docker-compose CLI installer/upgrader command-line tool"""

from typing import Optional, Sequence

# do not use relative imports
from project_init_tools.installer.docker_compose import install_docker_compose as install
import project_init_tools.installer.docker_compose.installer as installer

def main(argv: Optional[Sequence[str]]=None):
  import argparse

  parser = argparse.ArgumentParser(description='Install or upgrade docker-compose.')
  parser.add_argument('--verbose', '-v', action='store_true', default=False,
                      help='Provide verbose output.')
  parser.add_argument('--force', '-f', action='store_true', default=False,
                      help='Force installation even if not required.')
  parser.add_argument('--upgrade', '-u', action='store_true', default=False,
                      help='Upgrade to latest version. Shorthand for --min-version=latest. Ignored if --min-version is provided.')
  parser.add_argument('--min-version', default=None,
                      help='Upgrade to at least the specified version. May be "latest". By default, no upgrade is performed if installed.')

  args = parser.parse_args(argv)

  installer.verbose = args.verbose
  force: bool = args.force
  upgrade: bool = args.upgrade
  min_version: Optional[str] = args.min_version

  if min_version is None:
    if upgrade:
      min_version = 'latest'

  install(
        min_version=min_version,
        force=force
    )

if __name__ == "__main__":
  main()

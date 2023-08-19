#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard Poetry CLI installer/upgrader command-line tool"""

from typing import Optional, Sequence

# do not use relative imports
from project_init_tools.installer.poetry import install_poetry
import project_init_tools.installer.poetry.installer

def main(argv: Optional[Sequence[str]]=None, prog: Optional[str]=None):
  import argparse

  parser = argparse.ArgumentParser(prog=prog, description='Install or upgrade Python Poetry package manager.')
  parser.add_argument('--verbose', '-v', action='store_true', default=False,
                      help='Provide verbose output.')
  parser.add_argument('--force', '-f', action='store_true', default=False,
                      help='Force installation even if not required.')
  parser.add_argument('--upgrade', '-u', action='store_true', default=False,
                      help='Upgrade to latest version. Shorthand for --min-version=latest. Ignored if --min-version is provided.')
  parser.add_argument('--min-version', default=None,
                      help='Upgrade to at least the specified version. May be "latest". By default, no upgrade is performed if installed.')

  args = parser.parse_args(argv)

  project_init_tools.installer.poetry.installer.verbose = args.verbose
  force: bool = args.force
  upgrade: bool = args.upgrade
  min_version: Optional[str] = args.min_version

  if min_version is None:
    if upgrade:
      min_version = 'latest'

  install_poetry(
        min_version=min_version,
        force=force
    )

if __name__ == "__main__":
  main()

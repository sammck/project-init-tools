#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard Pulumi CLI installer/upgrader command-line tool"""

from typing import Optional, Sequence

# do not use relative imports
from project_init_tools.installer.pulumi import default_pulumi_dir, install_pulumi
import project_init_tools.installer.pulumi.installer

def main(argv: Optional[Sequence[str]]=None):
  import argparse

  parser = argparse.ArgumentParser(description='Install or upgrade Python Poetry package manager.')
  parser.add_argument(
      '--verbose', '-v', action='store_true', default=False,
      help='Provide verbose output.'
    )
  parser.add_argument(
      '--force', '-f', action='store_true', default=False,
      help='Force installation even if not required.'
    )
  parser.add_argument(
      '--upgrade', '-u', action='store_true', default=False,
      help='Upgrade to latest version. Shorthand for --min-version=latest. Ignored if --min-version is provided.'
    )
  parser.add_argument(
      '--dir', '-d', dest='dirname', default=None,
      help=f"Install in the specified directory. Default={default_pulumi_dir}"
    )
  parser.add_argument('--min-version', default=None,
      help='Upgrade to at least the specified version. May be "latest". By default, no upgrade is performed if installed.'
    )
  parser.add_argument('--install-version', default=None,
      help='The version to install if installation is required. May be "latest". By default, the latest version is installed.'
    )

  args = parser.parse_args(argv)

  project_init_tools.installer.pulumi.installer.verbose = args.verbose
  force: bool = args.force
  upgrade: bool = args.upgrade
  dirname: Optional[str] = args.dirname
  min_version: Optional[str] = args.min_version
  install_version: Optional[str] = args.install_version

  if min_version is None:
    if upgrade:
      min_version = 'latest'

  install_pulumi(
        dirname,
        min_version=min_version,
        upgrade_version=install_version,
        force=force
    )

if __name__ == "__main__":
  main()

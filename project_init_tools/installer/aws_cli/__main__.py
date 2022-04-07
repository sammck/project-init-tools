#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Standard AWS CLI installer/upgrader command-line tool"""

from typing import Optional, Sequence

# do not use relative imports
from project_init_tools.installer.aws_cli import install_aws_cli

def main(argv: Optional[Sequence[str]]=None):
  import argparse

  parser = argparse.ArgumentParser(description='Install or upgrade AWS CLI.')
  parser.add_argument('--force', '-f', action='store_true', default=False,
                      help='Force installation even if not required.')

  args = parser.parse_args(argv)

  force: bool = args.force

  install_aws_cli(force=force)

if __name__ == "__main__":
  main()

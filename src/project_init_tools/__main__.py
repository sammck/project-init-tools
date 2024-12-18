#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""project-init-tools commandline tool"""

from types import ModuleType
from typing import Optional, Sequence, Tuple, List, Union, Protocol, Dict

import sys
import argparse

installer_list: List[Union[str, Tuple[str, str]]] = [
    'aws-cli',
    'aws-sam-cli',
    'docker',
    'docker-compose',
    'gh',
    'poetry',
    'pulumi',
]

class CommandHandler(Protocol):
  def __call__(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    ...

class InstallerEntry(Protocol):
  def __call__(self, argv: Optional[Sequence[str]]=None, prog: Optional[str]=None):
    ...

class Installer:
  name: str
  module_name: str
  func_name: str = 'main'
  # module: ModuleType
  func: InstallerEntry

  def __init__(self, initializer: Union[str, Tuple[str, str]]):
    if isinstance(initializer, str):
      self.name = initializer
      short_module_name = initializer.replace('-', '_')
    else:
      assert isinstance(initializer, tuple)
      self.name, short_module_name = initializer
    self.module_name = f'project_init_tools.installer.{short_module_name}.__main__'
    imp_mod: InstallerEntry = __import__(self.module_name, fromlist=[self.func_name])
    self.func = getattr(imp_mod, self.func_name)

installers: Dict[str, Installer] = {}
for _initializer in installer_list:
  _installer = Installer(_initializer)
  installers[_installer.name] = _installer

def cmd_bare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
  parser.print_help()
  return 1

def cmd_install(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
  installer_name: str = args.package
  installer = installers.get(installer_name)
  if installer is None:
    raise ValueError(f'Unknown installer virtual package: {installer_name}')
  prog = f'{parser.prog} install {installer_name}'
  installer_args: List[str] = args.installer_args
  installer.func(argv=installer_args, prog=prog)
  return 0

def main(argv: Optional[Sequence[str]]=None, prog: Optional[str]=None) -> int:
  parser = argparse.ArgumentParser(prog=prog, description='Project initialization tool.')
  parser.set_defaults(func=cmd_bare)

  subparsers = parser.add_subparsers(help='command help')
  parser_install = subparsers.add_parser('install', help='Install tools/packages')
  parser_install.add_argument('package', help='Virtual package to install', choices=sorted(installers.keys()))
  parser_install.add_argument('installer_args', nargs=argparse.REMAINDER, help='Installer arguments')
  parser_install.set_defaults(func=cmd_install)

  args = parser.parse_args(argv)
  func: Optional[CommandHandler] = args.func
  if func is None:
    parser.print_help()
    return 1
  return func(parser, args)

def main_script():
  rc = main(prog="project-init-tools")
  sys.exit(rc)

if __name__ == '__main__':
  main_script()

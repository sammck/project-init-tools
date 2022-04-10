#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Standalone python script (no dependencies other than standard python 3.7+) that
can create and manage a per-app virtualenv under ~/.local/cache and install a python app in it.
Suitable for running as a piped script from curl.
"""

from typing import (
    Optional,
    Sequence,
    MutableMapping,
    List,
    Dict,
    Any,
  )

import argparse
import sys
import os
import re
import pathlib
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
import subprocess
import venv
import tempfile

def searchpath_split(searchpath: Optional[str]=None) -> List[str]:
  if searchpath is None:
    searchpath = os.environ['PATH']
  result = [ x for x in searchpath.split(os.pathsep) if x != '' ]
  return result

def searchpath_join(dirnames: List[str]) -> str:
  return os.pathsep.join(dirnames)

def searchpath_normalize(searchpath: Optional[str]=None) -> str:
  return searchpath_join(searchpath_split(searchpath))

def searchpath_parts_contains_dir(parts: List[str], dirname: str) -> bool:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  return dirname in parts

def searchpath_contains_dir(searchpath: Optional[str], dirname: str) -> bool:
  return searchpath_parts_contains_dir(searchpath_split(searchpath), dirname)

def searchpath_parts_remove_dir(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [ x for x in parts if x != dirname ]
  return result

def searchpath_remove_dir(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_remove_dir(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [dirname] + searchpath_parts_remove_dir(parts, dirname)
  return result

def searchpath_prepend(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend_if_missing(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = [dirname] + parts
  return result

def searchpath_prepend_if_missing(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend_if_missing(searchpath_split(searchpath), dirname))

def searchpath_parts_force_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = searchpath_parts_remove_dir(parts, dirname) + [dirname]
  return result

def searchpath_force_append(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_force_append(searchpath_split(searchpath), dirname))

def searchpath_parts_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = parts + [dirname]
  return result

def searchpath_append(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_append(searchpath_split(searchpath), dirname))

def deactivate_virtualenv(env: Optional[MutableMapping]=None):
  if env is None:
    env = os.environ
  if 'VIRTUAL_ENV' in env:
    venv = env['VIRTUAL_ENV']
    del env['VIRTUAL_ENV']
    if 'POETRY_ACTIVE' in env:
      del env['POETRY_ACTIVE']
    if 'PATH' in env:
      venv_bin = os.path.join(venv, 'bin')
      env['PATH'] = searchpath_remove_dir(env['PATH'], venv_bin)

def activate_virtualenv(venv_dir: str, env: Optional[MutableMapping]=None):
  venv_dir = os.path.abspath(os.path.normpath(os.path.expanduser(venv_dir)))
  venv_bin_dir = os.path.join(venv_dir, 'bin')
  if env is None:
    env = os.environ
  deactivate_virtualenv(env)
  env['VIRTUAL_ENV'] = venv_dir
  env['PATH'] = searchpath_prepend_if_missing(env['PATH'], venv_bin_dir)

class CmdExitError(RuntimeError):
  exit_code: int

  def __init__(self, exit_code: int=1, msg: Optional[str]=None):
    if msg is None:
      msg = f"Command exited with return code {exit_code}"
    super().__init__(msg)
    self.exit_code = exit_code

class ArgparseExitError(CmdExitError):
  pass

class NoExitArgumentParser(argparse.ArgumentParser):
  def exit(self, status=0, message=None):
    if message:
      self._print_message(message, sys.stderr)
    raise ArgparseExitError(status, message)

class Cli:
  home_dir = os.path.expanduser('~')
  pit_cache_dir = os.path.join(home_dir, '.cache', 'project-init-tools')
  apps_dir = os.path.join(pit_cache_dir, 'apps')

  argv: Optional[Sequence[str]] = None
  verbose: bool = False
  args: argparse.Namespace
  _app_name: Optional[str] = None
  _no_venv_env: Optional[Dict[str, str]] = None
  _venv_env: Optional[Dict[str, str]] = None

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self.argv = argv

  def cmd_bare(self) -> int:
    raise CmdExitError(msg="A subcommand is required")

  @property
  def app_name(self) -> str:
    assert not self._app_name is None
    return self._app_name

  @property
  def app_dir(self) -> str:
    return os.path.join(self.apps_dir, self.app_name)

  @property
  def app_venv_dir(self) -> str:
    return os.path.join(self.app_dir, '.venv')

  @property
  def app_bin_dir(self) -> str:
    return os.path.join(self.app_venv_dir, 'bin')

  @property
  def no_venv_env(self) -> Dict[str, str]:
    if self._no_venv_env is None:
      no_venv_env = dict(os.environ)
      deactivate_virtualenv(no_venv_env)
      self._no_venv_env = no_venv_env
    return self._no_venv_env

  @property
  def venv_env(self) -> Dict[str, str]:
    if self._venv_env is None:
      app_venv_dir = self.app_venv_dir
      venv_env = dict(self.no_venv_env)
      activate_virtualenv(app_venv_dir, venv_env)
      self._venv_env = venv_env
    return self._venv_env

  @property
  def python_prog(self) -> str:
    return os.path.join(self.app_bin_dir, 'python3')

  @property
  def pip_prog(self) -> str:
    return os.path.join(self.app_bin_dir, 'pip3')

  @property
  def project_init_helper_prog(self) -> str:
    return os.path.join(self.app_bin_dir, 'project-init-helper')

  @classmethod
  def package_name_from_package_spec(self, package_spec: str):
    package_name = package_spec
    if 'egg=' in package_name:
      package_name = package_name.rsplit('egg=', 1)[1]
    if '@' in package_name:
      package_name = package_name.split('@', 1)[0]
    if '==' in package_name:
      package_name = package_name.split('==', 1)[0]
    if '[' in package_name:
      package_name = package_name.split('[', 1)[0]
    if package_name.endswith('.tar.gz'):
      package_name = package_name[:-7]
    if package_name.endswith('.git'):
      package_name = package_name[:-7]
    if '/' in package_name:
      package_name = package_name.rsplit('/', 1)[1]
    if package_name == '' or '.' in package_name or '#' in package_name:
      raise ValueError(f"Unable to determine package name from package spec: {package_spec}")
    return package_name

  def do_install(
      self,
      package: str,
      app_name: Optional[str],
      update: bool,
      clean: bool,
      app_cmd_prog: Optional[str] = None,
      stdout: Any = sys.stdout,
      stderr: Any = sys.stderr,
      ) -> str:
    if app_name is None:
      app_name = self.package_name_from_package_spec(package)

    self._app_name = app_name

    app_dir = self.app_dir
    app_venv_dir = self.app_venv_dir
    app_bin_dir = self.app_bin_dir

    if not app_cmd_prog is None:
      app_cmd_prog = os.path.abspath(os.path.join(app_bin_dir, os.path.normpath(os.path.expanduser(app_cmd_prog))))
    app_cmd_prog_exists = app_cmd_prog is not None and os.path.exists(app_cmd_prog)

    if update or clean or not app_cmd_prog_exists:
      if not os.path.isdir(app_dir):
        os.makedirs(app_dir)

      builder = venv.EnvBuilder(
          clear=clean,
        )
      builder.create(app_venv_dir)
      
      python = self.python_prog
      pip = self.pip_prog
      no_venv_env = self.no_venv_env

      if update or not os.path.exists(pip):
        cmd = [python, '-m', 'ensurepip']
        if update:
          cmd.append('--upgrade')
        subprocess.check_call(cmd, env=no_venv_env, stdout=stdout, stderr=stderr)

      if update or not app_cmd_prog_exists:
        cmd = [pip, 'install']
        if update:
          cmd.append('--upgrade')
        cmd.append('wheel')
        subprocess.check_call(cmd, env=no_venv_env, stdout=stdout, stderr=stderr)

        cmd = [pip, 'install']
        if update:
          cmd.append('--upgrade')
        cmd.append(package)
        subprocess.check_call(cmd, env=no_venv_env, stdout=stdout, stderr=stderr)

    return app_dir

  def cmd_install(self) -> int:
    args = self.args
    update: bool = args.install_update
    clean: bool = args.install_clean
    package: str = args.package_name
    app_name: Optional[str] = args.app_name
    app_path_file: Optional[str] = args.app_path_file

    app_dir = self.do_install(package, app_name=app_name, update=update, clean=clean)

    if not app_path_file is None:
      with open(app_path_file, 'w', encoding='utf-8') as f:
        f.write(app_dir)

    return 0

  def cmd_run(self) -> int:
    args = self.args
    update: bool = args.install_update
    clean: bool = args.install_clean
    package: str = args.package_name
    app_name: Optional[str] = args.app_name
    app_cmd: List[str] = args.app_cmd
    app_cmd_prog = app_cmd[0] if len(app_cmd) > 0 else None 
    if self.verbose:
      self.do_install(
          package,
          app_name=app_name,
          update=update,
          clean=clean,
          app_cmd_prog=app_cmd_prog,
        )
    else:
      with tempfile.NamedTemporaryFile() as f_install_log:
        try:
          self.do_install(
              package,
              app_name=app_name,
              update=update,
              clean=clean,
              app_cmd_prog=app_cmd_prog,
              stdout=f_install_log,
              stderr=subprocess.STDOUT
            )
        except Exception as e:
          f_install_log.flush()
          f_install_log.seek(0)
          sys.stderr.write(f_install_log.read().decode('utf-8'))
          raise

    if len(app_cmd) > 0:
      cmd = app_cmd[:]
      cmd[0] = os.path.abspath(os.path.join(self.app_bin_dir, os.path.normpath(os.path.expanduser(cmd[0]))))

      venv_env = self.venv_env
      subprocess.check_call(
          cmd,
          env=venv_env
        )

    return 0

  def get_parser(self) -> argparse.ArgumentParser:
    parser = NoExitArgumentParser()
    parser.set_defaults(func=self.cmd_bare)
    parser.add_argument(
        '--traceback', "--tb",
        action='store_true',
        default=False,
        help='Display detailed exception information')
    parser.add_argument("-v", "--verbose",
        help="Verbose output",
        default=False,
        action="store_true"
      )

    subparsers = parser.add_subparsers(
                        title='Commands',
                        description='Valid commands',
                        help='Additional help available with "local_venv_app_install <command-name> -h"')

    # ======================= install

    parser_install = subparsers.add_parser(
        'install',
        description='''Install a python app/package in its own virtualenv private to this user.'''
      )
    parser_install.add_argument(
        '-n', '--name',
        dest="app_name",
        default=None,
        help='Local name of the app. By default, derived from package_name')
    parser_install.add_argument(
        '-u', '--update',
        dest="install_update",
        default=False,
        action='store_true',
        help='Update the package if it is already installed')
    parser_install.add_argument(
        '--clean',
        dest="install_clean",
        default=False,
        action='store_true',
        help='Force a clean installation of the package')
    parser_install.add_argument(
        '-o', '--app-path-file',
        default=None,
        help='The name of a file  to which the installed application\'s path will be written')
    parser_install.add_argument('package_name',
                        help='The package to install, as provided to "pip3 install".')
    parser_install.set_defaults(func=self.cmd_install)

    # ======================= run

    parser_run = subparsers.add_parser(
        'run',
        description='''Install a python app/package in its own virtualenv and run a command in the virtualenv.'''
      )
    parser_run.add_argument(
        '-n', '--name',
        dest="app_name",
        default=None,
        help='Local name of the app. By default, derived from package_name')
    parser_run.add_argument(
        '-u', '--update',
        dest="install_update",
        default=False,
        action='store_true',
        help='Update the package if it is already installed')
    parser_run.add_argument(
        '--clean',
        dest="install_clean",
        default=False,
        action='store_true',
        help='Force a clean installation of the package')
    parser_run.add_argument('package_name',
                        help='The package to install, as provided to "pip3 install".')
    parser_run.add_argument('app_cmd', nargs=argparse.REMAINDER,
                        help='Command and arguments as would be used within the virtualenv.')
    parser_run.set_defaults(func=self.cmd_run)

    return parser

  def __call__(self) -> int:
    parser = self.get_parser()
    try:
      args = parser.parse_args(self.argv)
    except ArgparseExitError as ex:
      return ex.exit_code
    traceback: bool = args.traceback
    try:
      self.verbose = args.verbose
      self.args = args
      rc = args.func()
    except Exception as ex:
      if isinstance(ex, CmdExitError):
        rc = ex.exit_code
      else:
        rc = 1
      if rc != 0:
        if traceback:
          raise

        print(f"local_venv_app_install: error: {ex}", file=sys.stderr)
    return rc

def run(argv: Optional[Sequence[str]]=None) -> int:
  try:
    rc = Cli(argv)()
  except CmdExitError as ex:
    rc = ex.exit_code
  return rc

if __name__ == "__main__":
  sys.exit(run())

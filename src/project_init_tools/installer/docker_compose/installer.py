#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard docker-compose CLI"""

from ast import Module
from typing import TextIO, Tuple, Optional, Dict, Iterator, Sequence, cast
from pathlib import Path

import subprocess
import sys
import os
import argparse
import re
import urllib.request
from urllib.parse import urlparse
import http.client
import platform
import tempfile
import shutil
import shlex
import types
from enum import Enum
from contextlib import contextmanager

from ...exceptions import ProjectInitError

from ...util import (
    command_exists,
    find_command_in_path,
    download_url_text,
    run_once,
    sudo_check_output_stderr_exception,
    check_version_ge,
    get_current_architecture,
    get_current_system,
    atomic_mv,
    find_command_in_path,
)

from ...github_util import get_github_project_latest_release_tag

MIN_DOCKER_COMPOSE_VERSION = "2.20.2"

verbose: bool = False

home_dir = os.path.expanduser("~")
default_docker_compose_bin_dir = os.path.join(home_dir, '.local', 'bin')
default_docker_plugin_dir = os.path.join(home_dir, '.docker', 'cli-plugins')
default_docker_compose_cmd = os.path.join(default_docker_compose_bin_dir, 'docker-compose')

@run_once
def get_docker_compose_latest_version() -> str:
  """
  Returns the latest version of docker-compose CLI available for download
  """
  result = get_github_project_latest_release_tag('docker/compose')
  if result.startswith('v'):
    result = result[1:]
  return result

def download_docker_compose(dirname: str, version: Optional[str]=None, stderr: TextIO=sys.stderr) -> str:
  if version is None:
    version = get_docker_compose_latest_version()
  version_tag = version
  if not version_tag.startswith('v'):
    version_tag = 'v' + version_tag
  dirname = os.path.abspath(os.path.expanduser(dirname))
  result: str = os.path.join(dirname, 'docker-compose')
  temp_file = result + '.tmp'
  os_system = get_current_system()
  arch = get_current_architecture()
  url = f"https://github.com/docker/compose/releases/download/{version_tag}/docker-compose-{os_system}-{arch}"

  if not os.path.isdir(dirname):
    os.makedirs(dirname)
  urllib.request.urlretrieve(url, temp_file)
  os.chmod(temp_file, 0o755)
  atomic_mv(temp_file, result)
  return result

def get_docker_compose_prog(dirname: Optional[str]=None) -> Optional[str]:
  if dirname is None:
    result = find_command_in_path('docker-compose')
    if result is None:
      result = get_docker_compose_prog(dirname=default_docker_compose_bin_dir)
  else:
    result = os.path.join(dirname, 'docker-compose')
    if not os.path.exists(result):
      result = None
  return result

def require_docker_compose_prog(dirname: Optional[str]=None) -> str:
  result = get_docker_compose_prog(dirname=dirname)
  if result is None:
    raise ProjectInitError("Unable to locate docker-compose executable")
  return result

def docker_compose_is_installed(dirname: Optional[str]=None) -> bool:
  return not get_docker_compose_prog(dirname=dirname) is None

def get_docker_compose_version(dirname: Optional[str]=None) -> str:
  version = cast(bytes,
      sudo_check_output_stderr_exception(
          [require_docker_compose_prog(dirname=dirname), 'version', '--short'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  if version.startswith('v'):
    version = version[1:]
  return version

def create_docker_compose_symlink(
      dirname: Optional[str]=None,
      plugin_path: Optional[str]=None,
      plugin_dirname: Optional[str]=None
    ) -> str:
  if dirname is None:
    dirname = default_docker_compose_bin_dir
  dirname = os.path.abspath(os.path.expanduser(dirname))
  if plugin_dirname is None:
    plugin_dirname = default_docker_plugin_dir
  plugin_dirname = os.path.abspath(os.path.expanduser(plugin_dirname))
  if plugin_path is None:
    plugin_path = os.path.join(plugin_dirname, 'docker-compose')
  if not os.path.exists(plugin_path):
    raise FileNotFoundError(f"docker-compose plugin not found at {plugin_path}")
  symlink_path = os.path.join(dirname, 'docker-compose')
  if symlink_path == plugin_path:
    print(f"docker-compose plugin directly installed at {symlink_path}; no symlink needed", file=sys.stderr)
  if os.path.exists(symlink_path):
    if os.path.islink(symlink_path):
      if os.readlink(symlink_path) == plugin_path:
        print(f"docker-compose symlink already installed at {symlink_path} and points to {plugin_path}; no action needed", file=sys.stderr)
        return symlink_path
      print(f"docker-compose symlink installed at {symlink_path} points to wrong plugin path {plugin_path}; replacing", file=sys.stderr)
      os.unlink(symlink_path)
    else:
      print(f"docker-compose symlink location {symlink_path} is not a symlink; replacing with symlink", file=sys.stderr)
      os.unlink(symlink_path)
  else:
    print(f"docker-compose symlink not installed at {symlink_path}; installing", file=sys.stderr)
  os.symlink(plugin_path, symlink_path)
  return symlink_path

def install_docker_compose(
      dirname:Optional[str] = None,
      plugin_dirname: Optional[str] = None,
      min_version: Optional[str] = None,
      upgrade_version: Optional[str] = None,
      force: bool = False,
      stderr: TextIO = sys.stderr,
    ) -> Tuple[str, bool]:
  """Installs or upgrades the standard docker-compose CLI

  Args:
      dirname (Optional[str], optional):
                      The directory where the docker-compose symlink should be installed. If None, "$HOME/.local/bin"
                      will be used. If the same as plugin_dirname, then no symlink will be installed. Defaults to None.
      plugin_dirname (Optional[str], optional):
                      The directory where the docker-compose CLI plugin should be installed. If None, "$HOME/.docker/cli-plugins"
                      will be used. If the same as dirname, then no symlink will be installed. Defaults to None.
      min_version (Optional[str], optional):
                       The minimum installed version before an upgrade will be forced. If 'latest',
                       then the latest version will be installed. If None,any installed version is
                       accepted. Defaults to None.
      upgrade_version (Optional[str], optional): The version to install if an installation is performed.
                       If None or 'latest', then the latest version will be installed.. Defaults to None.
      force (bool, optional):
                       If True, the installation will be refreshed even if the current version satisfies other
                       constraints. Defaults to False.

  Raises:
      RuntimeError: The requested upgrade_version is less than min_version
      RuntimeError: The requested upgrade version is greater than the latest available version

  Returns:
      Tuple[str, bool]: A Tuple with:
                         [0]: The absolute path to the docker-compose executable
                         [1]: True iff an update/install was done
  """
  if dirname is None:
    dirname = default_docker_compose_bin_dir
  dirname = os.path.abspath(os.path.expanduser(dirname))
  if plugin_dirname is None:
    plugin_dirname = default_docker_plugin_dir
  plugin_dirname = os.path.abspath(os.path.expanduser(plugin_dirname))
  if upgrade_version == 'latest':
    upgrade_version = None

  if min_version == 'latest':
    min_version = get_docker_compose_latest_version()

  if not upgrade_version is None and not min_version is None and not check_version_ge(upgrade_version, min_version):
    raise RuntimeError("Requested docker-compose upgrade version {upgrade_version} is less than than minimum required version {min_version}")

  old_version: Optional[str] = None
  if docker_compose_is_installed(dirname):
    old_prog = get_docker_compose_prog(dirname=dirname)
    assert not old_prog is None
    old_version = get_docker_compose_version(dirname)
    if force:
      print(f"Forcing upgrade/reinstall of docker-compose version {old_version} in {dirname}", file=sys.stderr)
    else:
      if min_version is None:
        print(f"docker-compose version {old_version} is already installed in {dirname}; no need to reinstall", file=stderr)
        return old_prog, False
      if check_version_ge(old_version, min_version):
        print(f"docker-compose version {old_version} is already installed in {dirname} and meets minimum version {min_version}; no need to upgrade", file=stderr)
        return old_prog, False
      print(f"Installed docker-compose version {old_version} in {dirname} does not meet minimum version {min_version}; upgrading", file=stderr)
  else:
    print(f"docker-compose not installed in {dirname}; installing", file=stderr)

  if upgrade_version is None:
    upgrade_version = get_docker_compose_latest_version()
    if not min_version is None and not check_version_ge(upgrade_version, min_version):
      raise RuntimeError(f"Requested docker-compose upgrade version {upgrade_version} is less than than minimum required version {min_version}")

  plugin_path = download_docker_compose(plugin_dirname, version=upgrade_version, stderr=stderr)
  print(f"docker-compose plugin version {upgrade_version} successfully installed in {plugin_path}.", file=stderr)
  result = create_docker_compose_symlink(dirname=dirname, plugin_path=plugin_path, plugin_dirname=plugin_dirname)

  print(f"docker-compose cli version {upgrade_version} successfully installed in {result}.", file=stderr)
  return result, True

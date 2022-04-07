#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard docker CLI"""

from ast import Module
from typing import TextIO, Tuple, Optional, Dict, Iterator, Sequence, List, cast
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
from enum import Enum
from contextlib import contextmanager
from threading import Lock
import glob

from ...exceptions import ProjectInitError
from ..os_packages.util import os_group_add_user, update_gpg_keyring, PackageList, update_apt_sources_list

from ..util import (
    command_exists,
    command_exists_outside_venv,
    find_command_in_path,
    find_command_in_path_outside_venv,
    download_url_text,
    get_linux_distro_name,
    os_group_exists,
    os_group_includes_user,
    run_once,
    running_as_root,
    should_run_with_group,
    sudo_check_output_stderr_exception,
    check_version_ge,
    file_contents,
    get_current_os_user,
)


MIN_DOCKER_CLIENT_VERSION = "20.0.0"
MIN_DOCKER_SERVER_VERSION = MIN_DOCKER_CLIENT_VERSION

verbose: bool = False

def escaped_hex(hexv: str) -> str:
  if len(hexv) % 2 != 0:
    raise ProjectInitError("Hex values must contain an even number of digits")
  result = ''
  for i in range(0, len(hexv), 2):
    result += f"\\x{hexv[i:i+2]}"
  return result

class BinFmtEntry:
  _pathname: str
  _text_data: str
  _lines: List[str]

  def __init__(self, pathname: str, text_data: Optional[str]=None):
    self._pathname = pathname
    if text_data is None:
      text_data = file_contents(pathname)
    self._text_data = text_data
    self._lines = text_data.split('\n')

  def get_field(self, field_name: str, default: Optional[str]=None) -> Optional[str]:
    m = field_name + ' '
    for v in self._lines:
      vs = v.rstrip()
      if vs == field_name:
        return ''
      if vs.startswith(m):
        return vs[len(m):].lstrip()
    return default

  def get_package_name(self) -> str:
    result = self._lines[0].rstrip()
    if result == '':
      raise ProjectInitError(f"Unable to read binfmts package name from {self._pathname}")
    return result

  def is_fixed_binary(self) -> bool:
    fline = None if len(self._lines) < 10 else self._lines[9].rstrip()
    if fline is None or not fline in ("", "yes"):
      raise ProjectInitError(f"Unable to read binfmts fixed binary status from {self._pathname}")

    return fline == 'yes'

_binfmt_cache_lock = Lock()
_binfmt_cache: Dict[str, BinFmtEntry] = {}
def get_binfmt(target_arch: str) -> BinFmtEntry:
  with _binfmt_cache_lock:
    result = _binfmt_cache.get(target_arch, None)
    if result is None:
      result = BinFmtEntry(f"/var/lib/binfmts/qemu-{target_arch}")
      _binfmt_cache[target_arch] = result
  return result

def invalidate_binfmt_cache(target_arch: str):
  with _binfmt_cache_lock:
    if target_arch in _binfmt_cache:
      del _binfmt_cache[target_arch]

def fix_binfmt_qemu_binary(target_arch: str):
  binfmt = get_binfmt(target_arch)
  package_name = binfmt.get_package_name()
  magic = cast(str, binfmt.get_field('magic', ''))
  escaped_magic = escaped_hex(magic)
  mask = cast(str, binfmt.get_field('mask', ''))
  escaped_mask = escaped_hex(mask)
  interpreter = cast(str, binfmt.get_field('interpreter', ''))
  offset = cast(str, binfmt.get_field('offset', ''))
  invalidate_binfmt_cache(target_arch)
  sudo_check_output_stderr_exception(
      [
          'update-binfmts',
          '--package', package_name,
          '--remove', f"qemu-{target_arch}",
          f"/usr/bin/qemu-{target_arch}-static"
        ],
      sudo_reason=f"Reregistering QEMU binfmts binary /usr/bin/qemu-{target_arch}-static to set --fix-binary option",
    )
  sudo_check_output_stderr_exception(
      [
          'update-binfmts',
          '--package', package_name,
          '--install', f"qemu-{target_arch}",
          interpreter,
          '--offset', offset,
          '--magic', escaped_magic,
          '--mask', escaped_mask,
          '--credentials', 'yes',
          '--fix-binary', 'yes',
        ],
      sudo_reason=f"Registering QEMU binfmts binary /usr/bin/qemu-{target_arch}-static to set --fix-binary option",
    )

def fix_binfmt_qemu_binary_if_needed(target_arch: str):
  binfmt = get_binfmt(target_arch)
  if not binfmt.is_fixed_binary():
    fix_binfmt_qemu_binary(target_arch)

def get_all_target_arches() -> List[str]:
  result: List[str] = []
  for pathname in glob.glob('/proc/sys/fs/binfmt_misc/qemu-*'):
    filename = os.path.basename(pathname)
    assert filename.startswith('qemu-')
    target_arch = filename[5:]
    result.append(target_arch)
  return result

def fix_all_binfmt_qemu_binaries_if_needed():
  for target_arch in get_all_target_arches():
    fix_binfmt_qemu_binary_if_needed(target_arch)

def docker_is_installed() -> bool:
  return command_exists_outside_venv('docker')

def get_docker_prog() -> str:
  result = find_command_in_path_outside_venv('docker')
  if result is None:
    raise FileNotFoundError("Docker program is not in PATH")
  return result

def get_docker_version() -> str:
  result = cast(bytes,
      sudo_check_output_stderr_exception(
          [get_docker_prog(), 'version', '-f{{.Client.Version}}'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  return result

def get_docker_server_version() -> str:
  result = cast(bytes,
      sudo_check_output_stderr_exception(
          [get_docker_prog(), 'version', '-f{{.Server.Version}}'],
          use_sudo=False,
          run_with_group='docker',
        )
    ).decode('utf-8').rstrip()
  return result

def install_docker(force: bool=False):
  need_client_install: bool = True
  if docker_is_installed():
    prog = get_docker_prog()
    version = get_docker_version()

    if force:
      print(f"Forcing install/upgrade of docker from existing version {version}", file=sys.stderr)
    elif check_version_ge(version, MIN_DOCKER_CLIENT_VERSION):
      print(f"Docker client version {version} is installed and in PATH at {prog}, and", file=sys.stderr)
      print(f"meets the minimum version {MIN_DOCKER_CLIENT_VERSION}. No update is necessary.", file=sys.stderr)
      need_client_install = False
    else:
      print(
          f"Docker client version {version} does not meet the minimum "
          f"version {MIN_DOCKER_CLIENT_VERSION}; upgrading", file=sys.stderr
        )
  else:
    print("Docker is not installed; installing", file=sys.stderr)

  if need_client_install:
    PackageList([ "docker-engine",  "docker.io", "containerd", "runc" ]).uninstall_all()
    pl = PackageList()
    pl.add_packages_if_missing([ "ca-certificates", "curl", "gnupg", "lsb-release" ])
    pl.add_package_if_cmd_missing("sha256sum", "coreutils")
    pl.install_all()

    variant = "stable"
    lsbrelease = get_linux_distro_name()

    # HACK: docker does not currently have a repo for ubuntu 22.04 (jammy), but they recommend using
    #   the ubuntu 20.04 (focal) repo.
    if lsbrelease == "jammy":
      lsbrelease="focal"

    update_gpg_keyring(
        "https://download.docker.com/linux/ubuntu/gpg",
        "/usr/share/keyrings/docker-archive-keyring.gpg",
        filter_cmd=["gpg", "--dearmor"]
      )

    update_apt_sources_list(
        "/etc/apt/sources.list.d/docker.list",
        "/usr/share/keyrings/docker-archive-keyring.gpg",
        "https://download.docker.com/linux/ubuntu",
        lsbrelease,
        variant)

    pl = PackageList()
    pl.add_packages_if_missing( [ "containerd.io" ] )
    pl.install_all()

    # HACK: install of docker-ce 20 often fails on first try even though it actually succeeded
    # see https://github.com/docker/for-linux/issues/989. So, we will try to install
    # docker-ce once, and if it fails, try once again.
    pl = PackageList()
    if force:
      pl.add_packages( [ "docker-ce" ] )
    else:
      pl.add_package_if_outdated("docker-ce", MIN_DOCKER_CLIENT_VERSION)

    if not pl.is_empty():
      try:
        pl.upgrade_all()
        print("Install/upgrade of docker-ce succeeded on first attempt...", file=sys.stderr)
      except subprocess.CalledProcessError as e:
        print(
            f"Install/upgrade of docker-ce failed on first attempt. "
            f"Retrying to work around docker-ce install bug...: {e}"
          )
        pl.upgrade_all()
        print("Install/upgrade of docker-ce succeeded on second attempt...", file=sys.stderr)

    pl = PackageList()
    if force:
      pl.add_packages( [ "docker-ce-cli" ] )
    else:
      pl.add_package_if_outdated("docker-ce-cli", MIN_DOCKER_CLIENT_VERSION)
    pl.upgrade_all()

    if not docker_is_installed():
      raise ProjectInitError("Docker client still not found in PATH after install/upgrade.")

    prog = get_docker_prog()
    version = get_docker_version()

    if not check_version_ge(version, MIN_DOCKER_CLIENT_VERSION):
      raise ProjectInitError(
          f"Docker client installed/upgraded, but version {version} still "
          f"does not meet the minimum version {MIN_DOCKER_CLIENT_VERSION}")

    print(f"Docker client version {version} successfully installed...", file=sys.stderr)

  if not os_group_exists('docker'):
    raise ProjectInitError(
      f"The OS group 'docker' does not exist, even though Docker client version {version} is installed")

  if not os_group_includes_user('docker'):
    print(f"User {get_current_os_user()} is not in the 'docker' OS group--adding...", file=sys.stderr)
    os_group_add_user('docker')

  if should_run_with_group('docker'):
    print(
        f"User {get_current_os_user()} is in the 'docker' OS group, but current process is not. "
        f"sudo required until shell restart...", file=sys.stderr
      )

  try:
    docker_server_version = get_docker_server_version()
  except ChildProcessError as e:
    raise ProjectInitError(
        f"Docker server is not reachable by the client, even though user {get_current_os_user()} "
        f"is in the 'docker' OS group"
      ) from e

  if not check_version_ge(docker_server_version, MIN_DOCKER_SERVER_VERSION):
    raise ProjectInitError(
        f"Docker server is reachable by the client, but its version {docker_server_version} "
        f"does not meet the minimum version {MIN_DOCKER_SERVER_VERSION}"
      )

  print(
      f"Docker server is reachable, and its version {docker_server_version} "
      f"meets the minimum version {MIN_DOCKER_SERVER_VERSION}", file=sys.stderr
    )

  pl = PackageList()
  pl.add_packages_if_missing( [ "binfmt-support", "qemu-user-static" ])
  pl.install_all()

  fix_all_binfmt_qemu_binaries_if_needed()

  print("All QEMU interpreter binaries have been registered with binfmts as --fix-binary; no further update necessary", file=sys.stderr)

  if should_run_with_group('docker'):
    print("WARNING: Command 'docker' requires membership in OS group 'docker', which is newly added for", file=sys.stderr)
    print(f"user \"{get_current_os_user()}\", and is not yet effective for the current process. Please logout", file=sys.stderr)
    print(" and log in again, or in the mean time run docker with:", file=sys.stderr)
    print(f"         sudo -E -u {get_current_os_user()} docker [<arg>...]", file=sys.stderr)

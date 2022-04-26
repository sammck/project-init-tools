#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Utilities to assist with installation of OS packages"""

import filecmp
import grp
import hashlib
import os
import platform
import subprocess
import sys
from typing import List, Optional, Set, TextIO, Union, cast, Iterator

from .exceptions import ProjectInitError

from .util import (check_version_ge, chown_root, command_exists,
                    download_url_file, file_contents, files_are_identical,
                    get_current_os_user, get_tmp_dir, os_group_includes_user,
                    run_once, sudo_check_call,
                    sudo_check_output_stderr_exception, unix_mv, os_group_exists,
                    get_gid_of_group, gid_exists, get_group_of_gid)

_os_package_metadata_stale: bool = True
def invalidate_os_package_list() -> None:
  global _os_package_metadata_stale
  _os_package_metadata_stale = True

def update_gpg_keyring(
      url: str,
      dest_file: str,
      filter_cmd: Optional[Union[str, List[str]]]=None,
      stderr: Optional[TextIO]=None,
    ) -> None:
  if stderr is None:
    stderr = sys.stderr
  tmp_file_gpg = os.path.join(get_tmp_dir(), "tmp_gpg_keyring.gpg")
  download_url_file(url, tmp_file_gpg, filter_cmd=filter_cmd)
  if os.path.exists(dest_file) and files_are_identical(dest_file, tmp_file_gpg):
    return
  print(f"Updating GPG keyring at {dest_file} (sudo required)", file=stderr)
  os.chmod(tmp_file_gpg, 0o644)
  chown_root(tmp_file_gpg, sudo_reason=f"Installing GPG keyring to {dest_file}")
  unix_mv(tmp_file_gpg, dest_file, use_sudo=True, sudo_reason=f"Installing GPG keyring to {dest_file}")

def install_gpg_keyring_if_missing(
      url: str,
      dest_file: str,
      filter_cmd: Optional[Union[str, List[str]]]=None,
      stderr: Optional[TextIO]=None,
    ) -> None:
  if not os.path.exists(dest_file):
    update_gpg_keyring(url, dest_file, filter_cmd=filter_cmd, stderr=stderr)

@run_once
def get_dpkg_arch() -> str:
  result = subprocess.check_output(['dpkg', '--print-architecture'])
  dpkg_arch = result.decode('utf-8').rstrip()
  return dpkg_arch

def update_os_package_list(force: bool=False, stderr: Optional[TextIO]=None) -> None:
  global _os_package_metadata_stale
  if force:
    _os_package_metadata_stale = True

  if _os_package_metadata_stale:
    sudo_check_call(['apt-get', 'update'], sudo_reason="Updating available apt-get package metadata", stderr=stderr)
    _os_package_metadata_stale = False

def update_apt_sources_list(dest_file: str, signed_by: str, url: str, *args, stderr: Optional[TextIO]=None) -> None:
  arch = get_dpkg_arch()
  tmp_file = os.path.join(get_tmp_dir(), "tmp_apt_source.list")
  with open(tmp_file, "w", encoding='utf-8') as f:
    print(f"deb [arch={arch} signed-by={signed_by}] {url} {' '.join(args)}", file=f)
  if os.path.exists(dest_file):
    if files_are_identical(tmp_file, dest_file):
      return
    sudo_reason= f"Updating apt-get sources list for {dest_file}; old=<{file_contents(dest_file).rstrip()}>"
  else:
    sudo_reason= f"Creating apt-get sources list for {dest_file}"
  sudo_reason += f", new=<{file_contents(tmp_file).rstrip()}>"
  os.chmod(tmp_file, 0o644)
  chown_root(tmp_file, sudo_reason=sudo_reason)
  invalidate_os_package_list()
  unix_mv(tmp_file, dest_file, use_sudo=True, sudo_reason=sudo_reason)
  update_os_package_list(stderr=stderr)

def install_apt_sources_list_if_missing(dest_file: str, signed_by: str, url: str, *args, stderr: Optional[TextIO]=None) -> None:
  if not os.path.exists(dest_file):
    update_apt_sources_list(dest_file, signed_by, url, *args, stderr=stderr)

def get_os_package_version(package_name: str) -> str:
  stdout_bytes = sudo_check_output_stderr_exception(
      ['dpkg-query', '--showformat=${Version}', '--show',
      package_name],
      use_sudo=False
    )
  return stdout_bytes.decode('utf-8').rstrip()

def os_package_is_installed(package_name: str) -> bool:
  result: bool = False
  try:
    if get_os_package_version(package_name) != '':
      result = True
  except subprocess.CalledProcessError:
    pass
  return result

def uninstall_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'remove'] + filtered, stderr=stderr, sudo_reason=f"Removing packages {filtered}")

def install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")


def update_and_install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")

def upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")


def update_and_upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")

class PackageList:
  _package_names: List[str]
  _package_name_set: Set[str]

  def __init__(self, package_names: Optional[List[str]]=None):
    self._package_names = []
    self._package_name_set = set()
    self.add_packages(package_names)

  def add_packages(self, package_names: Optional[Union[str, List[str]]]) -> None:
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set:
          self._package_names.append(package_name)
          self._package_name_set.add(package_name)

  def add_packages_if_missing(self, package_names: Optional[Union[str, List[str]]]) -> None:
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set and not os_package_is_installed(package_name):
          self.add_packages(package_name)

  def add_package_if_cmd_missing(self, cmd: str, package_name: Optional[str]=None) -> None:
    if package_name is None:
      package_name = cmd
    if not package_name in self._package_name_set and not command_exists(cmd):
      self.add_packages(package_name)

  def add_package_if_outdated(self, package_name: str, min_version: str) -> None:
    if not package_name in self._package_name_set:
      package_version: Optional[str] = None
      try:
        package_version = get_os_package_version(package_name)
      except subprocess.CalledProcessError:
        pass
      if package_version is None or not check_version_ge(package_version, min_version):
        self.add_packages(package_name)

  def install_all(self, stderr: Optional[TextIO]=None):
    if len(self._package_names) > 0:
      install_os_packages(self._package_names, stderr=stderr)

  def upgrade_all(self, stderr: Optional[TextIO]=None):
    if len(self._package_names) > 0:
      upgrade_os_packages(self._package_names, stderr=stderr)

  def uninstall_all(self, stderr: Optional[TextIO]=None):
    if len(self._package_names) > 0:
      uninstall_os_packages(self._package_names, stderr=stderr)

  def __len__(self) -> int:
    return len(self._package_names)

  def __contains__(self, package_name: str) -> bool:
    return package_name in self._package_name_set

  def __iter__(self) -> Iterator[str]:
    return sorted(self._package_names).__iter__()

  def is_empty(self) -> bool:
    return len(self._package_names) == 0

def create_os_group(
      group_name: str,
      gid: Optional[int]=None,
      required_gid: bool=True,
      is_system: bool=False,
      stderr: Optional[TextIO]=None,
    ) -> int:
  if gid is None:
    required_gid = False
  if os_group_exists(group_name):
    existing_gid = get_gid_of_group(group_name)
    if required_gid:
      if existing_gid != gid:
        raise ProjectInitError(f"OS group '{group_name}' already exists and its GID {existing_gid} differs from required GID {gid}")
    return existing_gid
  if not gid is None and gid_exists(gid):
    if required_gid:
      existing_group = get_group_of_gid(gid)
      raise ProjectInitError(f"Required GID {gid} for OS group '{group_name}' is already in use by group {existing_group}")
    else:
      gid = None
  cmd = [ 'groupadd' ]
  if is_system:
    cmd.append('--system')
  if not gid is None:
    cmd.extend( [ '-g', str(gid) ] )
  cmd.append(group_name)
  sudo_check_output_stderr_exception(cmd, stderr=stderr, sudo_reason=f"Adding OS group '{group_name}'")
  new_gid = get_gid_of_group(group_name)
  if not gid is None and new_gid != gid:
    raise ProjectInitError(f"OS group '{group_name}' successfully created, but created GID {new_gid} does not match required GID {gid}")
  return new_gid

def os_group_add_user(group_name: str, user: Optional[str]=None, stderr: Optional[TextIO]=None):
  if user is None:
    user = get_current_os_user()
  if not os_group_includes_user(user):
    sudo_check_output_stderr_exception(
        [
            'usermod', '-a', '-G', group_name, user
          ],
        stderr=stderr,
        sudo_reason=f"Adding user '{user}' to OS group '{group_name}'"
      )

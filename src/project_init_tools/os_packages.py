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
  """Create or update a GPG keyring. Used to verify 3rd-party apt packages

  Args:
      url (str):            The URL to download the GPG keyring from. The content can be transformed with filter_cmd.
      dest_file (str):      The path to the file to create or update. Normally this
                             would be /usr/share/keyrings/<keyring_name>.gpg or
                             /etc/apt/keyrings/<keyring_name>.gpg. If the file
                             already exists, it will be overwritten if the contents
                             differ from the downloaded file. If an update is required,
                             sudo will be used to overwrite the file.
      filter_cmd (Optional[Union[str, List[str]]], optional):
                            An optional filter/transformation to pass the downloaded content through.
                            A typical value is ["gpg", "--dearmor"]. Defaults to None.
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to None.
  """
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
  """Create a GPG keyring if it does not exist. Used to verify apt packages.

  Does not update existing keyrings.

  Args:
      url (str):            The URL to download the GPG keyring from if the keyring file does not exist.
                             The content can be transformed with filter_cmd.
      dest_file (str):      The path to the file to create. Normally this
                             would be /usr/share/keyrings/<keyring_name>.gpg or
                             /etc/apt/keyrings/<keyring_name>.gpg. If the file
                             already exists, it is not touched, even if it is outdated.
                             If it does not exist, sudo will be used to create the file.
      filter_cmd (Optional[Union[str, List[str]]], optional):
                            An optional filter/transformation to pass the downloaded content through.
                            A typical value is ["gpg", "--dearmor"]. Defaults to None.
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
  if not os.path.exists(dest_file):
    update_gpg_keyring(url, dest_file, filter_cmd=filter_cmd, stderr=stderr)

@run_once
def get_dpkg_arch() -> str:
  """Returns the dpkg architecture string for the current system.

  arm64: 64-bit ARM systems including Raspberry Pi 3 and 4, Mac M1, etc.
  amd64: 64-bit x86 systems including Intel and AMD
  """
  result = subprocess.check_output(['dpkg', '--print-architecture'])
  dpkg_arch = result.decode('utf-8').rstrip()
  return dpkg_arch

def update_os_package_list(force: bool=False, stderr: Optional[TextIO]=None) -> None:
  """Update the list of available apt-get packages. Identical to running 'apt-get update'.

  Keeps track of whether the list is stale and only updates if it is stale, unless
  force is True.  The list starts out stale and becomes nonstale any time
  this function is called successfully. After that, the list becomes stale
  if new apt sources are added.

  Args:
      force (bool, optional): Force an update even if the list is not stale. Defaults to False.
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
  global _os_package_metadata_stale
  if force:
    _os_package_metadata_stale = True

  if _os_package_metadata_stale:
    sudo_check_call(['apt-get', 'update'], sudo_reason="Updating available apt-get package metadata", stderr=stderr)
    _os_package_metadata_stale = False

def update_apt_sources_list(dest_file: str, signed_by: str, url: str, *args, stderr: Optional[TextIO]=None) -> None:
  """Create or update an apt-get sources list file. Used to add 3rd-party apt repositories.

  Args:
      dest_file (str):      The path to the file to create or update. Normally this would be
                              "/etc/apt/sources.list.d/<source_name>.list". If the file already
                              exists, it will be overwritten if the contents differ from the
                              expected value. If an update is required, sudo will be used to
                              overwrite the file.
      signed_by (str):      The existing GPG keyring to use to verify the apt repository. This is normally
                              "/usr/share/keyrings/<keyring_name>.gpg" or
                              "/etc/apt/keyrings/<keyring_name>.gpg".
      url (str):            The URL at which the 3rd party apt source lives.
      *args:                Additional arguments to pass to include in the
                             "deb [arch=<arch> signed-by=<keyring>] <url> <args>"
                            entry. A typical list is [get_linux_distro_name(), "stable"]
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
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
  """Create an apt-get sources list file if it does no exist. Used to add 3rd-party apt repositories.

  Does not update existing sources list files.

  Args:
      dest_file (str):      The path to the file to create or update. Normally this would be
                              "/etc/apt/sources.list.d/<source_name>.list". If the file already
                              exists, this function has no effect. If the file does not exist, sudo will be used to
                              create the file.
      signed_by (str):      The existing GPG keyring to use to verify the apt repository. This is normally
                              "/usr/share/keyrings/<keyring_name>.gpg" or
                              "/etc/apt/keyrings/<keyring_name>.gpg".
      url (str):            The URL at which the 3rd party apt source lives.
      *args:                Additional arguments to pass to include in the
                             "deb [arch=<arch> signed-by=<keyring>] <url> <args>"
                            entry. A typical list is [get_linux_distro_name(), "stable"]
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
  if not os.path.exists(dest_file):
    update_apt_sources_list(dest_file, signed_by, url, *args, stderr=stderr)

def get_os_package_version(package_name: str) -> str:
  """Returns the version of an installed OS (dpkg) package.

  The returned version string is a fully qualified dpkg version string; e.g.,
  "5:24.0.5-1~ubuntu.22.04~jammy".
  """
  stdout_bytes = sudo_check_output_stderr_exception(
      ['dpkg-query',
          '--showformat=${Version}', '--show', package_name
        ],
      use_sudo=False
    )
  return stdout_bytes.decode('utf-8').rstrip()

def os_package_is_installed(package_name: str) -> bool:
  """Returns True if the specified OS (dpkg) package is installed"""
  result: bool = False
  try:
    if get_os_package_version(package_name) != '':
      result = True
  except subprocess.CalledProcessError:
    pass
  return result

def uninstall_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  """Uninstall OS (dpkg) package(s).

  Package names that are not installed are ignored. If any package is installed,
  sudo will be used to uninstall it.
  """
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'remove'] + filtered, stderr=stderr, sudo_reason=f"Removing packages {filtered}")

def install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  """Install OS (dpkg) package(s).

  Package names that are already installed (with any version) are ignored. If any package is not installed,
  sudo will be used to install it.

  Packages that are already installed are not upgraded.
  """
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")


def update_and_install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  """Update the list of available apt-get packages and install OS (dpkg) package(s).

  Package names that are already installed (with any version) are ignored. If any package is not installed,
  then:
     1. If the list of available apt-get packages is stale, sudo is used to update it.
     2  sudo is used to install all uninstalled packages.

  Packages that are already installed are not upgraded.
  """

  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")

def upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  """Upgrade OS (dpkg) package(s).

  If there are any listed packages, they must be installed, and sudo is used to upgrade them.
  """
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")


def update_and_upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  """Update the list of available apt-get packages and upgrade OS (dpkg) package(s).

  If there are any listed packages, then:
     1. If the list of available apt-get packages is stale, sudo is used to update it.
     2  sudo is used to update all listed packages, which must be installed.
  """
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")

class PackageList:
  """A dynamically buildable list of OS (dpkg) packages to install, upgrade, or uninstall."""

  _package_names: List[str]
  _package_name_set: Set[str]

  def __init__(self, package_names: Optional[List[str]]=None):
    """Create a new PackageList--a dynamically buildable list of OS (dpkg) packages to install, upgrade, or uninstall.

    The list is ordered, but duplicate entries are automatically removed.

    Args:
        package_names (Optional[List[str]], optional):
            An optional list of package names to add to the list. Duplicate
            entries are removed. Defaults to None.
    """
    self._package_names = []
    self._package_name_set = set()
    self.add_packages(package_names)

  def add_packages(self, package_names: Optional[Union[str, List[str]]]) -> None:
    """Add package names to the end of the PackageList.

    Args:
        package_names (Optional[List[str]], optional):
            An optional list of package names to add to the end of list. Duplicate
            entries are removed. Defaults to None.
    """
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set:
          self._package_names.append(package_name)
          self._package_name_set.add(package_name)

  def add_packages_if_missing(self, package_names: Optional[Union[str, List[str]]]) -> None:
    """Add package names to the end of the PackageList if they are not installed.

    Args:
        package_names (Optional[List[str]], optional):
            An optional list of package names to add to the end of list. Installed
            packages are omitted. Duplicate entries are removed. Defaults to None.
    """
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set and not os_package_is_installed(package_name):
          self.add_packages(package_name)

  def add_package_if_cmd_missing(self, cmd: str, package_name: Optional[str]=None) -> None:
    """Adds a package to the end of the PackageList if it is not already in the PackageList and
       the specified command is not found in the search path.

    Args:
        cmd (str): The command to check for; e.g., "python3".
        package_name (Optional[str], optional):
            The name of the package to install if the command is not found.
            If None, then the cmd is used as the package name. Defaults to None.
    """
    if package_name is None:
      package_name = cmd
    if not package_name in self._package_name_set and not command_exists(cmd):
      self.add_packages(package_name)

  def add_package_if_outdated(self, package_name: str, min_version: str) -> None:
    """Adds a package to the end of the PackageList if it is not installed or
       the package version is less than a required minimum version.

       If the package is already in the PackageList, does nothing.

    Args:
        package_name (Optional[str], optional):
            The name of the package to add to the PackageList if is not installed or is outdated.
        min_version (str): The minimum required version of the package.
    """
    if not package_name in self._package_name_set:
      package_version: Optional[str] = None
      try:
        package_version = get_os_package_version(package_name)
      except subprocess.CalledProcessError:
        pass
      if package_version is None or not check_version_ge(package_version, min_version):
        self.add_packages(package_name)

  def install_all(self, stderr: Optional[TextIO]=None):
    """Install all packages in the PackageList.

    Package names that are already installed (with any version) are ignored. If any package is not installed,
    sudo will be used to install it.

    Packages that are already installed are not upgraded.
    """
    if len(self._package_names) > 0:
      install_os_packages(self._package_names, stderr=stderr)

  def upgrade_all(self, stderr: Optional[TextIO]=None):
    """Upgrade all packages in the PackageList.

    If there are any packages in the PackageList, they must be installed, and sudo is used to upgrade them.
    """
    if len(self._package_names) > 0:
      upgrade_os_packages(self._package_names, stderr=stderr)

  def uninstall_all(self, stderr: Optional[TextIO]=None):
    """Uninstall all packages in the PackageList.

    Package names that are not installed are ignored. If any package is installed,
    sudo will be used to uninstall it.
    """
    if len(self._package_names) > 0:
      uninstall_os_packages(self._package_names, stderr=stderr)

  def __len__(self) -> int:
    """Returns the number of packages in the PackageList. all names are unique."""
    return len(self._package_names)

  def __contains__(self, package_name: str) -> bool:
    """Returns True if the specified package name is in the PackageList."""
    return package_name in self._package_name_set

  def __iter__(self) -> Iterator[str]:
    """Returns an iterator over the package names in the PackageList."""
    return sorted(self._package_names).__iter__()

  def is_empty(self) -> bool:
    """Returns True if the PackageList is empty."""
    return len(self._package_names) == 0

def create_os_group(
      group_name: str,
      gid: Optional[int]=None,
      required_gid: bool=True,
      is_system: bool=False,
      stderr: Optional[TextIO]=None,
    ) -> int:
  """Create an OS group if it does not exist.

  If the group already exists, it is not modified regardless of other parameters, but
  it is checked for consistency.

  If the group does not exist, sudo is used to create it.

  Args:
      group_name (str):
          The name of the OS group to create; e.g., "docker".
      gid (Optional[int], optional):
          The GID to assign to the group. If None,
          the group is created with the next available GID.
          If not None, required_gid==True, and the group already exists, then
          this value must match the existing GID for the group.
          Defaults to None.
      required_gid (bool, optional):
          If True and the group already exists, and `gid` is not None, then the
          group's GID must match the value of `gid`. Defaults to True.
      is_system (bool, optional):
          If True, the group is created as a system group. Defaults to False.
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
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
  """Add a user to an OS group.

  The group must already exist.
  If the user is already in the group, nothing is done.
  If the user is not in the group, sudo is used to add the user to the group.

  Args:
      group_name (str):
          The name of the OS group to add the user to.
      user (Optional[str], optional):
          The name of the user to add to the group.
          If None, the current user is used. Defaults to None.
      stderr (Optional[TextIO], optional): Optional stream to which stderr output will be written. Defaults to sys.stderr.
  """
  if user is None:
    user = get_current_os_user()
  if not os_group_includes_user(group_name, user):
    sudo_check_output_stderr_exception(
        [
            'usermod', '-a', '-G', group_name, user
          ],
        stderr=stderr,
        sudo_reason=f"Adding user '{user}' to OS group '{group_name}'"
      )

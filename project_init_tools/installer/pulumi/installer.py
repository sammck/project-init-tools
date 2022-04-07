#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard Pulumi CLI"""

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
from enum import Enum
from contextlib import contextmanager

from ...exceptions import ProjectInitError

from ..util import (
    command_exists,
    command_exists_outside_venv,
    download_url_file,
    find_command_in_path,
    find_command_in_path_outside_venv,
    download_url_text,
    get_current_architecture,
    get_linux_distro_name,
    get_tmp_dir,
    os_group_exists,
    os_group_includes_user,
    run_once,
    running_as_root,
    should_run_with_group,
    sudo_check_output_stderr_exception,
    sudo_check_call_stderr_exception,
    check_version_ge,
    file_contents,
    get_current_os_user,
    unix_mv,
)

verbose: bool = False

home_dir = os.path.expanduser("~")
default_pulumi_dir = os.path.join(home_dir, '.pulumi')
default_pulumi_bin_dir = os.path.join(default_pulumi_dir, 'bin')
default_pulumi_cmd = os.path.join(default_pulumi_bin_dir, 'pulumi')
pulumi_latest_version_url = "https://www.pulumi.com/latest-version"
pulumi_tarball_base_url="https://get.pulumi.com/releases/sdk/pulumi"

@run_once
def get_pulumi_latest_version() -> str:
  """
  Returns the latest version of Pulumi CLI available for download
  """
  with urllib.request.urlopen(pulumi_latest_version_url) as resp:
    contents: bytes = resp.read()
  pulumi_latest_version = contents.decode('utf-8').strip()
  return pulumi_latest_version

def get_pulumi_tarball_url(version: Optional[str]=None):
  """
  Gets the full URL to the tarball for the specified version of Pulumi CLI,
  or the latest version.

  Args:
      version (Optional[str], optional): The desired version of Pulumi CLI,
      or None for the latest version. Defaults to None.
  """

  if version is None:
    version = get_pulumi_latest_version()

  platform_system = platform.system()    # Linux or Darwin
  if not platform_system in [ 'Linux', 'Darwin' ]:
    raise RuntimeError(f"OS platform \"{platform_system}\" is not supported")
  pulumi_os = platform_system.lower()
  platform_machine = platform.machine()  # aarch64 or arm64 for arm, x86_64 for intel/amd
  pulumi_arch: str
  if platform_machine  in [ 'aarch64', 'arm64' ]:
    pulumi_arch = 'arm64'
  elif platform_machine == 'x86_64':
    pulumi_arch = 'x64'
  else:
    raise RuntimeError(f"CPU architecture \"{platform_machine}\" is not supported")

  result = f"{pulumi_tarball_base_url}-v{version}-{pulumi_os}-{pulumi_arch}.tar.gz"
  return result

def download_file(url: str, dirname: str='.', filename: Optional[str]=None) -> str:
  """
  Downloads a file from http/https.

  Args:
      url:                                The URL that will provide the contents of the file
      dirname (str, optional):            The Directory to which filename is relative.  Defaults to '.'.
      filename (Optional[str], optional): The pathname in which to place the tarball, or None to use the last element of the url as a filename.
                                          Evaluated relative to dirname. Defaults to None.
  Returns:
      str: The path where the tarball was placed
  """
  dirname = os.path.expanduser(dirname)
  if filename is None:
    url_path = urlparse(url).path
    filename = os.path.basename(url_path)
  filename = os.path.abspath(os.path.join(dirname, os.path.basename(filename)))
  download_url_file(url, filename)
  return filename


def download_pulumi_tarball(
      version: Optional[str]=None,
      dirname: str='.',
      filename: Optional[str]=None,
    ) -> Tuple[str, str]:
  """
  Downloads a tarball for a specific version of Pulumi CLI, or the latest version.

  Args:
      version (Optional[str], optional): The desired version, or None for the latest version. Defaults to None.
      dirname (str, optional): The directory in which to place the tarball. Defaults to '.'.
      filename (Optional[str], optional): The pathname in which to place the tarball, or None to use the last element of the url as a filename.
                                          Evaluated relative to dirname. Defaults to None.

  Returns:
      Tuple[str, str]: A tuple with:
                          [0] The path where the tarball was placed
                          [1] The URL from which the tarball was fetched
  """
  url = get_pulumi_tarball_url(version=version)
  pathname = download_file(url, dirname=dirname, filename=filename)
  return pathname, url

class TarFilter(Enum):
  BZIP2 = 'bzip2'
  XZ = 'xz'
  LZIP = 'lzip'
  LZMA = 'lzma'
  LZOP = 'lzop'
  GZIP = 'gzip'
  COMPRESS = 'compress'
  AUTO = 'auto-compress'    # Use file extension to choose
  NONE = 'no-auto-compress'


def extract_tarball(tarball_file: str, extract_dir: str='.', tbfilter: TarFilter=TarFilter.AUTO):
  """
  Extracts a tarball, optionally filtering through bzip, etc.

  Args:
      tarball_file (str): The filename containing the tarball.
      extract_dir (str, optional): The directory in which to expand the tarball. Defaults to '.'.
      tbfilter (TarFilter, optional):  The compression filter to use. Defaults to TarFilter.AUTO, which
                will choose based on file extension.

  Raises:
      RuntimeError: Any error from the 'tar' command.
  """
  extract_dir = os.path.expanduser(extract_dir)
  tarball_file = os.path.expanduser(tarball_file)

  if tbfilter is None:
    tbfilter = TarFilter.AUTO

  filter_s: str = tbfilter.value

  if not filter_s.startswith('-'):
    filter_s = '--' + filter_s

  with subprocess.Popen(['tar', filter_s, '-xf', tarball_file, '-C', extract_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (_, stderr_bytes) = proc.communicate()
    exit_code = proc.returncode
  if exit_code != 0:
    stderr_s = stderr_bytes.decode('utf-8').rstrip()
    raise RuntimeError(f"Unable to extract tarball \"{tarball_file}\" to \"{extract_dir}\", exit code {exit_code}: {stderr_s}")

def mkdir_p(dirname: str):
  dirname = os.path.expanduser(dirname)
  with subprocess.Popen(['mkdir', '-p', dirname], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (_, stderr_bytes) = proc.communicate()
    exit_code = proc.returncode
  if exit_code != 0:
    stderr_s = stderr_bytes.decode('utf-8').rstrip()
    raise RuntimeError(f"Unable mkdir -p \"{dirname}\", exit code {exit_code}: {stderr_s}")

def download_pulumi(dirname: str, version: Optional[str]=None, stderr: TextIO=sys.stderr):
  dirname = os.path.abspath(os.path.expanduser(dirname))
  with tempfile.TemporaryDirectory() as temp_dir:
    tb_path, tb_url = download_pulumi_tarball(version=version, dirname=temp_dir)
    bin_dir = os.path.join(dirname, 'bin')
    backup_bin_dir = bin_dir + '.bak'
    tmp_install_dir = os.path.join(dirname, 'install.tmp')

    if os.path.exists(tmp_install_dir):
      shutil.rmtree(tmp_install_dir)

    try:
      if not os.path.exists(tmp_install_dir):
        mkdir_p(tmp_install_dir)

      extract_tarball(tb_path, tmp_install_dir)

      tmp_bin_dir = os.path.join(tmp_install_dir, 'pulumi', 'bin')
      if not os.path.exists(tmp_bin_dir):
        tmp_bin_dir = os.path.join(tmp_install_dir, 'pulumi')
        if not os.path.exists(tmp_bin_dir):
          raise RuntimeError(f"Pulumi tarball at {tb_url} does not include pulumi subdirectory")

      if os.path.exists(backup_bin_dir):
        shutil.rmtree(backup_bin_dir)

      success: bool = False
      try:
        if os.path.exists(bin_dir):
          unix_mv(bin_dir, backup_bin_dir)
        unix_mv(tmp_bin_dir, bin_dir)
        success = True
        if os.path.exists(backup_bin_dir):
          shutil.rmtree(backup_bin_dir)
      finally:
        if not success:
          try:
            unix_mv(backup_bin_dir, bin_dir)
          except Exception:
            pass
    finally:
      if os.path.exists(tmp_install_dir):
        try:
          shutil.rmtree(tmp_install_dir)
        except Exception:
          pass

def get_installed_pulumi_dir(dirname: Optional[str]=None) -> Optional[str]:
  result: Optional[str] = None
  if dirname is None:
    dirname = default_pulumi_dir
  dirname = os.path.abspath(os.path.expanduser(dirname))
  if os.path.exists(os.path.join(dirname, 'bin', 'pulumi')):
    result = dirname
  return result

def get_pulumi_prog(dirname: Optional[str]=None) -> Optional[str]:
  result: Optional[str] = None
  dirname = get_installed_pulumi_dir(dirname)
  if not dirname is None:
    result = os.path.join(dirname, 'bin', 'pulumi')
  return result

def require_pulumi_prog(dirname: Optional[str]=None) -> str:
  result: Optional[str] = get_pulumi_prog(dirname)
  if result is None:
    raise ProjectInitError("Unable to locate pulumi executable")
  return result

def pulumi_is_installed(dirname: Optional[str]=None) -> bool:
  return not get_pulumi_prog(dirname) is None

def get_pulumi_version(dirname: Optional[str]=None) -> str:
  version = cast(bytes,
      sudo_check_output_stderr_exception(
          [require_pulumi_prog(dirname), 'version'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  if version.startswith('v'):
    version = version[1:]
  return version

def install_pulumi(
      dirname:Optional[str] = None,
      min_version: Optional[str] = None,
      upgrade_version: Optional[str] = None,
      force: bool = False,
      stderr: TextIO = sys.stderr,
    ) -> Tuple[str, bool]:
  """Installs or upgrades the standard Pulumi CLI

  Args:
      dirname (Optional[str], optional):
                       The directory where Pulumi should be installed. If None, "$HOME/.pulumi"
                      will be used. Defaults to None.
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
                         [0]: The absolute installation directory name; i.e.., PULUMI_HOME
                         [1]: True iff an update/install was done
  """
  if dirname is None:
    dirname = default_pulumi_dir
  if upgrade_version == 'latest':
    upgrade_version = None
  if min_version == 'latest':
    min_version = get_pulumi_latest_version()

  if not upgrade_version is None and not min_version is None and not check_version_ge(upgrade_version, min_version):
    raise RuntimeError("Requested Pulumi upgrade version {upgrade_version} is less than than minimum required version {min_version}")

  dirname = os.path.abspath(os.path.expanduser(dirname))
  old_version: Optional[str] = None
  if pulumi_is_installed(dirname):
    old_version = get_pulumi_version(dirname)
    if force:
      print(f"Forcing upgrade/reinstall of Pulumi version {old_version} in {dirname}", file=sys.stderr)
    else:
      if min_version is None:
        print(f"Pulumi version {old_version} is already installed in {dirname}; no need to reinstall", file=stderr)
        return dirname, False
      if check_version_ge(old_version, min_version):
        print(f"Pulumi version {old_version} is already installed in {dirname} and meets minimum version {min_version}; no need to upgrade", file=stderr)
        return dirname, False
      print(f"Installed Pulumi version {old_version} in {dirname} does not meet minimum version {min_version}; upgrading", file=stderr)
  else:
    print(f"Pulumi not installed in {dirname}; installing", file=stderr)

  if upgrade_version is None:
    upgrade_version = get_pulumi_latest_version()
    if not min_version is None and not check_version_ge(upgrade_version, min_version):
      raise RuntimeError("Requested Pulumi upgrade version {upgrade_version} is less than than minimum required version {min_version}")

  download_pulumi(dirname, upgrade_version, stderr=stderr)
  print(f"Pulumi cli version {upgrade_version} successfully installed in {dirname}.", file=stderr)
  return dirname, True


def get_pulumi_username(dirname: Optional[str]=None, stderr: TextIO=sys.stderr) -> Optional[str]:
  pulumi_cmd = get_pulumi_prog(dirname)
  if pulumi_cmd is None:
    raise ProjectInitError(f"Pulumi is not installed in {dirname}")
  with subprocess.Popen([pulumi_cmd, 'whoami'], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (pulumi_out_bytes, pulumi_err_bytes) = proc.communicate()
    exit_code = proc.returncode
  pulumi_out = pulumi_out_bytes.decode('utf-8').rstrip()
  pulumi_err = pulumi_err_bytes.decode('utf-8').rstrip()
  username: Optional[str] = None
  if pulumi_err != '':
    # error: PULUMI_ACCESS_TOKEN must be set for login during non-interactive CLI sessions
    if not pulumi_err.startswith("error: PULUMI_ACCESS_TOKEN must be set "):
      print(pulumi_err, file=stderr)
      raise ProjectInitError(f"Unexpected stderr output from \"pulumi whoami\", exit_code={exit_code}")
  else:
    if exit_code != 0:
      raise RuntimeError("Unexpected nonzero exit code from \"pulumi whoami\": {exit_code}")
    if pulumi_out == "":
      raise RuntimeError("Unexpected empty username output from \"pulumi whoami\"")

    username = pulumi_out
  return username

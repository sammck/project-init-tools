#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard AWS CLI"""

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
)


MIN_AWS_CLI_VERSION = "2.4.23"

verbose: bool = False

def aws_cli_is_installed() -> bool:
  return command_exists_outside_venv('aws')

def get_aws_cli_prog() -> str:
  result = find_command_in_path_outside_venv('aws')
  if result is None:
    raise FileNotFoundError("aws program is not in PATH")
  return result

def get_aws_cli_long_version() -> str:
  result = cast(bytes,
      sudo_check_output_stderr_exception(
          [get_aws_cli_prog(), '--version'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  return result

def get_aws_cli_version() -> str:
  long_result = get_aws_cli_long_version()
  sp_parts0 = long_result.split(' ', 1)[0]
  sl_parts = sp_parts0.split('/')
  if len(sl_parts) != 2 or sl_parts[0] != 'aws-cli' or sl_parts[1] == '':
    raise ProjectInitError(f"Malformed AWS CLI version string: {long_result}")
  return sl_parts[1]

def install_aws_cli(force: bool=False):
  need_client_install: bool = True
  if aws_cli_is_installed():
    prog = get_aws_cli_prog()
    version = get_aws_cli_version()

    if force:
      print(f"Forcing install/upgrade of AWS CLI from existing version {version}", file=sys.stderr)
    elif check_version_ge(version, MIN_AWS_CLI_VERSION):
      print(f"AWS CLI version {version} is installed and in PATH at {prog}, and", file=sys.stderr)
      print(f"meets the minimum version {MIN_AWS_CLI_VERSION}. No update is necessary.", file=sys.stderr)
      need_client_install = False
    else:
      print(f"AWS CLI version {version} does not meet the minimum version {MIN_AWS_CLI_VERSION}; upgrading", file=sys.stderr)
  else:
    print("AWS CLI is not installed; installing", file=sys.stderr)

  home_local = os.path.expanduser("~/.local")

  if need_client_install:
    arch = get_current_architecture()
    with tempfile.TemporaryDirectory() as tdir:
      zipfile = os.path.join(tdir, "awscliv2.zip")
      download_url_file(f"https://awscli.amazonaws.com/awscli-exe-linux-{arch}.zip", zipfile)
      sudo_check_output_stderr_exception([ 'unzip', '-q', './awscliv2.zip'], cwd=tdir, use_sudo=False)
      os.remove(zipfile)
      if not os.path.isdir(home_local):
        os.mkdir(home_local)
      cmd = [ './aws/install', '-i', os.path.join(home_local, 'aws-cli'), '-b', os.path.join(home_local, 'bin')]
      if aws_cli_is_installed():
        cmd.append('--update')
      sudo_check_call_stderr_exception(cmd, cwd=tdir, use_sudo=False)

    if not aws_cli_is_installed():
      raise ProjectInitError("AWS CLI still not found in PATH after install/upgrade.")

    prog = get_aws_cli_prog()
    version = get_aws_cli_version()

    if not check_version_ge(version, MIN_AWS_CLI_VERSION):
      raise ProjectInitError(
        f"AWS CLI installed/upgraded, but version {version} still does not meet the minimum version {MIN_AWS_CLI_VERSION}")

    print(f"AWS CLI version {version} successfully installed...", file=sys.stderr)

    if prog != os.path.join(home_local, 'bin', 'aws'):
      raise ProjectInitError(f"The AWS CLI in PATH ({prog}) is not the most recently installed.")

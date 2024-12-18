#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard AWS SAM CLI"""

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
from ...os_packages import os_group_add_user, update_gpg_keyring, PackageList, update_apt_sources_list
from ...util import (
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


MIN_aws_sam_cli_VERSION = "1.6.0"

verbose: bool = False

def aws_sam_cli_is_installed() -> bool:
  return command_exists_outside_venv('sam')

def get_aws_sam_cli_prog() -> str:
  result = find_command_in_path_outside_venv('sam')
  if result is None:
    raise FileNotFoundError("aws program is not in PATH")
  return result

def get_aws_sam_cli_long_version() -> str:
  result = cast(bytes,
      sudo_check_output_stderr_exception(
          [get_aws_sam_cli_prog(), '--version'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  return result

def get_aws_sam_cli_version() -> str:
  long_result = get_aws_sam_cli_long_version()
  result = long_result.split(' ')[-1]
  return result

def install_aws_sam_cli(force: bool=False):
  need_client_install: bool = True
  if aws_sam_cli_is_installed():
    prog = get_aws_sam_cli_prog()
    version = get_aws_sam_cli_version()

    if force:
      print(f"Forcing install/upgrade of AWS SAM CLI from existing version {version}", file=sys.stderr)
    elif check_version_ge(version, MIN_aws_sam_cli_VERSION):
      print(f"AWS SAM CLI version {version} is installed and in PATH at {prog}, and", file=sys.stderr)
      print(f"meets the minimum version {MIN_aws_sam_cli_VERSION}. No update is necessary.", file=sys.stderr)
      need_client_install = False
    else:
      print(f"AWS SAM CLI version {version} does not meet the minimum version {MIN_aws_sam_cli_VERSION}; upgrading", file=sys.stderr)
  else:
    print("AWS SAM CLI is not installed; installing", file=sys.stderr)

  home_local = os.path.expanduser("~/.local")

  if need_client_install:
    local_bin_dir = os.path.join(home_local, 'bin')
    prog_symlink = os.path.join(local_bin_dir, 'sam')
    install_dir = os.path.join(home_local, 'aws-sam-cli')
    venv_dir = os.path.join(install_dir, '.venv')
    venv_bin_dir = os.path.join(venv_dir, 'bin')
    venv_pip = os.path.join(venv_bin_dir, 'pip3')
    venv_prog = os.path.join(venv_bin_dir, 'sam')
    rel_venv_prog = os.path.relpath(venv_prog, local_bin_dir)
    # venv_activate = os.path.join(venv_bin_dir, 'activate')
    if os.path.exists(install_dir):
      shutil.rmtree(install_dir)
    os.makedirs(install_dir)
    subprocess.check_call([sys.executable, '-m', 'venv', venv_dir])
    subprocess.check_call([venv_pip, 'install', '--upgrade', 'pip'])
    subprocess.check_call([venv_pip, 'install', 'aws-sam-cli'])

    if os.path.exists(prog_symlink) or os.path.islink(prog_symlink):
      os.remove(prog_symlink)
    os.symlink(rel_venv_prog, prog_symlink)

    if not aws_sam_cli_is_installed():
      raise ProjectInitError("AWS SAM CLI still not found in PATH after install/upgrade.")

    prog = get_aws_sam_cli_prog()
    version = get_aws_sam_cli_version()

    if not check_version_ge(version, MIN_aws_sam_cli_VERSION):
      raise ProjectInitError(
        f"AWS SAM CLI installed/upgraded, but version {version} still does not meet the minimum version {MIN_aws_sam_cli_VERSION}")

    print(f"AWS SAM CLI version {version} successfully installed...", file=sys.stderr)

    if prog != os.path.join(home_local, 'bin', 'sam'):
      raise ProjectInitError(f"The AWS SAM CLI in PATH ({prog}) is not the most recently installed.")

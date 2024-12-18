#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Installer for standard GitHub CLI (gh)"""

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


MIN_GH_VERSION = "2.5.0"

verbose: bool = False

def gh_is_installed() -> bool:
  return command_exists_outside_venv('gh')

def get_gh_prog() -> str:
  result = find_command_in_path_outside_venv('gh')
  if result is None:
    raise FileNotFoundError("GitHub CLI (gh) program is not in PATH")
  return result

def get_gh_version() -> str:
  long_version = cast(bytes,
      sudo_check_output_stderr_exception(
          [get_gh_prog(), '--version'],
          use_sudo=False
        )
    ).decode('utf-8').rstrip()
  line0 = long_version.split('\n', 1)[0].rstrip()
  parts = line0.split(' ')
  if len(parts) < 3 or parts[0] != 'gh' or parts[1] != 'version' or parts[2] == '':
    raise ProjectInitError(f"Malformed gh version response: {line0}")
  return parts[2]

def install_gh(force: bool=False):
  need_client_install: bool = True
  if gh_is_installed():
    prog = get_gh_prog()
    version = get_gh_version()

    if force:
      print(f"Forcing install/upgrade of GitHub CLI from existing version {version}", file=sys.stderr)
    elif check_version_ge(version, MIN_GH_VERSION):
      print(f"GitHub CLI version {version} is installed and in PATH at {prog}, and", file=sys.stderr)
      print(f"meets the minimum version {MIN_GH_VERSION}. No update is necessary.", file=sys.stderr)
      need_client_install = False
    else:
      print(f"GitHub CLI version {version} does not meet the minimum version {MIN_GH_VERSION}; upgrading", file=sys.stderr)
  else:
    print("GitHub CLI is not installed; installing", file=sys.stderr)

  if need_client_install:

    variant = "stable"
    #lsbrelease = get_linux_distro_name()

    update_gpg_keyring(
        "https://cli.github.com/packages/githubcli-archive-keyring.gpg",
        "/etc/apt/trusted.gpg.d/githubcli-archive-keyring.gpg"
      )

    update_apt_sources_list(
        "/etc/apt/sources.list.d/github-cli.list",
        "/etc/apt/trusted.gpg.d/githubcli-archive-keyring.gpg",
        "https://cli.github.com/packages",
        variant,
        "main"
      )

    pl = PackageList()
    if force:
      pl.add_packages( [ "gh" ] )
    else:
      pl.add_package_if_outdated("gh", MIN_GH_VERSION)
    pl.upgrade_all()

    if not gh_is_installed():
      raise ProjectInitError("GitHub CLI (gh) still not found in PATH after install/upgrade.")

    prog = get_gh_prog()
    version = get_gh_version()

    if not check_version_ge(version, MIN_GH_VERSION):
      raise ProjectInitError(
        f"GitHub CLI (gh) installed/upgraded, but version {version} still does not meet the minimum version {MIN_GH_VERSION}")

    print(f"GitHub CLI version {version} successfully installed...", file=sys.stderr)

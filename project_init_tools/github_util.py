#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Simple utilities for interActing with github"""

from typing import cast

import urllib.request
import json
from .internal_types import JsonableDict
from .exceptions import ProjectInitError

def get_github_project_latest_release_info(gh_repo_short_name: str) -> JsonableDict:
  url = f"https://api.github.com/repos/{gh_repo_short_name}/releases/latest"
  with urllib.request.urlopen(url) as resp:
    bin_contents: bytes = resp.read()
  result = cast(JsonableDict, json.loads(bin_contents.decode('utf-8')))
  if not isinstance(result, dict):
    raise ProjectInitError(f"Malformed github release info document: {url}")
  return result

def get_github_project_latest_release_tag(gh_repo_short_name: str) -> str:
  info = get_github_project_latest_release_info(gh_repo_short_name)
  result = cast(str, info['tag_name'])
  if not isinstance(result, str):
    raise ProjectInitError(f"Malformed github release info document: {gh_repo_short_name}")
  return result

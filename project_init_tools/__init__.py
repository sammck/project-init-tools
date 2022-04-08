# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Package project_init_tools provides tools to help with initialization
of git projects, including installing prerequisites, setting up
configuration, enabling build tools, creating a virtualenv, etc.
"""

from .version import __version__

from .internal_types import Jsonable, JsonableDict, JsonableList
from .exceptions import ProjectInitError
from .util import (
    run_once,
    get_tmp_dir,
    hash_pathname,
    full_name_of_type,
    full_type,
    clone_json_data,
    file_url_to_pathname,
    pathname_to_file_url,
    get_git_config_value,
    get_git_root_dir,
    get_git_user_email,
    get_git_user_friendly_name,
    append_lines_to_file_if_missing,
    gen_etc_shadow_password_hash,
    multiline_indent,
    atomic_mv,
  )
from .pyproject_toml import PyprojectToml

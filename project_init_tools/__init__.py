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
from .exceptions import ProjectInitError, CalledProcessErrorWithStderrMessage
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
    deactivate_virtualenv,
    get_aws_identity,
    get_aws_account,
    get_aws_region,
    get_aws_session,
    dedent,
    check_version_ge,
    chown_root, command_exists,
    download_url_file, file_contents, files_are_identical,
    find_command_in_path, get_all_os_groups,
    get_current_architecture, get_current_os_user,
    get_file_hash_hex, get_gid_of_group, get_linux_distro_name,
    get_os_groups_of_current_process, get_os_groups_of_user,
    os_group_exists,
    os_group_includes_current_process, os_group_includes_user,
    running_as_root, searchpath_append,
    searchpath_contains_dir, searchpath_force_append,
    searchpath_join, searchpath_normalize,
    searchpath_parts_append, searchpath_parts_contains_dir,
    searchpath_parts_force_append, searchpath_parts_prepend,
    searchpath_parts_prepend_if_missing,
    searchpath_parts_remove_dir, searchpath_prepend,
    searchpath_prepend_if_missing, searchpath_remove_dir,
    searchpath_split, should_run_with_group, sudo_call,
    sudo_check_call, sudo_check_output,
    sudo_check_output_stderr_exception, sudo_Popen, unix_mv,
  )
from .pyproject_toml import PyprojectToml
from .os_packages import (
    PackageList, create_os_group, get_dpkg_arch, get_os_package_version,
    install_apt_sources_list_if_missing,
    install_gpg_keyring_if_missing, install_os_packages,
    invalidate_os_package_list, os_group_add_user,
    os_package_is_installed, uninstall_os_packages,
    update_and_install_os_packages,
    update_and_upgrade_os_packages,
    update_apt_sources_list, update_gpg_keyring,
    update_os_package_list, upgrade_os_packages,
  )

#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""General utilities for installation"""

from typing import TYPE_CHECKING, Optional, List, Union, TextIO, cast, Callable, Any, Set, Tuple, Generator, overload, Literal

import os
import sys
from packaging import version
import tempfile
import platform
import grp
import hashlib
import filecmp
import urllib3
import shutil
import shlex
import subprocess

import threading
from collections import defaultdict
from functools import lru_cache, _make_key

from ..exceptions import ProjectInitError
from ..util import run_once, get_tmp_dir

if TYPE_CHECKING:
  from subprocess import _CMD, _FILE, _ENV
  from _typeshed import StrOrBytesPath
else:
  _CMD = Any
  _FILE = Any
  _ENV = Any
  StrOrBytesPath = Any

def check_version_ge(version1: str, version2: str) -> bool:
  """returns True iff version1 is greater than or equal to version2

  Args:
      version1 (str): A standard version string
      version2 (str): A standard version string

  Returns:
      bool: True iff version1 is greater than or equal to version2
  """
  return version.parse(version1) >= version.parse(version2)

def searchpath_split(searchpath: Optional[str]=None) -> List[str]:
  if searchpath is None:
    searchpath = os.environ['PATH']
  result = [ x for x in searchpath.split(os.pathsep) if x != '' ]
  return result

def searchpath_join(dirnames: List[str]) -> str:
  return os.pathsep.join(dirnames)

def searchpath_normalize(searchpath: Optional[str]=None) -> str:
  """Removes leading, trailing, and duplicate searchpath seperators from
  a search path string.

  Args:
      searchpath (str): A search path string similar to $PATH

  Returns:
      str: The search path string with extraneous seperators removed
  """
  return searchpath_join(searchpath_split(searchpath))

def searchpath_parts_contains_dir(parts: List[str], dirname: str) -> bool:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  return dirname in parts

def searchpath_contains_dir(searchpath: Optional[str], dirname: str) -> bool:
  return searchpath_parts_contains_dir(searchpath_split(searchpath), dirname)

def searchpath_parts_remove_dir(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [ x for x in parts if x != dirname ]
  return result

def searchpath_remove_dir(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_remove_dir(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [dirname] + searchpath_parts_remove_dir(parts, dirname)
  return result

def searchpath_prepend(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend_if_missing(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = [dirname] + parts
  return result

def searchpath_prepend_if_missing(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend_if_missing(searchpath_split(searchpath), dirname))

def searchpath_parts_force_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = searchpath_parts_remove_dir(parts, dirname) + [dirname]
  return result

def searchpath_force_append(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_force_append(searchpath_split(searchpath), dirname))

def searchpath_parts_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = parts + [dirname]
  return result

def searchpath_append(searchpath: Optional[str], dirname: str) -> str:
  return searchpath_join(searchpath_parts_append(searchpath_split(searchpath), dirname))

def get_current_architecture() -> str:
  return platform.machine()

def get_gid_of_group(group: str) -> int:
  gi = grp.getgrnam(group)
  return gi.gr_gid

def get_file_hash_hex(filename: str) -> str:
  h = hashlib.sha256()
  with open(filename, 'rb') as f:
    while True:
      data = f.read(1024*128)
      if len(data) == 0:
        break
      h.update(data)
  return h.hexdigest()

def files_are_identical(filename1: str, filename2: str, quick: bool=False) -> bool:
  return filecmp.cmp(filename1, filename2, shallow=quick)

def download_url_text(
      url: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
    ) -> str:
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()
  resp = cast(urllib3.HTTPResponse, pool_manager.request('GET', url, preload_content=False))
  return resp.data.decode('utf-8')

def download_url_bytes(
      url: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
    ) -> bytes:
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()
  resp = cast(urllib3.HTTPResponse, pool_manager.request('GET', url, preload_content=False))
  return resp.data

def download_url_file(
      url: str,
      filename: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
      filter_cmd: Optional[Union[str, List[str]]]=None
    ) -> None:
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()

  if not filter_cmd is None and not isinstance(filter_cmd, list):
    filter_cmd = cast(List[str], [ filter_cmd ])
  if filter_cmd is None or len(filter_cmd) == 0 or (len(filter_cmd) == 1 and filter_cmd[0] == 'cat'):
    with open(filename, 'wb') as f:
      resp = pool_manager.request('GET', url, preload_content=False)
      shutil.copyfileobj(resp, f)
  else:
    with tempfile.NamedTemporaryFile(dir=get_tmp_dir()) as f3:
      resp = pool_manager.request('GET', url, preload_content=False)
      shutil.copyfileobj(resp, f3)
      f3.flush()
      # NOTE: following won't work on windows; see https://code.djangoproject.com/wiki/NamedTemporaryFile
      with open(f3.name, 'rb') as f1:
        with open(filename, 'wb') as f2:
          subprocess.check_call(filter_cmd, stdin=f1, stdout=f2)

def running_as_root() -> bool:
  return os.geteuid() == 0

@run_once
def sudo_warn(
      args: _CMD,
      stderr: Optional[_FILE] = None,
      sudo_reason: Optional[str] = None,
    ):
  errout = stderr if isinstance(stderr, TextIO) else sys.stderr
  if sudo_reason is None:
    sudo_reason = f"command: {args!r}"
  print(f"Sudo required: {sudo_reason}", file=errout)

def _sudo_fix_args(
      args: _CMD,
      stderr: Optional[_FILE] = None,
      shell: bool = False,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> _CMD:
  if shell:
    if isinstance(args, list):
      raise RuntimeError(f"Arglist not allowed with shell=True: {args}")
    args = cast(_CMD, [ 'bash', '-c', args ])

  if not isinstance(args, list):
    args = cast(_CMD, [ args ])

  need_group = not run_with_group is None and should_run_with_group(run_with_group)
  is_root = running_as_root()

  if need_group or (use_sudo and not is_root):
    sudo_warn(args, stderr=stderr, sudo_reason=sudo_reason)

    new_args = [ 'sudo' ]
    if need_group:
      new_args.extend( [ '-E', '-u', get_current_os_user()  ] )
    new_args.extend(cast(List[str], args))
    args = new_args
  return args

def sudo_Popen(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      text: Optional[bool] = None,
      encoding: Optional[str] = None,
      errors: Optional[str] = None,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> subprocess.Popen:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.Popen(  # pylint: disable=consider-using-with
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
      text=cast(bool, text),
      encoding=encoding,
      errors=errors,
    )
  return result

def sudo_call(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )


  result = subprocess.call(
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
    )
  return result

def sudo_check_call(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.check_call(
      args,
      bufsize=bufsize,
      executable=cast(StrOrBytesPath, executable),
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
    )
  return result

def sudo_check_output(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      text: Optional[bool] = None,
      encoding: Optional[str] = None,
      errors: Optional[str] = None,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> Union[str, bytes]:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.check_output(   # type: ignore [misc]
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
      text=text,
      encoding=cast(str, encoding),
      errors=errors,
    )
  return result

class CalledProcessErrorWithStderrMessage(subprocess.CalledProcessError):
  def __str__(self):
    return super().__str__() + f": [{self.stderr}]"

@overload
def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = ...,
      executable: Optional[StrOrBytesPath] = ...,
      stdin: Optional[_FILE] = ...,
      stderr: Optional[_FILE] = ...,
      preexec_fn: Optional[Callable[[], Any]] = ...,
      close_fds: bool = ...,
      shell: bool = ...,
      cwd: Optional[StrOrBytesPath] = ...,
      env: Optional[_ENV] = ...,
      universal_newlines: Optional[bool] = ...,
      startupinfo: Any = ...,
      creationflags: int = ...,
      restore_signals: bool = ...,
      start_new_session: bool = ...,
      pass_fds: Any = ...,
      *,
      encoding: Optional[str] = ...,
      errors: Optional[str] = ...,
      use_sudo: bool = ...,
      run_with_group: Optional[str] = ...,
      sudo_reason: Optional[str] = ...,
      text: Literal[True],
    ) -> str:
  ...

@overload
def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = ...,
      executable: Optional[StrOrBytesPath] = ...,
      stdin: Optional[_FILE] = ...,
      stderr: Optional[_FILE] = ...,
      preexec_fn: Optional[Callable[[], Any]] = ...,
      close_fds: bool = ...,
      shell: bool = ...,
      cwd: Optional[StrOrBytesPath] = ...,
      env: Optional[_ENV] = ...,
      universal_newlines: Optional[bool] = ...,
      startupinfo: Any = ...,
      creationflags: int = ...,
      restore_signals: bool = ...,
      start_new_session: bool = ...,
      pass_fds: Any = ...,
      *,
      encoding: Optional[str] = ...,
      errors: Optional[str] = ...,
      use_sudo: bool = ...,
      run_with_group: Optional[str] = ...,
      sudo_reason: Optional[str] = ...,
      text: Literal[False, None] = None,
    ) -> bytes:
  ...

def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      encoding: Optional[str] = None,
      errors: Optional[str] = None,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
      text: Optional[bool] = None,
    ) -> Union[str, bytes]:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  with subprocess.Popen(             # type: ignore [misc]
        args,
        bufsize=bufsize,
        executable=executable,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
        text=text,
        encoding=cast(str, encoding),
        errors=errors,
      ) as proc:
    (stdout_bytes, stderr_bytes) = cast(Tuple[Union[str, bytes], Union[str, bytes]], proc.communicate())
    exit_code = proc.returncode
  if exit_code != 0:
    if encoding is None:
      encoding = 'utf-8'
    stderr_s = stderr_bytes if isinstance(stderr_bytes, str) else stderr_bytes.decode(encoding)
    stderr_s = stderr_s.rstrip()
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr = stderr_s)
  return stdout_bytes

def sudo_check_call_stderr_exception(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      text: Optional[bool] = None,
      encoding: Optional[str] = None,
      errors: Optional[str] = None,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  with subprocess.Popen(             # type: ignore [misc]
        args,
        bufsize=bufsize,
        executable=executable,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
        text=text,
        encoding=cast(str, encoding),
        errors=errors,
      ) as proc:
    (_, stderr_bytes) = cast(Tuple[Union[str, bytes], Union[str, bytes]], proc.communicate())
    exit_code = proc.returncode
  if exit_code != 0:
    if encoding is None:
      encoding = 'utf-8'
    stderr_s = stderr_bytes if isinstance(stderr_bytes, str) else stderr_bytes.decode(encoding)
    stderr_s = stderr_s.rstrip()
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr = stderr_s)
  return exit_code

def chown_root(filename: str, sudo_reason: Optional[str]=None):
  sudo_check_output_stderr_exception(['chown', 'root.root', filename], sudo_reason=sudo_reason)

@run_once
def get_linux_distro_name() -> str:
  result = subprocess.check_output(['lsb_release', '-cs'])
  linux_distro = result.decode('utf-8').rstrip()
  return linux_distro

def file_contents(filename: str) -> str:
  with open(filename, encoding='utf-8') as f:
    result = f.read()
  return result

def pathname_is_executable(pathname: str) -> bool:
  return os.path.isfile(pathname) and os.access(pathname, os.X_OK)

def find_commands_in_path(
      cmd: str,
      searchpath: Optional[str]=None,
      cwd: Optional[str]=None
    ) -> Generator[str, None, None]:
  if cwd is None:
    cwd = '.'
  cwd = os.path.abspath(os.path.expanduser(cwd))
  cmd = os.path.expanduser(cmd)
  if os.path.sep in cmd or (not os.path.altsep is None and os.path.altsep in cmd):
    fq_cmd = os.path.abspath(os.path.join(cwd, cmd))
    if pathname_is_executable(fq_cmd):
      yield fq_cmd
    return
  for path_dir in searchpath_split(searchpath):
    fq_cmd = os.path.abspath(os.path.join(cwd, os.path.expanduser(path_dir), cmd))
    if pathname_is_executable(fq_cmd):
      yield fq_cmd

def get_virtualenv() -> Optional[str]:
  result = sys.prefix
  if result == sys.base_prefix:
    return None
  return result

def pathname_is_in_dir(pathname: str, dirname: str) -> bool:
  pathname = os.path.abspath(os.path.expanduser(pathname))
  dirname = os.path.abspath(os.path.expanduser(dirname))
  rp = os.path.relpath(pathname, dirname)
  first_part = os.path.normpath(rp).split(os.sep)[0]
  return first_part != '..'

def pathname_is_in_venv(pathname: str) -> bool:
  venv_dir = get_virtualenv()
  return not venv_dir is None and pathname_is_in_dir(pathname, venv_dir)

def find_commands_in_path_outside_venv(
      cmd: str,
      searchpath: Optional[str]=None,
      cwd: Optional[str]=None
    ) -> Generator[str, None, None]:
  for fq_cmd in find_commands_in_path(cmd, searchpath=searchpath, cwd=cwd):
    if not pathname_is_in_venv(fq_cmd):
      yield fq_cmd

def find_command_in_path(cmd: str, searchpath: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
  for fq_cmd in find_commands_in_path(cmd, searchpath=searchpath, cwd=cwd):
    return fq_cmd
  return None

def find_command_in_path_outside_venv(cmd: str, searchpath: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
  for fq_cmd in find_commands_in_path_outside_venv(cmd, searchpath=searchpath, cwd=cwd):
    return fq_cmd
  return None

def command_exists(cmd: str) -> bool:
  return not find_command_in_path(cmd) is None

def command_exists_outside_venv(cmd: str) -> bool:
  return not find_command_in_path_outside_venv(cmd) is None

def get_current_os_user() -> str:
  return os.getlogin()

def get_all_os_groups() -> List[str]:
  return sorted(x.gr_name for x in grp.getgrall())

def os_group_exists(group_name: str) -> bool:
  gid: Optional[int] = None
  try:
    groupinfo = grp.getgrnam(group_name)
    gid = groupinfo.gr_gid
  except KeyError:
    pass
  return not gid is None

def get_os_groups_of_user(user: Optional[str]=None) -> List[str]:
  if user is None:
    user = get_current_os_user()
  result: List[str] = []
  for group in grp.getgrall():
    if user in group.gr_mem:
      result.append(group.gr_name)
  return sorted(result)

def get_os_groups_of_current_process() -> List[str]:
  gids = os.getgroups()
  result: List[str] = []
  for group in grp.getgrall():
    if group.gr_gid in gids:
      result.append(group.gr_name)
  return sorted(result)

def os_group_includes_user(group_name: str, user: Optional[str]=None) -> bool:
  groups = get_os_groups_of_user(user=user)
  return group_name in groups

def os_group_includes_current_process(group_name: str) -> bool:
  groups = get_os_groups_of_current_process()
  return group_name in groups

def should_run_with_group(group_name: str, require: bool=True) -> bool:
  if require:
    if not os_group_includes_user(group_name):
      if os_group_exists(group_name):
        raise ProjectInitError(f"User \"{get_current_os_user()}\" is not a member of OS group \"{group_name}\"")
      raise ProjectInitError(f"OS group \"{group_name}\" does not exist")
    result = not os_group_includes_current_process(group_name)
  else:
    result = not os_group_includes_current_process(group_name) and os_group_includes_user(group_name)
  return result

def unix_mv(source: str, dest: str, use_sudo: bool=False, sudo_reason: Optional[str]=None) -> None:
  """
  Equivalent to the linux "mv" commandline.  Atomic within same volume, and overwrites the destination.
  Works for directories.

  Args:
      source (str): Source file or directory.
      dest (str): Destination file or directory. Will be overwritten if it exists.
      sudo (bool): If True, the move will be done as sudo
      sudo_reason (str, optional): Reason why sudo is needed

  Raises:
      RuntimeError: Any error from the mv command
  """
  source = os.path.expanduser(source)
  dest = os.path.expanduser(dest)
  sudo_check_output_stderr_exception(['mv', source, dest], use_sudo=use_sudo, sudo_reason=sudo_reason)


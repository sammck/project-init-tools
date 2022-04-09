# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Miscellaneous utility functions"""

from typing import (
    TYPE_CHECKING,
    Optional,
    List,
    Union,
    TextIO,
    cast,
    Callable,
    Any,
    Set,
    Tuple,
    Generator,
    overload,
    Literal,
    Dict,
    MutableMapping,
    Type
  )

from .exceptions import ProjectInitError, CalledProcessErrorWithStderrMessage
from .internal_types import Jsonable, JsonableDict

import json
import hashlib
import os
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
import pathlib
import subprocess
import threading
import tempfile
import secrets
import boto3
from boto3.session import Session as BotoAwsSession
from botocore.session import Session as BotocoreSession
import sys
from packaging import version
import platform
import grp
import filecmp
import urllib3
import shutil
import shlex
from collections import defaultdict
from functools import lru_cache, _make_key

import yaml

try:
  from yaml import CLoader as YamlLoader, CDumper as YamlDumper
except ImportError:
  from yaml import Loader as YamlLoader, Dumper as YamlDumper  #type: ignore[misc]

# mypy really struggles with this
if TYPE_CHECKING:
  from subprocess import _CMD, _FILE, _ENV
  from _typeshed import StrOrBytesPath
else:
  _CMD = Any
  _FILE = Any
  _ENV = Any
  StrOrBytesPath = Any

class _RunOnceState:
  has_run: bool = False
  result: Any = None
  lock: threading.Lock

  def __init__(self):
    self.lock = threading.Lock()

def run_once(func):
  """Function decorator that caches the result of the first call to a function.

  Useful for creating lazily instanciated singletons. The decorator is
  thread safe--if multiple threads call the function at the same time
  before the first call has returned, they will block waiting for the result.

  Any arguments provided to the function are ignored after the first call.

  Args:
      func (_type_): A function that returns a value to be cached. This
                     function will only be called once.

  Returns:
      _type_: A decorated function that is thread safe and returns the
              value returned from the first call to func.
  """
  state = _RunOnceState()

  def _run_once(*args, **kwargs) -> Any:
    if not state.has_run:
      with state.lock:
        if not state.has_run:
          state.result = func(*args, **kwargs)
          state.has_run = True
    return state.result
  return _run_once

@run_once
def get_tmp_dir() -> str:
  """Returns a temporary directory that is private to this user

  Returns:
      str: A temporary directory that is private to this user
  """
  parent_dir: Optional[str] = os.environ.get("XDG_RUNTIME_DIR")
  if parent_dir is None:
    parent_dir = tempfile.gettempdir()
    tmp_dir = os.path.join(parent_dir, f"user-{os.getuid()}")
  else:
    tmp_dir = os.path.join(parent_dir, 'tmp')
  if not os.path.exists(tmp_dir):
    os.mkdir(tmp_dir, mode=0o700)
  return tmp_dir


def hash_pathname(pathname: str) -> str:
  """Returns an SHA1 hash of a pathname. Used to create fixed-sized
     identifiers without delimeters that will always be the same
     for a given path, but always different for different paths.

  Args:
      pathname (str): A pathname to be hashed. The pathname is
                      canonicalized begore hashing.

  Returns:
      str: a hex-encoded SHA1 hash
  """
  result = hashlib.sha1(os.path.abspath(os.path.expanduser(pathname)).encode("utf-8")).hexdigest()
  return result

def full_name_of_type(t: Type) -> str:
  """Returns the fully qualified name of a python type

  Args:
      t (Type): A python type, which may be a builtin type or a class

  Returns:
      str: The fully qualified name of the type, including the package/module
  """
  module: str = t.__module__
  if module == 'builtins':
    result: str = t.__qualname__
  else:
    result = module + '.' + t.__qualname__
  return result

def full_type(o: Any) -> str:
  """Returns the fully qualified name of an object or value's type

  Args:
      o: any object or value

  Returns:
      str: The fully qualified name of the object or value's type,
           including the package/module
  """
  return full_name_of_type(o.__class__)

def clone_json_data(data: Jsonable) -> Jsonable:
  """Makes a deep copy of a json-serializable value, by serializing and then unserializing.

     Equivalent to deepcopy, but also validates that the data is simple Jsonable data.
     Simple immutable scalar values are directly retuurned without serialization.

  Args:
      data (Jsonable): A JSON-serializable value

  Raises:
      TypeError: If data is not serializable to JSON.

  Returns:
      Jsonable: A deep copy of the provided value, which can be modified without affecting the original.
  """
  if not data is None and not isinstance(data, (str, int, float, bool)):
    data = json.loads(json.dumps(data))
  return data

def file_url_to_pathname(
      url: str,
      cwd: Optional[str]=None,
      allow_relative: bool=True,
      allow_bare_path: bool=True
    ) -> str:
  """Converts a file:// URL to an absolute pathname

  Args:
      url (str):  A URL that begins with "file://", or a bare pathname
                  (allowed if allow_bare_path==True)
      cwd (Optional[str], optional):
                  The directory (absolute or relative to ".") to be used
                  as the base directory for relative pathnames. If None, "."
                  is used. Defaults to None.
      allow_relative (bool, optional):
                  If True, nonstandard "file://<relative-pathname>" URLs will be allowed.
                  Some applications (e.g., Pulumi) use nonstandard (and ambiguous)
                  file:// URIs that allow relative pathnames. They do not support
                  the standard SMB-style "file://<server>/<shared-path>" model.
                  If True, "file://myfile" is interpreted as relative filename "myfile".
                  "file://~/myfile" is interpreted as file "myfile" in the
                  caller's home directory. For sanity we treat "file://localhost/" and
                  "file://127.0.0.1/" as special cases. In any case, "file:///myfile"
                  is properly interpreted as absolute path "/myfile" on the
                  local machine. "Defaults to True.
      allow_bare_path (bool, optional):
                  If True, bare pathnames (either relative or absolute) without a
                  URI scheme (i.e., no "file:" prefix) are accepted. Defaults to True.
  Raises:
      ValueError: An invalid file:// URL was passed

  Returns:
      str: The absolute pathname corresponding to the URL
  """
  if cwd is None:
    cwd = '.'
  url_parts = urlparse(url)
  if allow_bare_path and url_parts.scheme == '':
    url_path = url_parts.path
  else:
    if url_parts.scheme != 'file':
      raise ValueError(f"Not a file:// URL: {url}")
    base_dir = url_unquote(url_parts.netloc)
    if base_dir in [ '', 'localhost', '127.0.0.1' ]:
      base_dir = '/'
    if not allow_relative and base_dir != '/':
      raise ValueError(f"Relative and network-based file:// backends are not allowed: {url}")
    url_path = url_unquote(url_parts.path)
    while url_path.startswith('/'):
      url_path = url_path[1:]
    if url_path == '':
      url_path = base_dir
    elif base_dir.endswith('/'):
      url_path = base_dir + url_path
    else:
      url_path = base_dir + '/' + url_path
  pathname = os.path.abspath(os.path.join(os.path.expanduser(cwd), os.path.expanduser(os.path.normpath(url_path))))
  return pathname

def pathname_to_file_url(pathname: str, cwd: Optional[str]=None) -> str:
  """Converts a pathname for a file:// URL

  Args:
      pathname (str): A local pathname. Will be normalized to an absolute path.
      cwd (Optional[str], optional):
                  The base directory to use for relative pathnames. If None, "."
                  will be used. Defaults to None.

  Returns:
      str: A fully qualified standard "file://" URL.
  """
  if cwd is None:
    cwd = '.'
  pathname = os.path.abspath(os.path.join(os.path.expanduser(cwd), os.path.expanduser(pathname)))
  url = pathlib.Path(pathname).as_uri()
  return url

def get_git_config_value(name: str, cwd: Optional[str]=None) -> str:
  """Gets a configuration value from the local git installation"""
  if cwd is None:
    cwd = '.'
  result = subprocess.check_output(['git', '-C', cwd, 'config', name]).decode('utf-8').rstrip()
  return result

def get_git_user_email(cwd: Optional[str]=None) -> str:
  """Gets the user email address associated with the local git installation"""
  return get_git_config_value('user.email', cwd=cwd)

def get_git_user_friendly_name(cwd: Optional[str]=None) -> str:
  """Gets the friendly name associated with the local git installation"""
  return get_git_config_value('user.name', cwd=cwd)

def get_git_root_dir(starting_dir: Optional[str]=None) -> Optional[str]:
  """Find the root directory of the current git project

  Args:
      starting_dir (str, optional): The subdir in which to begin the search.
                      If None, "." is used. Defaults to None.

  Returns:
      Optional[str]:  The absolute pathname of the top-level git project directory, or
                      None if starting_dir is not in a git project.
  """
  if starting_dir is None:
    starting_dir = '.'
  starting_dir = os.path.abspath(starting_dir)
  rel_root_dir: Optional[str] = None
  try:
    rel_root_dir = subprocess.check_output(
        ['git', '-C', starting_dir, 'rev-parse', '--show-cdup'],
        stderr=subprocess.DEVNULL,
      ).decode('utf-8').rstrip()
  except subprocess.CalledProcessError:
    pass
  result = None if rel_root_dir is None else os.path.abspath(os.path.join(starting_dir, rel_root_dir))
  return result

def append_lines_to_file_if_missing(
    pathname: str,
    lines: Union[str, List[str]],
    create_file: Optional[bool] = False,
    create_mode: int = 0o664,
  ) -> bool:
  """Adds one or more lines to a file, each only if they do not already exist in the file.

  Each line in lines is independently evaluated; lines that are added are added
  in the order they appear in the list.

  Args:
      pathname (str):
                  The pathname of a file to modify
      lines (Union[str, List[str]]):
                  A single line or a list of lines to be added.
      create_file (Optional[bool], optional):
                  If True, the file will be created with the specified
                  mode if it does not exist. Defaults to False.
      create_mode (int, optional):
                  If the file is created, the mode bits to be applied.
                  These bits will be masked by the current umask. Default
                  is 0o664.

  Returns:
      bool: True if the file was created or at least one line was added to the file.
  """
  result: bool = False

  if not isinstance(lines, list):
    lines = [lines]

  if create_file and not os.path.exists(pathname):
    with open(os.open(
          pathname, os.O_CREAT | os.O_WRONLY, create_mode
        ), 'w', encoding='utf-8'):
      pass
    result = True

  if len(lines) > 0:
    adjusted = [x.rstrip("\n\r") for x in lines]
    found = dict((x, False) for x in adjusted)
    with open(pathname, "r+", encoding='utf-8') as f:
      ends_with_newline: bool = True
      for line in f:
        ends_with_newline = line.endswith("\n")
        bline = line.rstrip("\n\r")
        if bline in found:
          found[bline] = True
      for line in adjusted:
        if not found[line]:
          if not ends_with_newline:
            f.write("\n")
            ends_with_newline = True
          f.write(line + "\n")
          result = True
  return result

def multiline_indent(
      s: str,
      n: int,
      trim: bool=True
    ) -> str:
  """Indents all lines in a multiline string

  Args:
      s (str):  A string which may contain '\n' characters
      n (int):  The number of characters to indent each line
      trim (bool, optional):
                If True, whitespace will be removed from the
                end of each line. Defaults to True.

  Returns:
      str: The string with each line indented
  """
  if n <= 0 or s == '':
    return s
  lines = s.split('\n')
  result: List[str] = []
  for line in lines:
    if line != '':
      line = ' '*n + line
      if trim:
        line = line.rstrip()
    result.append(line)
  return '\n'.join(result)

def _detab(s: str, tab_width: int=4, ip: int=0) -> str:
  """Converts tabs to spaces in a potentially multiline string.

  Makes an attempt to be efficient by scanning rather than processing
  one character at a time. If the string has no tabs it is quickly
  returned without modification.

  Args:
      s (str): A potentially multiline string
      tab_width (int, optional): The tab width. Defaults to 4.
      ip (int, optional): The 0-based initial column position. Defaults to 0.

  Returns:
      str: The same string with all tabs converted to spaces.
  """
  result = ""
  next_tab: int = s.find('\t')
  if next_tab < 0:
    # string has no tabs
    return s
  # find the first newline, if any. We have to find
  # all newlines up to the last tab, to reset the
  # character column number.
  next_newline: int = s.find('\n')
  ic = 0  # character index into the string
  while ic < len(s):
    if 0 <= next_newline  < next_tab:
      # There are more tabs, but a newline appears before the next tab
      assert next_newline >= ic
      result += s[ic:next_newline+1]   # take all chars up to the next newline
      ic = next_newline+1
      ip = 0  # reset the column number to 0--start of a new line
      # Find the next newline, if any
      next_newline = s.find('\n', ic)
    else:
      # there is a tab before the next newline
      assert next_tab >= ic
      result += s[ic:next_tab]  # take all chars up to the tab
      ip += next_tab - ic

      ns = tab_width - (ip % tab_width)  # compute number of spaces modulo tab with

      # append the correct number of spaces
      result += ' '*ns
      ip += ns
      ic = next_tab+1

      # find the next tab, if any
      next_tab = s.find('\t', ic)

      # if there are no more tabs, append the remainder of the string and exit
      if next_tab < 0:
        result += s[ic:]
        break
  return result

def dedent(
      s: str,
      min_indent: int=0,
      strip_empty_first_line: bool=True,
      ignore_first_line: bool=True,
      strip_trailing_whitespace: bool=True,
      force_end_with_newline: bool=False,
      tab_width: int=4
    ) -> str:
  """Removes as much indentation from a multiline string as possible without affecting
     relative indentation. In the process, detabs the string (before unindenting).

  Args:
      s (str):    A multiline string to be unindented.
      min_indent (int, optional):
                  The amount of indentation to add back in after removing
                  as much as possible. Defaults to 0.
      strip_empty_first_line (bool, optional):
                  If True, and the first line is empty, remove it. Useful for
                  multiline quoted blocks in code. Defaults to True.
      ignore_first_line (bool, optional):
                  If True, the first line will not be considered for the purposes of
                  determining how much indentation to remove. Useful for
                  multiline quoted blocks in code. Defaults to True.
      strip_trailing_whitespace (bool, optional):
                  If True, trailing whitespace on each line will be removed. Defaults to True.
      force_end_with_newline (bool, optional):
                  If True, the result will always end with a newline unless it is the empty string.
                  Defaults to False.
      tab_width (int, optional):
                  The tab width, for detabbing. Defaults to 4.

  Returns:
      str: A detabbed and unindented string
  """

  if s == '':
    return s
  s = _detab(s, tab_width=tab_width)

  lines = s.split('\n')
  if strip_empty_first_line and (lines[0] == '' or strip_trailing_whitespace and lines[0].rstrip() == ''):
    lines = lines[1:]
    ignore_first_line = False

  if len(lines) == 0:
    return ''

  min_existing_indent: Optional[int] = None
  bare_lines: List[Tuple[Optional[int], str]] = []
  for i, line in enumerate(lines):
    rstrip_line = line.rstrip()
    is_whitespace_line = rstrip_line == ''
    if strip_trailing_whitespace:
      line = rstrip_line
    line_tail = line if is_whitespace_line else line.lstrip()
    existing_indent = None if is_whitespace_line else len(line)-len(line_tail)
    if not existing_indent is None and (i > 0 or not ignore_first_line) and (
          min_existing_indent is None or existing_indent < min_existing_indent):
      min_existing_indent = existing_indent
    bare_lines.append((existing_indent, line_tail))
  if min_existing_indent is None:
    min_existing_indent = 0

  for i, bare_line in enumerate(bare_lines):
    existing_indent, line = bare_line
    if line != '':
      if existing_indent is None:
        existing_indent = len(line)
      ns = max(0, existing_indent - min_existing_indent)
      line = ' '*ns + line
      lines[i] = line
    lines[i] = line

  if force_end_with_newline and lines[-1] != '':
    lines.append('')

  return '\n'.join(lines)

def gen_etc_shadow_password_hash(password: str) -> str:
  """Generates a unique, salted SHA512 password hash for /etc/shadow.

  The resulting string is suitable for direct insertion into /etc/shadow.
  This is mostly useful for remotely initializing user account passwords
  without sending passwords in the clear (e.g., when setting up a VM).

  Args:
      password (str): A cleartext password

  Returns:
      str: A salted, SHA-512 hash of the password expressed as a string
           compatible with /etc/shadow.
  """
  salt = secrets.token_urlsafe(16)
  result = subprocess.check_output(['openssl', 'passwd', '-6', '-salt', salt, password]).decode('utf-8').rstrip()
  return result

def atomic_mv(source: str, dest: str) -> None:
  """
  Equivalent to the linux "mv" commandline.  Atomic within same volume, and overwrites the destination.
  Works for directories.

  Args:
      source (str): Source file or directory.x
      dest (str): Destination file or directory. Will be overwritten if it exists.

  Raises:
      RuntimeError: Any error from the mv command
  """
  source = os.path.expanduser(source)
  dest = os.path.expanduser(dest)
  subprocess.check_call(['mv', source, dest])

def deactivate_virtualenv(env: Optional[MutableMapping]=None):
  """Modifies env vars to deactivate any activated virtualenv.

     Works on the current os environment or a dict/MutableMapping.

  Args:
      env (Optional[MutableMapping], optional):
              The environment to modify. If None, modifies the current
              os.environ.  Defaults to None.
  """
  if env is None:
    env = os.environ
  if 'VIRTUAL_ENV' in env:
    venv = env['VIRTUAL_ENV']
    del env['VIRTUAL_ENV']
    if 'POETRY_ACTIVE' in env:
      del env['POETRY_ACTIVE']
    if 'PATH' in env:
      venv_bin = os.path.join(venv, 'bin')
      env['PATH'] = searchpath_remove_dir(env['PATH'], venv_bin)

def get_aws_session(s: Optional[BotoAwsSession]=None) -> BotoAwsSession:
  if s is None:
    s = BotoAwsSession()
  return s

def get_aws_identity(s: Optional[BotoAwsSession]=None) -> Dict[str, str]:
  """Fetches AWS identity including the account number associated with an AWS session.

  The first time it is done for a session, requires a network request to AWS.
  After that, the result is cached on the session object.

  Args:
      s (BotoAwsSession): The AWS session in question, or None to create a default session.
                          Defaults to None.

  Returns:
      A dictionary with:
         ['Arn']  the AWS user's Arn
         ['Account'] The AWS account number
         ['UserId'] The user's AWS user ID
  """
  s = get_aws_session(s)
  result: Dict[str, str]
  if hasattr(s, "_xpulumi_caller_identity"):
    result = s._xpulumi_caller_identity  # type: ignore[attr-defined] # pylint: disable=protected-access
  else:
    sts = s.client('sts')
    result = sts.get_caller_identity()
    # cache the result on the session object
    s._xpulumi_caller_identity = result  # type: ignore[attr-defined] # pylint: disable=protected-access
  return result

def get_aws_account(s: Optional[BotoAwsSession]=None) -> str:
  return get_aws_identity(s)['Account']

def get_aws_region(s: Optional[BotoAwsSession]=None, default: Optional[str]=None) -> Optional[str]:
  s = get_aws_session(s)
  result: Optional[str] = s.region_name
  if result is None:
    result = default

  return result

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

if TYPE_CHECKING:
  _exported_keep = [ yaml, YamlLoader, YamlDumper]

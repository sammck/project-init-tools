# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Miscellaneous utility functions"""

import re
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
import string
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


def get_optional_git_config_value(name: str, cwd: Optional[str]=None) -> Optional[str]:
  """Gets a configuration value from the local git installation"""
  if cwd is None:
    cwd = '.'
  try:
    result = sudo_check_output_stderr_exception(
        ['git', '-C', cwd, 'config', name],
        use_sudo=False,
      ).decode('utf-8').rstrip()
  except CalledProcessErrorWithStderrMessage as e:
    if e.returncode == 1 and (e.stderr is None or len(e.stderr) == 0):
      result = None
    else:
      raise
  return result

def set_git_config_value(name: str, value: str, cwd: Optional[str]=None, is_global: bool=False) -> None:
  """Sets a configuration value in the local git installation"""
  if cwd is None:
    cwd = '.'
  cmd = ['git', '-C', cwd, 'config']
  if is_global:
    cmd.append('--global')
  cmd.extend([name, value])

  sudo_check_output_stderr_exception(
        cmd,
        use_sudo=False,
      )

def get_git_config_value(name: str, cwd: Optional[str]=None) -> str:
  """Gets a configuration value from the local git installation"""
  result = get_optional_git_config_value(name, cwd=cwd)
  if result is None:
    raise KeyError(f"git config value '{name}' does not exist")
  return result

def get_git_user_email(cwd: Optional[str]=None) -> str:
  """Gets the user email address associated with the local git installation"""
  return get_git_config_value('user.email', cwd=cwd)

def get_git_user_friendly_name(cwd: Optional[str]=None) -> str:
  """Gets the friendly name associated with the local git installation"""
  return get_git_config_value('user.name', cwd=cwd)

def set_git_user_email(value: str, cwd: Optional[str]=None, is_global: bool=True) -> None:
  """Sets the user email address associated with the local git installation"""
  set_git_config_value('user.email', value, cwd=cwd, is_global=is_global)

def set_git_user_friendly_name(value: str, cwd: Optional[str]=None, is_global: bool=True) -> None:
  """Sets the friendly name associated with the local git installation"""
  set_git_config_value('user.name', value, cwd=cwd, is_global=is_global)

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

def gen_etc_shadow_password_salt(num_chars: int=16) -> str:
  # linux mkpasswd only accepts salt with chars in [a-zA-Z0-9/.].
  # openssl passwd is too lax, and it will allow '-' and '_' in the salt.
  # As a result there is a chance user could never log in unless
  # we constrain it as above.
  # secrets.token_urlsafe returns chars in [a-zA-Z0-9_\-]
  if not 8 <= num_chars <= 16:
    raise ValueError(f"Invalid /etc/shadow password salt length: {num_chars}")
  # token_urlsafe returns a base64 encoding of num_chars binary bytes. the
  # string will be longer than num_chars but we can truncate
  salt = secrets.token_urlsafe(num_chars)[:num_chars].replace('-', '/').replace('_', '.')
  return salt

_valid_shadow_password_chars = set(string.ascii_lowercase + string.ascii_uppercase + string.digits + '/.')
def is_valid_etc_shadow_password_salt(salt: str) -> bool:
  # linux mkpasswd only accepts salt with chars in [a-zA-Z0-9/.],
  # and the salt must be 8-12 chars in length
  return 8 <= len(salt) <= 16 and all(c in _valid_shadow_password_chars for c in salt)

def gen_etc_shadow_password_hash(password: str, salt: Optional[str]=None, num_chars: int=16) -> str:
  """Generates a unique, salted SHA512 password hash for /etc/shadow.

  The resulting string is suitable for direct insertion into /etc/shadow.
  This is mostly useful for remotely initializing user account passwords
  without sending passwords in the clear (e.g., when setting up a VM).

  Args:
      password (str): A cleartext password
      salt: (str, optional): A known salt string. Must only contain characters in
           [a-zA-Z0-9/.], and be from 8 to 16 characters in length. 16 is preferred.
           If omitted, a 16-character random salt is generated; it will be in
           returnval[3:19].

  Returns:
      str: A salted, SHA-512 hash of the password expressed as a string
           compatible with /etc/shadow.
  """
  if salt is None:
    salt = gen_etc_shadow_password_salt(num_chars)

  if not is_valid_etc_shadow_password_salt(salt):
    raise ValueError(f"Invalid /etc/shadow password salt string: '{salt}'")

  # use openssl rather than mkpasswd because the latter is not installed in base os
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
  """Fetches the AWS account number associated with an AWS session.

  Args:
      s (BotoAwsSession): The AWS session in question, or None to create a default session.
                          Defaults to None.
  """
  return get_aws_identity(s)['Account']

def get_aws_region(s: Optional[BotoAwsSession]=None, default: Optional[str]=None) -> Optional[str]:
  """Fetches the AWS region associated with an AWS session.

  Args:
      s (BotoAwsSession): The AWS session in question, or None to create a default session.
                          Defaults to None.
      default (Optional[str], optional):
                          The default region to use if the session does not have a region.
  """
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
  """Splits a ':'-delimited search path string into a list of directories

  Omits empty directory names due to extraneous colons.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH']. Defaults to None.

  Returns:
      List[str]: A list of directory names, with no empty strings
  """
  if searchpath is None:
    searchpath = os.environ['PATH']
  result = [ x for x in searchpath.split(os.pathsep) if x != '' ]
  return result

def searchpath_join(dirnames: List[str]) -> str:
  """Joins a list of directories into a ':'-delimited search path string"""
  return os.pathsep.join(dirnames)

def searchpath_normalize(searchpath: Optional[str]=None) -> str:
  """Removes leading, trailing, and duplicate searchpath seperators from
  a search path string.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH']. Defaults to None.

  Returns:
      str: The search path string with extraneous seperators removed
  """
  return searchpath_join(searchpath_split(searchpath))

def searchpath_parts_contains_dir(parts: List[str], dirname: str) -> bool:
  """Returns True if a direcory name is in a list of directories.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      bool: True if the directory name after normalization is in the list of directories
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  return dirname in parts

def searchpath_contains_dir(searchpath: Optional[str], dirname: str) -> bool:
  """Returns True if a direcory name is in a ':'-delimited search path.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant directories in the search path are also normalized to
  absolute paths.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      bool: True if the directory name after normalization is in the list of directories
  """
  return searchpath_parts_contains_dir(searchpath_split(searchpath), dirname)

def searchpath_parts_remove_dir(parts: List[str], dirname: str) -> List[str]:
  """Removes a directory name from a list of directories.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  Has no effect if the directory name is not in the list of directories.
  If the directory name is present multiple times, all instances are removed.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      List[str]: A list of directory names, with the specified directory removed
                 if it was present in the original list.
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [ x for x in parts if x != dirname ]
  return result

def searchpath_remove_dir(searchpath: Optional[str], dirname: str) -> str:
  """Removes a directory name from a ':'-delimited search path.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant directories in the search path are
  also normalized to absolute paths.

  Has no effect if the directory name is not in the search path.
  If the directory name is present multiple times, all instances are removed.

  The resulting search path will have extraneous ':' delimeters removed.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      str: The resulting search path, with the specified directory removed
                 if it was present in the original list. Extraneous ':' delimeters
                 are removed.
  """
  return searchpath_join(searchpath_parts_remove_dir(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend(parts: List[str], dirname: str) -> List[str]:
  """Prepends a directory name to a list of directories.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the list of directories, all instances are removed
  before prepending the directory name. This has the effect of moving the directory name
  to the front of the list. Hence, this function always results in the directory name
  being the first element of the list, which is consistent with expectations in
  search paths.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      List[str]: A list of directory names, with the normalized directory name appearing
                 exactly once at the beginning of the list.
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [dirname] + searchpath_parts_remove_dir(parts, dirname)
  return result

def searchpath_prepend(searchpath: Optional[str], dirname: str) -> str:
  """Prepends a directory name to a ':'-delimited search path.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the search path, all instances are removed
  before prepending the directory name. This has the effect of moving the directory name
  to the front of the search path. Hence, this function always results in the directory name
  being the first directory searched.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      str: The resulting search path, with the normalized directory name appearing
           exactly once at the beginning of the search path.
  """
  return searchpath_join(searchpath_parts_prepend(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend_if_missing(parts: List[str], dirname: str) -> List[str]:
  """Prepends a directory name to a list of directories if it is not already in the list.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the list of directories, does nothing.
  Otherwise, creates a new list with the normalized directory name at the beginning.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      List[str]: A list of directory names, with the normalized directory name appearing
                 at least once, and at the beginning of the list if it was added.
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = [dirname] + parts
  return result

def searchpath_prepend_if_missing(searchpath: Optional[str], dirname: str) -> str:
  """Prepends a directory name to a ':'-delimited search path if it is not already in the search path.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the search path, does nothing.
  Otherwise, creates a new search path with the normalized directory name at the beginning.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      str: The resulting search path, with the normalized directory name appearing
           at least once, and at the beginning of the search path if it was added.
  """
  return searchpath_join(searchpath_parts_prepend_if_missing(searchpath_split(searchpath), dirname))

def searchpath_parts_force_append(parts: List[str], dirname: str) -> List[str]:
  """Appends a directory name to a list of directories or forces it to the end if it is already in the list.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the list of directories, all instances are removed
  before appending the directory name. This has the effect of moving the directory name
  to the end of the list. Hence, this function always results in the directory name
  being the last element of the list.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      List[str]: A list of directory names, with the normalized directory name appearing
                 exactly once at the end of the list.
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = searchpath_parts_remove_dir(parts, dirname) + [dirname]
  return result

def searchpath_force_append(searchpath: Optional[str], dirname: str) -> str:
  """Appends a directory name to a ':'-delimited search path, or forces it to the
     end if it is already in the search path.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the search path, all instances are removed
  before appending the directory name. This has the effect of moving the directory name
  to the end of the search path. Hence, this function always results in the directory name
  being the last directory searched.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      str: The resulting search path, with the normalized directory name appearing
           exactly once at the end of the search path.
  """
  return searchpath_join(searchpath_parts_force_append(searchpath_split(searchpath), dirname))

def searchpath_parts_append(parts: List[str], dirname: str) -> List[str]:
  """Appends a directory name to a list of directories if it is not already present.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the list of directories, does nothing.
  Otherwise, creates a new list with the directory name added to the end.
  This has the effect of ensuring the directory will be searched, but never lowering
  its search priority from an existing position, which is consistent with expectations
  for search paths.

  Args:
      parts (List[str]): A list of directory names, normalized to absolute paths
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      List[str]: A list of directory names, with the normalized directory name appearing
                 at least once, and at the end of the list if added.
  """
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = parts + [dirname]
  return result

def searchpath_append(searchpath: Optional[str], dirname: str) -> str:
  """Appends a directory name to a ':'-delimited search path if it is not already present.

  The directory name is normalized to an absolute path before comparison;
  the assumption is that the relevant list of directories is also normalized to contain
  absolute paths.

  If the directory name is already in the search path, does nothing.
  Otherwise, creates a new search path with the normalized directory on the end.
  This has the effect of ensuring the directory will be searched, but never lowering
  its search priority from an existing position, which is consistent with expectations
  for search paths.

  Args:
      searchpath (str): A ':'-delimited search path string, or None
                        to use os.environ['PATH'].
      dirname (str): A directory name, which will be normalized to an absolute path

  Returns:
      str: The resulting search path, with the normalized directory name appearing
           at least once, and at the end of the search path if added.
  """
  return searchpath_join(searchpath_parts_append(searchpath_split(searchpath), dirname))

def get_current_architecture() -> str:
  """Returns current hardware architecture; e.g., aarch64 or x86_64"""
  return platform.machine()

def get_current_system() -> str:
  """Returns current software platform; e.g., Linux or Darwin"""
  return platform.system()

def get_gid_of_group(group: str) -> int:
  """Returns the GID of a group name"""
  gi = grp.getgrnam(group)
  return gi.gr_gid

def get_group_of_gid(gid: int) -> str:
  """Returns the name of a group given its GID"""
  gi = grp.getgrgid(gid)
  return gi.gr_name

def gid_exists(gid: int) -> bool:
  """Returns True if a group with the specified GID exists"""
  result = False
  try:
    get_group_of_gid(gid)
    result = True
  except KeyError:
    pass
  return result

def get_file_hash_hex(filename: str) -> str:
  """Returns the SHA256 hash of a file as a hex string"""
  h = hashlib.sha256()
  with open(filename, 'rb') as f:
    while True:
      data = f.read(1024*128)
      if len(data) == 0:
        break
      h.update(data)
  return h.hexdigest()

def files_are_identical(filename1: str, filename2: str, quick: bool=False) -> bool:
  """Returns True if two files are identical"""
  return filecmp.cmp(filename1, filename2, shallow=quick)

def download_url_text(
      url: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
    ) -> str:
  """Returns the content of a text document at an URL as a string"""
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()
  resp = cast(urllib3.HTTPResponse, pool_manager.request('GET', url, preload_content=False))
  return resp.data.decode('utf-8')

def download_url_bytes(
      url: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
    ) -> bytes:
  """Returns the content of a binary document at an URL as a bytes object"""
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()
  resp = cast(urllib3.HTTPResponse, pool_manager.request('GET', url, preload_content=False))
  return resp.data

def download_url_file(
      url: str,
      filename: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
      filter_cmd: Optional[Union[str, List[str]]]=None,
      mode: Optional[int] = None,
      uid: Optional[int] = None,
      gid: Optional[int] = None,
    ) -> None:
  """Downloads a file from an URL to a local file.

  sudo is not used; the file is written with the current user's permissions.

  Args:
      url (str): The URL to download from
      filename (str): The local filename to download to
      pool_manager (Optional[urllib3.PoolManager], optional):
              An optional urllib3 PoolManager to use for the download.
              Defaults to None, in which case a default PoolManager is used.
      filter_cmd (Optional[Union[str, List[str]]], optional):
              An optional command to pipe the downloaded file through before
              writing it to disk. Defaults to None, in which case the file is
              written directly to disk.
      mode (Optional[int], optional):
              Optional file mode bits (see chmod) to use when creating the local file. Defaults to None,
              in which case the default mode bits are used.
      uid (Optional[int], optional):
              Optional user ID (see chown) to use when creating the local file. Defaults to None,
              in which case the default user ID is used.
      gid (Optional[int], optional):
              Optional group ID (see chown) to use when creating the local file. Defaults to None,
              in which case the default group ID is used.
  """
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()

  if not filter_cmd is None and not isinstance(filter_cmd, list):
    filter_cmd = cast(List[str], [ filter_cmd ])
  resp = pool_manager.request('GET', url, preload_content=False)
  if filter_cmd is None or len(filter_cmd) == 0 or (len(filter_cmd) == 1 and filter_cmd[0] == 'cat'):
    if mode is None:
      with open(filename, 'wb') as f:
        shutil.copyfileobj(resp, f)
    else:
      with open(
            os.open(filename, os.O_CREAT | os.O_WRONLY, mode),
            'wb',
          ) as f:
        shutil.copyfileobj(resp, f)
  else:
    with tempfile.NamedTemporaryFile(dir=get_tmp_dir()) as f3:
      # NOTE: permissions on NamedTemporaryFile are 0o600 so we don't need to worry
      #       about mode bits on the temp file
      shutil.copyfileobj(resp, f3)
      f3.flush()
      # NOTE: following won't work on windows; see https://code.djangoproject.com/wiki/NamedTemporaryFile
      with open(f3.name, 'rb') as f1:
        if mode is None:
          with open(filename, 'wb') as f2:
            subprocess.check_call(filter_cmd, stdin=f1, stdout=f2)
        else:
          with open(
                os.open(filename, os.O_CREAT | os.O_WRONLY, mode),
                'wb',
              ) as f2:
            subprocess.check_call(filter_cmd, stdin=f1, stdout=f2)
  if not uid is None or not gid is None:
    if uid is None or gid is None:
      st = os.stat(filename)
      if uid is None:
        uid = st.st_uid
      if gid is None:
        gid = st.st_gid
    os.chown(filename, uid, gid)


def running_as_root() -> bool:
  return os.geteuid() == 0

@run_once
def sudo_warn(
      args: _CMD,
      stderr: Optional[_FILE] = None,
      sudo_reason: Optional[str] = None,
    ):
  """Prints a warning message that sudo is required the first time it is needed.

  Does nothing if not the first time called.
  """
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
  """Modifies a command intended to be run with subprocess.Popen to use sudo if needed.

  Args:
      args (_CMD):
          The command to run, as a list of strings or a string.
      stderr (Optional[_FILE], optional):
          The destination for stderr output, or None to use sys.stderr. Defaults to None.
      shell (bool, optional):
          True if command is a shell string rather than raw args. Defaults to False.
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

  Returns:
      _CMD: The modified command suitable for subprocess.POpen, as a list of strings or a string.
  """
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
  """Run subprocess.Popen with sudo if needed.

  Args:
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.Popen for details.
      """
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
  """Run subprocess.call with sudo if needed.

  Args:
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.call for details.
      """
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
  """Run subprocess.check_call with sudo if needed.

  Args:
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.check_call for details.
      """
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
  """Run subprocess.check_output with sudo if needed.

  Args:
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.check_output for details.
      """
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
  """Run subprocess.check_output with sudo if needed, capturing stderr output in the error detail.

  If an error occurs, the captured stderr output is included in the exception message.

  Args:
      stderr (Optional[_FILE], optional):
          Ignored.  Included only for compatibility with subprocess.check_output.
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.check_output for details.

      Raises:
          CalledProcessErrorWithStderrMessage:
              A subclass of CalledProcessError. If the command fails, this exception is raised.
              The exception includes the captured stderr output in the exception message.
      """
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
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr=stderr_s, output=stdout_bytes)
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
  """Run subprocess.check_call with sudo if needed, capturing stderr output in the error detail.

  If an error occurs, the captured stderr output is included in the exception message.

  Args:
      stderr (Optional[_FILE], optional):
          Ignored.  Included only for compatibility with subprocess.check_output.
      use_sudo (bool, optional):
          True if sudo is required. Defaults to True.
      run_with_group (Optional[str], optional):
          An optional group name that this user needs to be in to run the command.
          Useful for situations in which the user has been added to a group but the
          login session needs to be restarted before the changes will be visible. If
          this is the case, this function will modify the command to use "sudo -E -u <username>"
          to run the command in a new login session. May cause sudo to be used even if
          use_sudo is False. Defaults to None.
      sudo_reason (Optional[str], optional):
          If use_sudo is True, a description of why sudo is required. If this is
          the first time sudo is required, a message will be displayed to the user
          understands why they need to type in their sudo password. Defaults to None.

      <other args>:
          See subprocess.check_call for details.

      Raises:
          CalledProcessErrorWithStderrMessage:
              A subclass of CalledProcessError. If the command fails, this exception is raised.
              The exception includes the captured stderr output in the exception message.
      """
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
  """Returns the current Linux distribution name, e.g. 'jammy'."""
  result = subprocess.check_output(['lsb_release', '-cs'])
  linux_distro = result.decode('utf-8').rstrip()
  return linux_distro

def file_contents(filename: str) -> str:
  """Returns the contents of a text file as a string."""
  with open(filename, encoding='utf-8') as f:
    result = f.read()
  return result

def pathname_is_executable(pathname: str) -> bool:
  """Returns True if pathname is an existing file that is executable by the current user."""
  return os.path.isfile(pathname) and os.access(pathname, os.X_OK)

def find_commands_in_path(
      cmd: str,
      searchpath: Optional[str]=None,
      cwd: Optional[str]=None
    ) -> Generator[str, None, None]:
  """Searches for all occurences of an executable command in the search path.

  Identical to the way the shell searches for non-builtin commands:

     1. If cmd contains a path separator ('/'), it is simply checked for existence
        and executability.
     2. Otherwise, each directory in the search path is checked for
        existence of a file named cmd. If the file is found and is
        executable, the full path to the executable is yielded.

  There may be multiple matches. An ordered sequence of matching
  absolute pathnames is generated. The first yielded path is the
  first matching executable found in the search path, which is the
  one the shell would use.

  If no matching executable is found, no paths are yielded.


  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Yields:
      str: An absolute path to a matching executable
  """
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
  """Returns the path to the current Python virtualenv, or None if not in a virtualenv."""
  result = sys.prefix
  if result == sys.base_prefix:
    return None
  return result

def pathname_is_in_dir(pathname: str, dirname: str) -> bool:
  """Returns True if a pathname refers to a directory or anything under the directory.

  Both pathname and dirname are normalized to absolute paths before comparison.

  Neither the pathname nor the directory referred to by the arguments need exist. Only
  their pathnames are compared.

  Args:
      pathname (str): A relative or absolute pathname to check for inclusion in a directory.
      dirname (str): A relative or absolute pathname to a directory.

  Returns:
      bool: True if pathname is equal to dirname, or is under dirname.
  """
  pathname = os.path.abspath(os.path.expanduser(pathname))
  dirname = os.path.abspath(os.path.expanduser(dirname))
  rp = os.path.relpath(pathname, dirname)
  first_part = os.path.normpath(rp).split(os.sep)[0]
  return first_part != '..'

def pathname_is_in_venv(pathname: str) -> bool:
  """Returns True if a pathname refers to the current virtualenv or anything it.

  If not in a virtualenv, returns False.
  pathname is normalized to an absolute path before comparison.
  pathname need not exist. Only the path string is considered.

  Args:
      pathname (str): A relative or absolute pathname to check for inclusion in the virtualenv.

  Returns:
      bool: True if currently running in a virtualenv and pathname is equal to the virtualenv,
            or is under the virtualenv.
  """
  venv_dir = get_virtualenv()
  return not venv_dir is None and pathname_is_in_dir(pathname, venv_dir)

def find_commands_in_path_outside_venv(
      cmd: str,
      searchpath: Optional[str]=None,
      cwd: Optional[str]=None
    ) -> Generator[str, None, None]:
  """Searches for an executable command in the search path, excluding commands in the current virtualenv.

  Identical to the way the shell searches for non-builtin commands, except
  that the current virtualenv is never searched:

     1. If cmd contains a path separator ('/'), it is simply checked for existence
        and executability and being outside the virtualenv.
     2. Otherwise, each directory in the search path is checked for
        existence of a file named cmd. If the file is found and is
        executable, and is outside the virtualenv, the full path to the executable is yielded.

  If not currently running in a virtualenv, this function is identical to find_commands_in_path.

  There may be multiple matches. An ordered sequence of matching
  absolute pathnames is generated. The first yielded path is the
  first matching executable found in the search path, which is the
  one the shell would use.

  If no matching executable is found, no paths are yielded.

  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Yields:
      str: An absolute path to a matching executable that is not in the current virtualenv
  """
  for fq_cmd in find_commands_in_path(cmd, searchpath=searchpath, cwd=cwd):
    if not pathname_is_in_venv(fq_cmd):
      yield fq_cmd

def find_command_in_path(cmd: str, searchpath: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
  """Searches for the first occurence of an executable command in the search path.

  Identical to the way the shell searches for non-builtin commands:

     1. If cmd contains a path separator ('/'), it is simply checked for existence
        and executability and returned as an absolute path.
     2. Otherwise, each directory in the search path is checked in order for
        existence of a file named cmd. If the file is found and is
        executable, the search is ended and the full path to the executable is returned.

  If no matching executable is found, None is returned.

  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Returns:
      str: An absolute path to a matching executable
  """
  for fq_cmd in find_commands_in_path(cmd, searchpath=searchpath, cwd=cwd):
    return fq_cmd
  return None

def find_command_in_path_outside_venv(cmd: str, searchpath: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
  """Searches for the first occurence of an executable command in the search path, excluding commands in the current virtualenv.

  Identical to the way the shell searches for non-builtin commands, except
  that the current virtualenv is never searched:

     1. If cmd contains a path separator ('/'), it is simply checked for existence
        and executability and being outside the virtualenv, and returned as an absolute path.
     2. Otherwise, each directory in the search path is checked in order for
        existence of a file named cmd. If the file is found and is
        executable, and is outside the virtualenv, the search is ended and
        the full path to the executable is returned.

  If not currently running in a virtualenv, this function is identical to find_command_in_path.

  If no matching executable is found, None is returned.

  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Yields:
      str: An absolute path to a matching executable that is not in the current virtualenv
  """
  for fq_cmd in find_commands_in_path_outside_venv(cmd, searchpath=searchpath, cwd=cwd):
    return fq_cmd
  return None

def command_exists(cmd: str, searchpath: Optional[str]=None, cwd: Optional[str]=None) -> bool:
  """Returns True if the command exists in the search path.

  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Returns:
      bool: True if the command exists in the search path.
 """
  return not find_command_in_path(cmd, searchpath=searchpath, cwd=cwd) is None

def command_exists_outside_venv(cmd: str) -> bool:
  """Returns True if the command exists in the search path, excluding the current virtualenv.

  If not currently running in a virtualenv, this function is identical to command_exists.

  Args:
      cmd (str):
          The name of the command to search for.
      searchpath (str):
          A ':'-delimited search path string, or None
          to use os.environ['PATH']. Defaults to None.
      cwd (Optional[str], optional):
          The working directory from which to resolve relative pathnames, or
          None to use the current working directory. Defaults to None.

  Returns:
      bool: True if the command exists in the search path, excluding the current virtualenv.
 """
  return not find_command_in_path_outside_venv(cmd) is None

def get_current_os_user() -> str:
  """Get the current OS user name."""
  return os.getlogin()

def get_all_os_groups() -> List[str]:
  """Get a list of all OS group names."""
  return sorted(x.gr_name for x in grp.getgrall())

def os_group_exists(group_name: str) -> bool:
  """Returns True if the named OS group exists."""
  gid: Optional[int] = None
  try:
    groupinfo = grp.getgrnam(group_name)
    gid = groupinfo.gr_gid
  except KeyError:
    pass
  return not gid is None

def get_os_groups_of_user(user: Optional[str]=None) -> List[str]:
  """Returns a list of OS group names for which the user is a member.

  If user is None, the current user is used.
  """
  if user is None:
    user = get_current_os_user()
  result: List[str] = []
  for group in grp.getgrall():
    if user in group.gr_mem:
      result.append(group.gr_name)
  return sorted(result)

def get_os_groups_of_current_process() -> List[str]:
  """Returns a list of OS group names for which the current process is a member.

  Normally this is the same as get_os_groups_of_user(), but if the current user has been
  added to the group after the current login session started, the current process
  will not be included in the group, and this function will reflect that.
  """
  gids = os.getgroups()
  result: List[str] = []
  for group in grp.getgrall():
    if group.gr_gid in gids:
      result.append(group.gr_name)
  return sorted(result)

def os_group_includes_user(group_name: str, user: Optional[str]=None) -> bool:
  """Returns True if the named OS group includes the named user.

  If user is None, the current user is used.
  """
  groups = get_os_groups_of_user(user=user)
  return group_name in groups

def os_group_includes_current_process(group_name: str) -> bool:
  """Returns True if the named OS group includes the current process.

  Normally this is the same as os_group_includes_user(), but if the current user has been
  added to the group after the current login session started, the current process
  will not be included in the group, and this function will reflect that.
  """
  groups = get_os_groups_of_current_process()
  return group_name in groups

def should_run_with_group(group_name: str, require: bool=True) -> bool:
  """Returns True if the current user is a member of the named OS group,
     but the current process is not.

  If True, this is an indication that the user was added to the group after the current
  login session started, and the user needs to log out and log back in for the
  change to take effect. Alternatively, this indicateds that True should be passed in
  the "run_with_sudo" argument to sudo_*() functions, to run the command in a
  simulation of a new login session.

  If require is True, raises an exception if the group does not exist or
  the user is not a member of the group.

  Args:
      group_name (str):
          The name of the OS group to check.
      require (bool, optional):
          If True, raises an exception if the group does not exist or
          the user is not a member of the group. Defaults to True.

  """
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

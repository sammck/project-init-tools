# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Miscellaneous utility functions"""

from typing import Type, Any, Optional, Union, List, Tuple
from .internal_types import Jsonable

import json
import hashlib
import os
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
import pathlib
import subprocess
import threading
import tempfile
import secrets

from .exceptions import ProjectInitError

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
  return get_git_config_value('user.email', cwd=cwd)

def get_git_root_dir(starting_dir: str=".") -> Optional[str]:
  """Find the root directory of the current git project

  Args:
      starting_dir (str, optional): The subdir in which to begin the search. Defaults to ".".

  Returns:
      Optional[str]: The absolute pathname of the top-level git project directory, or
                     None if starting_dir is not in a git project.
  """
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

#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper class for reading/editing pyproject.toml"""

from typing import Optional, cast, Union, Any, List

import os
import tomlkit
from tomlkit.toml_document import TOMLDocument
from tomlkit.items import Table, Item, Key
from tomlkit.container import Container, OutOfOrderTableProxy
from tomlkit.exceptions import TOMLKitError, ParseError
from .exceptions import ProjectInitError
from .util import get_git_root_dir, atomic_mv

class PyprojectToml:
  project_dir: str
  filename: str
  data: TOMLDocument
  _raw_text: str

  def __init__(self, project_dir: Optional[str]=None, create: bool=False, starting_dir: Optional[str]=None):
    if project_dir is None:
      project_dir = get_git_root_dir(starting_dir=starting_dir)
    elif not project_dir is None:
      project_dir = os.path.abspath(os.path.normpath(os.path.expanduser(project_dir)))
    if project_dir is None:
      raise ValueError("Not in a git project, and project directory not provided")
    if not os.path.isdir(project_dir):
      raise FileNotFoundError(f"Project directory does not exist: {project_dir}")
    self.project_dir = project_dir
    self.filename = os.path.join(project_dir, 'pyproject.toml')
    try:
      with open(self.filename, 'rb') as f:
        bcontent = f.read()
    except FileNotFoundError:
      if create:
        bcontent = b'\n'
        with open(self.filename, 'wb') as f:
          f.write(bcontent)
      else:
        raise
    self.data = tomlkit.parse(bcontent)
    self._raw_text = self.as_toml()

  def __str__(self) -> str:
    return str(self.data)

  def __repr__(self) -> str:
    return f"PyprojectToml({str(self)})"

  def as_toml(self) -> str:
    return tomlkit.dumps(self.data)

  def is_dirty(self) -> bool:
    new_raw_text = self.as_toml()
    return new_raw_text != self._raw_text

  def save(self) -> bool:
    """Saves any changes made to pyproject.toml.

    Returns:
        bool: True if any changes were made
    """
    new_raw_text = self.as_toml()
    if new_raw_text == self._raw_text:
      return False
    tmp_file = self.filename + '.tmp'
    with open(tmp_file, 'w', encoding='utf-8') as f:
      f.write(new_raw_text)
    atomic_mv(tmp_file, self.filename)
    self._raw_text = new_raw_text
    return True

  def get_table(
        self,
        table_name: Union[str, List[str]],
        create: bool=False,
        auto_split: bool=False,
        is_super_table: bool=False
      ) -> Union[Table, OutOfOrderTableProxy, Container]:
    if isinstance(table_name, list):
      parts: List[str] = table_name
    else:
      assert isinstance(table_name, str)
      if table_name != '' and auto_split:
        parts = table_name.split('.')
      else:
        parts = [ table_name ]
    current: Union[Table, OutOfOrderTableProxy, Container]  = self.data
    current_path = ''
    for i, part in enumerate(parts):
      current_path = part if current_path == '' else current_path + '.' + part
      try:
        if create:
          tab = cast(Optional[Union[Item, Container]], current.get(part, None))
          if tab is None:
            tab = tomlkit.table(is_super_table=is_super_table or i + 1 < len(parts))
            current[part] = tab
        else:
          tab = current[part]
      except Exception as e:
        raise KeyError(f"Unable to get TOML table [{current_path}]: {e}") from e
      if not isinstance(tab, (Table, OutOfOrderTableProxy, Container)):
        raise KeyError(f"TOML path [{current_path}] is not a table")
      current = tab
    return current

  def __getitem__(self, key: Union[Key, str]) -> Union[Item, Container]:
    return self.data[key]

  def __setitem__(self, key: Union[Key, str], value: Any) -> None:
    self.data[key] = value

  def __delitem__(self, key: Union[Key, str]) -> None:
    del self.data[key]

  def is_empty(self) -> bool:
    return len(self.data) == 0

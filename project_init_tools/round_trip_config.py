#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
YAML/JSON mutable config file abraction that preserves round-trip
fidelity for YAML updates (e.g., preserves comments).
"""

from typing import (
    Optional, MutableMapping,
    cast, Any, Iterator, ItemsView, ValuesView, KeysView )

import json
from base64 import b64encode, b64decode
import ruamel.yaml # type: ignore[import]
from io import StringIO

from .internal_types import JsonableDict

from .util import (
    file_contents,
  )

class RoundTripConfig(MutableMapping[str, Any]):
  _config_file: str
  _text: str
  _data: MutableMapping[str, Any]
  _yaml: Optional[ruamel.yaml.YAML] = None

  def __init__(self, config_file: str):
    self._config_file = config_file
    text = file_contents(config_file)
    self._text = text
    if config_file.endswith('.yaml'):
      self._yaml = ruamel.yaml.YAML()
      self._data = cast(MutableMapping[str, Any], self._yaml.load(text))
    else:
      self._data = cast(MutableMapping[str, Any], json.loads(text))
    assert isinstance(self._data, dict)

  @property
  def data(self) -> MutableMapping[str, Any]:
    return self._data

  def save(self):
    if self._yaml is None:
      text = json.dumps(cast(JsonableDict, self.data), indent=2, sort_keys=True)
    else:
      with StringIO() as output:
        self._yaml.dump(self.data, output)
        text = output.getvalue()
    if not text.endswith('\n'):
      text += '\n'
    if text != self._text:
      with open(self._config_file, 'w', encoding='utf-8') as f:
        f.write(text)

  def __setitem__(self, key: str, value: Any):
    self.data[key] = value

  def __getitem__(self, key: str) -> Any:
    return self.data[key]

  def __delitem__(self, key:str) -> None:
    del self.data[key]

  def __iter__(self) -> Iterator[Any]:
    return iter(self.data)

  def __len__(self) -> int:
    return len(self.data)

  def __contains__(self, key: object) -> bool:
    return key in self.data

  def keys(self) -> KeysView[str]:
    return self.data.keys()

  def values(self) -> ValuesView[Any]:
    return self.data.values()

  def items(self) -> ItemsView[str, Any]:
    return self.data.items()

  def update(self, *args, **kwargs) -> None:  # pylint: disable=arguments-differ
    if len(args) > 0:
      assert len(args) == 1
      assert len(kwargs) == 0
      for k, v in kwargs.items():
        self.data[k] = v
    else:
      for k, v in kwargs.items():
        self.data[k] = v

#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Type hints used internally by this package"""

from typing import (
    Dict,
    Union,
    Any,
    List,
    TYPE_CHECKING,
  )

JsonableTypes = ( str, int, float, bool, dict, list )
# A tuple of types to use for isinstance checking of JSON-serializable types. Excludes None. Useful for isinstance.

if TYPE_CHECKING:
  # mypy cannot deal with recursive type definitions
  Jsonable = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]
  """A Type hint for a simple JSON-serializable value; i.e., str, int, float, bool, None, Dict[str, Jsonable], List[Jsonable]"""
else:
  Jsonable = Union[str, int, float, bool, None, Dict[str, 'Jsonable'], List['Jsonable']]
  """A Type hint for a simple JSON-serializable value; i.e., str, int, float, bool, None, Dict[str, Jsonable], List[Jsonable]"""

JsonableDict = Dict[str, Jsonable]
"""A type hint for a simple JSON-serializable dict; i.e., Dict[str, Jsonable]"""

JsonableList = List[Jsonable]
"""A type hint for a simple JSON-serializable list; i.e., List[Jsonable]"""

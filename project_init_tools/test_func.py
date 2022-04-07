#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Temporary test function"""

from .internal_types import Jsonable, JsonableDict

def run_test() -> Jsonable:
  outputs: JsonableDict = dict(status="OK")
  return outputs

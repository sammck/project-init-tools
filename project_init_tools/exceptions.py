#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Exceptions defined by this package"""

from subprocess import CalledProcessError

class ProjectInitError(Exception):
  """Base class for all error exceptions defined by this package."""
  #pass

class CalledProcessErrorWithStderrMessage(CalledProcessError):
  def __str__(self):
    return super().__str__() + f": [{self.stderr}]"

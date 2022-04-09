#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""ProjectInitConfig class definition"""

from typing import Optional

import os

from .internal_types import Jsonable, JsonableDict
from .util import get_git_root_dir, yaml, YamlLoader
from .exceptions import ProjectInitError

class ProjectInitConfig:
  config_file: str
  config_data: JsonableDict
  project_root_dir: str
  project_init_dir: str
  project_init_local_dir: str

  def __init__(self, config_file: Optional[str]=None, starting_dir: Optional[str]=None):
    if starting_dir is None:
      starting_dir = '.'
    if config_file is None:
      project_root_dir = get_git_root_dir(starting_dir)
      if project_root_dir is None:
        raise ProjectInitError("Could not locate Git project root directory; please run inside git working directory or use -C")
      config_file = os.path.join(project_root_dir, 'project-init/config.yaml')

    self.config_file = os.path.abspath(os.path.normpath(os.path.expanduser(config_file)))
    with open(self.config_file, encoding='utf-8') as f:
      self.config_data = yaml.load(f, Loader=YamlLoader)
    self.project_init_dir = os.path.dirname(self.config_file)
    self.project_root_dir = os.path.dirname(self.project_init_dir)
    self.project_init_local_dir = os.path.join(self.project_init_dir, ".local")

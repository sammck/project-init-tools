#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""project_init_tools CLI"""

import base64
from typing import (
    Optional, Sequence, List, Union, Dict, TextIO, Mapping, MutableMapping,
    cast, Any, Iterator, Iterable, Tuple, ItemsView, ValuesView, KeysView )

import os
import sys
import datetime
import argparse
from unittest.mock import NonCallableMagicMock
import argcomplete # type: ignore[import]
import json
from base64 import b64encode, b64decode, encode
import colorama # type: ignore[import]
from colorama import Fore, Back, Style
import subprocess
from io import TextIOWrapper
import yaml
from secret_kv import create_kv_store
from urllib.parse import urlparse, ParseResult
import ruamel.yaml # type: ignore[import]
from io import StringIO

try:
  from yaml import CLoader as YamlLoader, CDumper as YamlDumper
except ImportError:
  from yaml import Loader as YamlLoader, Dumper as YamlDumper  #type: ignore[misc]

# This module runs as -m -- do NOT use relative imports
from project_init_tools.util import (
  file_contents,
  get_current_os_user,
)

from project_init_tools import (
    __version__ as pkg_version,
    ProjectInitError,
    PyprojectToml,
    Jsonable,
    JsonableDict,
    JsonableList,
    deactivate_virtualenv,
    full_name_of_type,
    full_type,
    get_aws_account,
    get_aws_region,
    get_aws_session,
    get_git_config_value,
    get_git_root_dir,
    append_lines_to_file_if_missing,
    file_url_to_pathname,
    get_git_user_email,
    get_git_user_friendly_name,
    pathname_to_file_url,
    dedent,
  )

def is_colorizable(stream: TextIO) -> bool:
  is_a_tty = hasattr(stream, 'isattry') and stream.isatty()
  return is_a_tty

class CmdExitError(RuntimeError):
  exit_code: int

  def __init__(self, exit_code: int, msg: Optional[str]=None):
    if msg is None:
      msg = f"Command exited with return code {exit_code}"
    super().__init__(msg)
    self.exit_code = exit_code

class ArgparseExitError(CmdExitError):
  pass

class NoExitArgumentParser(argparse.ArgumentParser):
  def exit(self, status=0, message=None):
    if message:
      self._print_message(message, sys.stderr)
    raise ArgparseExitError(status, message)

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

class CommandHandler:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _config_file: Optional[str] = None
  _cfg: Optional[ProjectInitConfig] = None
  _pyproject_toml: Optional[PyprojectToml] = None

  _raw_stdout: TextIO = sys.stdout
  _raw_stderr: TextIO = sys.stderr
  _raw: bool = False
  _compact: bool = False
  _output_file: Optional[str] = None
  _encoding: str = 'utf-8'

  _colorize_stdout: bool = False
  _colorize_stderr: bool = False

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self._argv = argv

  def ocolor(self, codes: str) -> str:
    return codes if self._colorize_stdout else ""

  def ecolor(self, codes: str) -> str:
    return codes if self._colorize_stderr else ""

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self._cwd, os.path.expanduser(path)))

  def get_or_create_config(self) -> ProjectInitConfig:
    if self._cfg is None and self._config_file is None:
      project_root_dir = get_git_root_dir(self._cwd)
      if project_root_dir is None:
        raise ProjectInitError("Could not locate Git project root directory; please run inside git working directory or use -C")
      project_init_dir = os.path.join(project_root_dir, 'project-init')
      if not os.path.exists(project_init_dir):
        os.mkdir(project_init_dir)
      config_file = os.path.join(project_init_dir, "config.yaml")
      self._config_file = config_file
      if not os.path.exists(config_file):
        new_config_data: JsonableDict = {}
        with open(config_file, 'w', encoding='utf-8') as f:
          yaml.dump(new_config_data, f)
    return self.get_config()

  def get_config(self) -> ProjectInitConfig:
    if self._cfg is None:
      self._cfg = ProjectInitConfig(starting_dir=self._cwd)
    return self._cfg

  def get_config_file(self) -> str:
    return self.get_config().config_file

  def update_config(self, *args, **kwargs):
    cfg_file = self.get_config_file()
    rt = RoundTripConfig(cfg_file)
    rt.update(*args, **kwargs)
    rt.save()

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_project_init_dir(self) -> str:
    return self.get_config().project_init_dir

  def get_project_init_local_dir(self) -> str:
    return self.get_config().project_init_local_dir

  def get_pyproject_toml(self, create: Optional[bool]=False) -> PyprojectToml:
    if create is None:
      create = False
    if self._pyproject_toml is None:
      self._pyproject_toml = PyprojectToml(project_dir=self.get_project_root_dir(), create=create)
    return self._pyproject_toml

  def pretty_print(
        self,
        value: Jsonable,
        compact: Optional[bool]=None,
        colorize: Optional[bool]=None,
        raw: Optional[bool]=None,
      ):

    if raw is None:
      raw = self._raw
    if raw:
      if isinstance(value, str):
        self._raw_stdout.write(value)
        return

    if compact is None:
      compact = self._compact
    if colorize is None:
      colorize = True

    def emit_to(f: TextIO):
      final_colorize = colorize and ((f is sys.stdout and self._colorize_stdout) or (f is sys.stderr and self._colorize_stderr))

      if not final_colorize:
        if compact:
          json.dump(value, f, separators=(',', ':'), sort_keys=True)
        else:
          json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')
      else:
        jq_input = json.dumps(value, separators=(',', ':'), sort_keys=True)
        cmd = [ 'jq' ]
        if compact:
          cmd.append('-c')
        cmd.append('.')
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=f) as proc:
          proc.communicate(input=jq_input.encode('utf-8'))
          exit_code = proc.returncode
        if exit_code != 0:
          raise subprocess.CalledProcessError(exit_code, cmd)

    output_file = self._output_file
    if output_file is None:
      emit_to(sys.stdout)
    else:
      with open(output_file, "w", encoding=self._encoding) as f:
        emit_to(f)

  def cmd_bare(self) -> int:
    print("A command is required", file=sys.stderr)
    return 1

  def cmd_test(self) -> int:
    from project_init_tools.test_func import run_test
    result = run_test()
    self.pretty_print(result)
    return 0

  def cmd_version(self) -> int:
    self.pretty_print(pkg_version)
    return 0

  def cmd_update_pulumi(self) -> int:
    from project_init_tools.installer.pulumi import install_pulumi
    pulumi_dir = os.path.join(self.get_project_init_local_dir(), ".pulumi")
    install_pulumi(pulumi_dir, min_version='latest')
    return 0

  def cmd_run(self) -> int:
    from project_init_tools.util import sudo_call
    args = self._args
    group: Optional[str] = args.run_with_group
    use_sudo: bool = args.use_sudo
    sudo_reason: Optional[str] = args.sudo_reason
    cmd_and_args: List[str] = args.cmd_and_args

    if len(cmd_and_args) == 0:
      cmd_and_args = [ 'bash' ]

    if cmd_and_args[0].startswith('-'):
      raise ProjectInitError(f"Unrecognized command option {cmd_and_args[0]}")

    exit_code = sudo_call(cmd_and_args, run_with_group=group, use_sudo=use_sudo, sudo_reason=sudo_reason)

    return exit_code

  def cmd_init_env(self) -> int:
    from project_init_tools.installer.docker import install_docker
    from project_init_tools.installer.aws_cli import install_aws_cli
    from project_init_tools.installer.gh import install_gh
    from project_init_tools.installer.pulumi import install_pulumi
    from project_init_tools.installer.poetry import install_poetry
    from project_init_tools.util import sudo_call
    from project_init_tools.os_packages import PackageList

    self.get_or_create_config()
    local_dir = self.get_project_init_local_dir()
    project_root_dir = self.get_project_root_dir()
    if not os.path.exists(local_dir):
      os.makedirs(local_dir)
    local_parent_dir = os.path.dirname(local_dir)
    local_gitignore = os.path.join(local_parent_dir, '.gitignore')
    append_lines_to_file_if_missing(local_gitignore, f"/{os.path.basename(local_dir)}/", create_file=True)

    #args = self._args

    pl = PackageList()
    pl.add_packages_if_missing(['build-essential', 'meson', 'ninja-build', 'python3.8', 'python3.8-venv', 'sqlcipher'])
    pl.add_package_if_cmd_missing('sha256sum', 'coreutils')
    pl.add_package_if_cmd_missing('curl')
    pl.add_package_if_cmd_missing('git')
    pl.install_all()

    install_poetry()

    license_filename = os.path.join(project_root_dir, 'LICENSE')
    license_text: Optional[str] = None
    if os.path.exists(license_filename):
      with open(license_filename, encoding='utf-8') as f:
        license_text = f.read()

    def set_toml_default(table, key, value):
      if not value is None and not key in table:
        table[key] = value

    pyproject = self.get_pyproject_toml(create=True)
    t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
    project_name  = cast(Optional[str], t_tool_poetry.get('name', None))
    project_version = cast(Optional[str], t_tool_poetry.get('version', None))
    project_description = cast(Optional[str], t_tool_poetry.get('description', None))
    project_authors = cast(Optional[List[str]], t_tool_poetry.get('authors', None))
    if project_authors is None:
      project_authors = []
    project_license = cast(Optional[str], t_tool_poetry.get('license', None))
    project_keywords = cast(Optional[List[str]], t_tool_poetry.get('keywords', None))
    if project_keywords is None:
      project_keywords = []
    project_readme = cast(Optional[str], t_tool_poetry.get('readme', None))
    project_homepage = cast(Optional[str], t_tool_poetry.get('homepage', None))
    project_repository = cast(Optional[str], t_tool_poetry.get('repository', None))

    if project_name is None:
      project_name = os.path.basename(project_root_dir)
    package_import_name = project_name.replace('-', '_')
    if project_version is None:
      project_version = '0.1.0'
    if project_description is None:
      project_description = f"Python package \"{project_name}\""
    if project_license is None:
      if not license_text is None:
        license_first_line = license_text.split('\n', 1)[0].strip().lower()
        if license_first_line == 'mit license':
          project_license = 'MIT'
    if project_license is None:
      project_license = 'MIT'
    if project_readme is None:
      project_readme = 'README.md'
    if project_repository is None:
      git_remote_repo = get_git_config_value('remote.origin.url', cwd=project_root_dir)
      if not git_remote_repo is None:
        if git_remote_repo.startswith('git@'):
          # force to HTTPS for publishing
          project_repository = f"https://{git_remote_repo[4:].replace(':', '/', 1)}"
        else:
          project_repository = git_remote_repo
    if project_homepage is None:
      if not project_repository is None:
        if project_repository.endswith('.git'):
          trep = project_repository[:-4]
          if trep.startswith('git@'):
            # force to HTTPS for web interface
            project_homepage = f"https://{trep[4:].replace(':', '/', 1)}"
          else:
            project_homepage = trep
    if project_homepage is None:
      username = get_current_os_user()
      project_homepage = f"https://github.com/{username}/{project_name}"
    user_homepage = project_homepage.rsplit('/', 1)[0]

    repo_user: Optional[str] = None
    if not project_repository is None:
      trep = project_repository
      if trep.startswith('git@'):
        # force to HTTPS for web interface
        trep = f"https://{trep[4:].replace(':', '/', 1)}"
      trep_path = urlparse(trep).path
      if trep_path.startswith('/'):
        trep_path = trep_path[1:]
      repo_user = trep_path.split('/', 1)[0]

    friendly_name = get_git_user_friendly_name()
    if friendly_name is None:
      friendly_name = 'John Q. Public'
    legal_name = friendly_name

    if len(project_authors) == 0:
      git_email = get_git_user_email(cwd=project_root_dir)
      if not git_email is None:
        project_authors.append(f"{friendly_name} <{git_email}>")

    year = datetime.date.today().year

    if license_text is None and project_license == 'MIT':
      license_text = dedent(f"""
          MIT License

          Copyright (c) {year} {legal_name}

          Permission is hereby granted, free of charge, to any person obtaining a copy
          of this software and associated documentation files (the "Software"), to deal
          in the Software without restriction, including without limitation the rights
          to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
          copies of the Software, and to permit persons to whom the Software is
          furnished to do so, subject to the following conditions:

          The above copyright notice and this permission notice shall be included in all
          copies or substantial portions of the Software.

          THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
          IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
          FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
          AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
          LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
          OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
          SOFTWARE.
        """)

    project_readme_file = os.path.join(project_root_dir, project_readme)
    if os.path.exists(project_readme_file):
      with open(project_readme_file, encoding='utf-8') as f:
        project_readme_text = f.read()
    else:
      if project_license is None:
        readme_project_license = ''
      else:
        if project_license == 'MIT':
          readme_project_license = (
              '[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)'
            )
          readme_project_license_short = (
              '[MIT License](https://opensource.org/licenses/MIT)'
            )
        else:
          readme_project_license = (
              f'[![License: {project_license}]](https://opensource.org/licenses)'
            )
          readme_project_license_short = (
              '[{project_license} License](https://opensource.org/licenses)'
            )
      project_readme_text = dedent(f"""
          {project_name}: A Python package
          =================================================

          {readme_project_license}
          [![Latest release](https://img.shields.io/github/v/release/{repo_user}/{project_name}.svg?style=flat-square&color=b44e88)](https://github.com/{repo_user}/{project_name}/releases)

          {project_description}

          Table of contents
          -----------------

          * [Introduction](#introduction)
          * [Installation](#installation)
          * [Usage](#usage)
            * [API](api)
          * [Known issues and limitations](#known-issues-and-limitations)
          * [Getting help](#getting-help)
          * [Contributing](#contributing)
          * [License](#license)
          * [Authors and history](#authors-and-history)


          Introduction
          ------------

          Python package `{project_name}` BLAH BLAH.

          Installation
          ------------

          ### Prerequisites

          **Python**: Python 3.8+ is required. See your OS documentation for instructions.

          ### From PyPi

          The current released version of `{project_name}` can be installed with 

          ```bash
          pip3 install {project_name}
          ```

          ### From GitHub

          [Poetry](https://python-poetry.org/docs/master/#installing-with-the-official-installer) is required; it can be installed with:

          ```bash
          curl -sSL https://install.python-poetry.org | python3 -
          ```

          Clone the repository and install {project_name} into a private virtualenv with:

          ```bash
          cd <parent-folder>
          git clone {project_repository}
          cd {project_name}
          poetry install
          ```

          You can then launch a bash shell with the virtualenv activated using:

          ```bash
          poetry shell
          ```


          Usage
          =====

          API
          ---

          TBD

          Known issues and limitations
          ----------------------------


          Getting help
          ------------

          Please report any problems/issues [here]({project_homepage}/issues).

          Contributing
          ------------

          Pull requests welcome.

          License
          -------

          `{project_name}` is distributed under the terms of the {readme_project_license_short}.  The license applies to this file and other files in the [Git repository]({project_homepage}) hosting this file.

          Authors and history
          -------------------

          The author of {project_name} is [{friendly_name}]({user_homepage}).
        """)

    gitignore_file = os.path.join(project_root_dir, ".gitignore")
    gitignore_add_lines=dedent('''
        __pycache__/
        *.py[cod]
        *$py.class
        *.so
        .Python
        build/
        develop-eggs/
        dist/
        downloads/
        eggs/
        .eggs/
        lib/
        lib64/
        parts/
        sdist/
        var/
        wheels/
        pip-wheel-metadata/
        share/python-wheels/
        *.egg-info/
        .installed.cfg
        *.egg
        MANIFEST
        pip-log.txt
        pip-delete-this-directory.txt
        htmlcov/
        .tox/
        .nox/
        .coverage
        .coverage.*
        .cache
        nosetests.xml
        coverage.xml
        *.cover
        *.py,cover
        .hypothesis/
        .pytest_cache/
        *.mo
        *.pot
        *.log
        local_settings.py
        db.sqlite3
        db.sqlite3-journal
        instance/
        .webassets-cache
        .scrapy
        docs/_build/
        target/
        .ipynb_checkpoints
        profile_default/
        ipython_config.py
        .python-version
        __pypackages__/
        celerybeat-schedule
        celerybeat.pid
        *.sage.py
        .env
        .venv
        env/
        venv/
        ENV/
        env.bak/
        venv.bak/
        .spyderproject
        .spyproject
        .ropeproject
        /site
        .mypy_cache/
        .dmypy.json
        dmypy.json
        .pyre/
        /trash/
        /.xppulumi/
        /.secret-kv/
      ''').rstrip().split('\n')

    xp_dir = os.path.join(project_root_dir, 'xp')
    xp_project_parent_dir = os.path.join(xp_dir, 'project')
    xp_backend_parent_dir = os.path.join(xp_dir, 'backend')
    aws_session = get_aws_session()
    aws_account = get_aws_account(aws_session)
    aws_region: str = cast(str, get_aws_region(aws_session, 'us-west-2'))
    aws_venv_suffix = "-2"         # allows us to create multiple parallel installations in the same AWS account

    local_backend_name = 'local'
    local_backend_dir = os.path.join(xp_backend_parent_dir, local_backend_name)
    local_backend_config_file = os.path.join(local_backend_dir, 'backend.json')
    local_backend_org_dir = os.path.join(local_backend_dir, 'state', 'g')
    local_backend_config: JsonableDict = dict(
        options = dict(
            includes_organization = False,
            includes_project = False,
            default_organization = "g",
          ),
        name = local_backend_name,
        uri = "file://./state",
      )

    s3_backend_project_name = "s3_backend"
    s3_backend_stack_name = "global"

    s3_backend_name = 's3'
    s3_backend_dir = os.path.join(xp_backend_parent_dir, s3_backend_name)
    s3_backend_bucket_name = f"{aws_account}-{aws_region}-xpulumi{aws_venv_suffix}"
    s3_backend_subkey = f"xpulumi{aws_venv_suffix}/prj"
    s3_backend_uri = f"s3://{s3_backend_bucket_name}/{s3_backend_subkey}"
    s3_backend_config_file = os.path.join(s3_backend_dir, 'backend.json')
    s3_backend_config: JsonableDict = dict(
        options = dict(
            includes_organization = False,
            includes_project = False,
            default_organization = "g",
            aws_region = aws_region,
            aws_account = aws_account,
            s3_bucket_stack = f"{s3_backend_project_name}:{s3_backend_stack_name}",
          ),
        name = s3_backend_name,
        uri = s3_backend_uri,
      )

    s3_backend_project_dir = os.path.join(xp_project_parent_dir, s3_backend_project_name)
    s3_backend_pulumi_project_name = f"be-{aws_account}-{aws_region}{aws_venv_suffix}"
    s3_backend_project_state_dir = os.path.join(local_backend_org_dir, s3_backend_pulumi_project_name)
    s3_backend_project_main_py_file = os.path.join(s3_backend_project_dir, '__main__.py')
    s3_backend_project_xpulumi_config_file = os.path.join(s3_backend_project_dir, 'xpulumi-project.json')
    s3_backend_project_pulumi_config_file = os.path.join(s3_backend_project_dir, 'Pulumi.yaml')
    s3_backend_project_pulumi_stack_config_file = os.path.join(s3_backend_project_dir, f'Pulumi.{s3_backend_stack_name}.yaml')
    s3_backend_project_xpulumi_config: JsonableDict = dict(
        pulumi_project_name = s3_backend_pulumi_project_name,
        organization = "g",
        backend = local_backend_name
      )
    s3_backend_project_pulumi_config: JsonableDict = dict(
        description = "Simple locally-backed pulumi project that manages an S3 backend used by all other projects",
        name = s3_backend_pulumi_project_name,
        runtime = dict(
            name = "python",
            options = dict(
                virtualenv = "../../../.venv"
              )
          )
      )
    s3_backend_project_pulumi_stack_config: JsonableDict = dict(
        config = {
            'aws:region': aws_region,
            f'{s3_backend_pulumi_project_name}:backend_url': s3_backend_uri,
          }
      )

    # ------

    if not os.path.isdir(xp_project_parent_dir):
      os.makedirs(xp_project_parent_dir)
    if not os.path.isdir(xp_backend_parent_dir):
      os.makedirs(xp_backend_parent_dir)
    if not os.path.exists(s3_backend_project_state_dir):
      os.makedirs(s3_backend_project_state_dir)
    if not os.path.exists(local_backend_config_file):
      with open(local_backend_config_file, 'w', encoding='utf-8') as f:
        json.dump(local_backend_config, f, indent=2, sort_keys=True)
    if not os.path.exists(s3_backend_project_state_dir):
      os.makedirs(s3_backend_project_state_dir)
    if not os.path.exists(local_backend_config_file):
      with open(local_backend_config_file, 'w', encoding='utf-8') as f:
        json.dump(local_backend_config, f, indent=2, sort_keys=True)
    if not os.path.exists(s3_backend_dir):
      os.makedirs(s3_backend_dir)
    if not os.path.exists(s3_backend_config_file):
      with open(s3_backend_config_file, 'w', encoding='utf-8') as f:
        json.dump(s3_backend_config, f, indent=2, sort_keys=True)
    if not os.path.exists(s3_backend_project_dir):
      os.makedirs(s3_backend_project_dir)
    if not os.path.exists(s3_backend_project_xpulumi_config_file):
      with open(s3_backend_project_xpulumi_config_file, 'w', encoding='utf-8') as f:
        json.dump(s3_backend_project_xpulumi_config, f, indent=2, sort_keys=True)
    if not os.path.exists(s3_backend_project_pulumi_config_file):
      with open(s3_backend_project_pulumi_config_file, 'w', encoding='utf-8') as f:
        yaml.dump(s3_backend_project_pulumi_config, f)
    if not os.path.exists(s3_backend_project_pulumi_stack_config_file):
      with open(s3_backend_project_pulumi_stack_config_file, 'w', encoding='utf-8') as f:
        yaml.dump(s3_backend_project_pulumi_stack_config, f)

    append_lines_to_file_if_missing(gitignore_file, gitignore_add_lines, create_file=True)

    set_toml_default(t_tool_poetry, 'name', project_name)
    set_toml_default(t_tool_poetry, 'version', project_version)
    set_toml_default(t_tool_poetry, 'description', project_description)
    set_toml_default(t_tool_poetry, 'authors', project_authors)
    set_toml_default(t_tool_poetry, 'license', project_license)
    set_toml_default(t_tool_poetry, 'keywords', project_keywords)
    set_toml_default(t_tool_poetry, 'readme', project_readme)
    set_toml_default(t_tool_poetry, 'homepage', project_homepage)
    set_toml_default(t_tool_poetry, 'repository', project_repository)

    t_tool_poetry_dependencies = pyproject.get_table('tool.poetry.dependencies', auto_split=True, create=True)
    set_toml_default(t_tool_poetry_dependencies, 'python', "^3.8")
    set_toml_default(t_tool_poetry_dependencies,
        'xpulumi', dict(git="https://github.com/sammck/xpulumi.git", branch='main'))

    t_tool_poetry_dev_dependencies = pyproject.get_table('tool.poetry.dev-dependencies', auto_split=True, create=True)
    set_toml_default(t_tool_poetry_dev_dependencies, 'mypy', "^0.931")
    set_toml_default(t_tool_poetry_dev_dependencies, 'dunamai', "^1.9.0")
    set_toml_default(t_tool_poetry_dev_dependencies, 'python-semantic-release', "^7.25.2")
    set_toml_default(t_tool_poetry_dev_dependencies, 'types-urllib3', "^1.26.11")
    set_toml_default(t_tool_poetry_dev_dependencies, 'types-PyYAML', "^6.0.5")
    set_toml_default(t_tool_poetry_dev_dependencies, 'pylint', "^2.13.5")
    t_build_system = pyproject.get_table('build-system', auto_split=True, create=True)
    set_toml_default(t_build_system, 'requires', ["poetry-core>=1.0.0"])
    set_toml_default(t_build_system, 'build-backend', "poetry.core.masonry.api")

    pyproject.get_table('tool.poetry.scripts', auto_split=True, create=True)
    t_tool_semantic_release = pyproject.get_table('tool.semantic_release', auto_split=True, create=True)
    set_toml_default(t_tool_semantic_release, 'version_variable', f'{package_import_name}/version.py:__version__')
    set_toml_default(t_tool_semantic_release, 'version_toml', 'pyproject.toml:tool.poetry.version')
    set_toml_default(t_tool_semantic_release, 'upload_to_pypi', False)
    set_toml_default(t_tool_semantic_release, 'upload_to_release', True)
    set_toml_default(t_tool_semantic_release, 'build_command', "pip install poetry && poetry build")
    t_tool_pylint_messages_control = pyproject.get_table(['tool', 'pylint', 'MESSAGES CONTROL'], create=True)
    set_toml_default(t_tool_pylint_messages_control, 'disable', [
        "wrong-import-order",
        "duplicate-code",
        "too-many-arguments",
        "missing-function-docstring",
        "import-outside-toplevel",
        "too-few-public-methods",
        "missing-class-docstring",
        "unused-import",
        "too-many-locals",
        "unused-argument",
        "invalid-name",
        "no-self-use",
        "global-statement",
        "broad-except",
        "too-many-branches",
        "too-many-statements",
        "exec-used",
        "ungrouped-imports",
        "subprocess-popen-preexec-fn",
        "multiple-statements",
        "too-many-public-methods",
        "missing-module-docstring",
        "too-many-instance-attributes",
        "too-many-nested-blocks",
        "unneeded-not",
        "unnecessary-lambda",
      ])
    t_tool_pylint_format = pyproject.get_table('tool.pylint.FORMAT', auto_split = True, create=True)
    set_toml_default(t_tool_pylint_format, 'indent-after-paren', 4)
    set_toml_default(t_tool_pylint_format, 'indent-string', '  ')
    set_toml_default(t_tool_pylint_format, 'max-line-length', 200)

    pyproject.save()

    project_name = cast(Optional[str], t_tool_poetry.get('name', None))
    project_version = cast(Optional[str], t_tool_poetry.get('version', None))
    project_description = cast(Optional[str], t_tool_poetry.get('description', None))
    project_authors = cast(Optional[List[str]], t_tool_poetry.get('authors', None))
    if project_authors is None:
      project_authors = []
    project_license = cast(Optional[str], t_tool_poetry.get('license', None))
    project_keywords = cast(Optional[List[str]], t_tool_poetry.get('keywords', None))
    if project_keywords is None:
      project_keywords = []
    project_readme = cast(Optional[str], t_tool_poetry.get('readme', None))
    project_homepage = cast(Optional[str], t_tool_poetry.get('homepage', None))
    project_repository = cast(Optional[str], t_tool_poetry.get('repository', None))

    package_dir = os.path.join(project_root_dir, package_import_name)
    if not os.path.exists(package_dir):
      os.mkdir(package_dir)

    pyfile_header = dedent(f'''
            # Copyright (c) {year} {legal_name}
            #
            # See LICENSE file accompanying this package.
            #

      ''')

    def write_pyfile(filename: str, content: str, executable: bool = False):
      filename = os.path.join(package_dir, filename)
      if not os.path.exists(filename):
        with open(filename, 'w', encoding='utf-8') as f:
          if executable:
            f.write("#!/usr/bin/env python3\n")
          f.write(pyfile_header+dedent(content))

    write_pyfile(s3_backend_project_main_py_file, '''
        import pulumi
        import pulumi_aws as aws
        from xpulumi.runtime import pconfig, aws_provider, split_s3_uri

        backend_uri = pconfig.require("backend_uri")
        bucket_name, backend_subkey = split_s3_uri(backend_uri)
        while backend_subkey.endswith('/'):
          backend_subkey = backend_subkey[:-1]

        slash_backend_subkey = '' if backend_subkey == '' else '/' + backend_subkey

        bucket = aws.s3.Bucket("bucket",
            bucket=bucket_name,
            opts=pulumi.ResourceOptions(
                provider=aws_provider,
              )
          )

        pulumi.export("backend_bucket", bucket_name)
        pulumi.export("backend_subkey", backend_subkey)
        pulumi.export("backend_uri", backend_uri)
      ''')


    write_pyfile("__init__.py", f'''
            """
            Package {package_import_name}: {project_description}
            """

            from .version import __version__
          ''')
    write_pyfile("version.py", f'''
            """
            Automatically updated version information for this package
            """

            # The following line is automatically updated with "semantic-release version"
            __version__ =  "{project_version}"

            __all__ = [ '__version__' ]
          ''')

    pytyped_file = os.path.join(package_dir, 'py.typed')
    if not os.path.exists(pytyped_file):
      with open(pytyped_file, 'w', encoding='utf-8') as f:
        pass

    if not license_text is None and not os.path.exists(license_filename):
      with open(license_filename, 'w', encoding='utf-8') as f:
        f.write(license_text)

    if not project_readme_text is None and not os.path.exists(project_readme_file):
      with open(project_readme_file, 'w', encoding='utf-8') as f:
        f.write(project_readme_text)

    install_docker()
    install_aws_cli()
    install_gh()

    project_root_dir = self.get_project_root_dir()
    project_init_pulumi_dir = os.path.join(local_dir, '.pulumi')
    install_pulumi(project_init_pulumi_dir, min_version='latest')
    secret_kv_dir = os.path.join(project_root_dir, '.secret-kv')
    if not os.path.exists(secret_kv_dir):
      create_kv_store(project_root_dir)

    no_venv_environ = dict(os.environ)
    deactivate_virtualenv(no_venv_environ)

    subprocess.check_call(['poetry', 'install'], cwd=project_root_dir, env=no_venv_environ)

    return 0

  def run(self) -> int:
    """Run the project_init_tools command-line tool with provided arguments

    Args:
        argv (Optional[Sequence[str]], optional):
            A list of commandline arguments (NOT including the program as argv[0]!),
            or None to use sys.argv[1:]. Defaults to None.

    Returns:
        int: The exit code that would be returned if this were run as a standalone command.
    """
    parser = argparse.ArgumentParser(description="Manage pulumi-based projects.")

    # ======================= Main command

    self._parser = parser
    parser.add_argument('--traceback', "--tb", action='store_true', default=False,
                        help='Display detailed exception information')
    parser.add_argument('-M', '--monochrome', action='store_true', default=False,
                        help='Output to stdout/stderr in monochrome. Default is to colorize if stream is a compatible terminal')
    parser.add_argument('-c', '--compact', action='store_true', default=False,
                        help='Compact instead of pretty-printed output')
    parser.add_argument('-r', '--raw', action='store_true', default=False,
                        help='''Output raw strings and binary content directly, not json-encoded.
                                Values embedded in structured results are not affected.''')
    parser.add_argument('-o', '--output', dest="output_file", default=None,
                        help='Write output value to the specified file instead of stdout')
    parser.add_argument('--text-encoding', default='utf-8',
                        help='The encoding used for text. Default  is utf-8')
    parser.add_argument('-C', '--cwd', default='.',
                        help="Change the effective directory used to search for configuration")
    parser.add_argument('--config',
                        help="Specify the location of the config file")
    parser.set_defaults(func=self.cmd_bare)

    subparsers = parser.add_subparsers(
                        title='Commands',
                        description='Valid commands',
                        help='Additional help available with "project_init_tools <command-name> -h"')


    # ======================= version

    parser_version = subparsers.add_parser('version',
                            description='''Display version information. JSON-quoted string. If a raw string is desired, user -r.''')
    parser_version.set_defaults(func=self.cmd_version)

    # ======================= init-env

    parser_init_env = subparsers.add_parser('init-env',
                            description='''Initialize a new overall GitHub project environment.''')
    parser_init_env.set_defaults(func=self.cmd_init_env)

    # ======================= run

    parser_run = subparsers.add_parser('run',
                            description='''Run a command, optionally in group or with sudo.''')
    parser_run.add_argument('-g', '--group', dest="run_with_group", default=None,
                        help='Run with membership in the specified OS group, using sudo if current process has not picked up membership')
    parser_run.add_argument('--sudo', dest="use_sudo", action='store_true', default=False,
                        help='''Run with sudo.''')
    parser_run.add_argument('--sudo-reason', default=None,
                        help='Provide a reason for why sudo is needed, if it turns out to be needed')
    parser_run.add_argument('cmd_and_args', nargs=argparse.REMAINDER,
                        help='Command and arguments as would be provided to sudo.')
    parser_run.set_defaults(func=self.cmd_run)

    # ======================= update-pulumi

    parser_update_pulumi = subparsers.add_parser('update-pulumi', description="Update the Pulumi CLI to the latest version.")
    parser_update_pulumi.set_defaults(func=self.cmd_update_pulumi)

    # ======================= test

    parser_test = subparsers.add_parser('test', description="Run a simple test. For debugging only.  Will be removed.")
    parser_test.set_defaults(func=self.cmd_test)

    # =========================================================

    argcomplete.autocomplete(parser)
    try:
      args = parser.parse_args(self._argv)
    except ArgparseExitError as ex:
      return ex.exit_code
    traceback: bool = args.traceback
    try:
      self._args = args
      self._raw_stdout = sys.stdout
      self._raw_stderr = sys.stderr
      self._raw = args.raw
      self._compact = args.compact
      self._output_file = args.output_file
      self._encoding = args.text_encoding
      monochrome: bool = args.monochrome
      if not monochrome:
        self._colorize_stdout = is_colorizable(sys.stdout)
        self._colorize_stderr = is_colorizable(sys.stderr)
        if self._colorize_stdout or self._colorize_stderr:
          colorama.init(wrap=False)
          if self._colorize_stdout:
            sys.stdout = colorama.AnsiToWin32(sys.stdout)
          if self._colorize_stderr:
            sys.stderr = colorama.AnsiToWin32(sys.stderr)

        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
          self._colorize_stdout = True
        if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
          self._colorize_stderr = True
      self._cwd = os.path.abspath(os.path.expanduser(args.cwd))
      config_file: Optional[str] = args.config
      if not config_file is None:
        self._config_file = self.abspath(config_file)
      rc = args.func()
    except Exception as ex:
      if isinstance(ex, CmdExitError):
        rc = ex.exit_code
      else:
        rc = 1
      if rc != 0:
        if traceback:
          raise

        print(f"{self.ecolor(Fore.RED)}project_init_tools: error: {ex}{self.ecolor(Style.RESET_ALL)}", file=sys.stderr)
    return rc

def run(argv: Optional[Sequence[str]]=None) -> int:
  try:
    rc = CommandHandler(argv).run()
  except CmdExitError as ex:
    rc = ex.exit_code
  return rc

# allow running with "python3 -m", or as a standalone script
if __name__ == "__main__":
  sys.exit(run())

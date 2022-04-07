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
import argparse
import argcomplete # type: ignore[import]
import json
from base64 import b64encode, b64decode
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

# NOTE: this module runs with -m; do not use relative imports
from project_init_tools.exceptions import ProjectInitError
from project_init_tools.installer.util import file_contents
from project_init_tools import (
    __version__ as pkg_version,
    Jsonable,
    JsonableDict,
    JsonableList,
  )
from project_init_tools.util import (
    full_name_of_type,
    full_type,
    get_git_root_dir,
    append_lines_to_file_if_missing,
    file_url_to_pathname,
    pathname_to_file_url
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
    self.project_root_dir = os.path.dirname(self.config_file)
    self.project_init_dir = os.path.join(self.project_root_dir, "project-init")
    self.project_init_local_dir = os.path.join(self.project_init_dir, ".local")

class CommandHandler:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _config_file: Optional[str] = None
  _cfg: Optional[ProjectInitConfig] = None

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
    from project_init_tools.installer import install_pulumi
    pulumi_dir = os.path.join(self.get_project_init_local_dir(), ".pulumi")
    install_pulumi(pulumi_dir, min_version='latest')
    return 0

  def cmd_run(self) -> int:
    from project_init_tools.installer.util import sudo_call
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
    from project_init_tools.installer.util import sudo_call
    from project_init_tools.installer.os_packages import PackageList

    cfg = self.get_or_create_config()
    local_dir = cfg.project_init_local_dir
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

    install_docker()
    install_aws_cli()
    install_gh()

    project_root_dir = cfg.project_root_dir

    install_poetry()

    project_init_pulumi_dir = os.path.join(local_dir, '.pulumi')
    install_pulumi(project_init_pulumi_dir, min_version='latest')
    project_root_dir = cfg.project_root_dir
    secret_kv_dir = os.path.join(project_root_dir, '.secret-kv')
    if not os.path.exists(secret_kv_dir):
      create_kv_store(project_root_dir)

    return 0

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

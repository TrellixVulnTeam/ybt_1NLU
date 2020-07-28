# -*- coding: utf-8 -*-

# Copyright 2020 Resonai Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=invalid-name, unused-argument

"""
yabt Go Builder
~~~~~~~~~~~~~~~

:author: Itamar Ostricher

TODO: libs, external libs
TODO: does this even work with non-flat source file tree??
"""
from ..config import YSETTINGS_FILE
from os import listdir, remove
from os.path import isfile, join, relpath

from yabt.docker import extend_runtime_params, format_docker_run_params
from ..artifact import ArtifactType as AT
from .dockerapp import build_app_docker_and_bin, register_app_builder_sig
from ..extend import (
    PropType as PT, register_build_func, register_builder_sig,
    register_manipulate_target_hook)
from ..logging import make_logger
from ..target_utils import split
from ..utils import link_files, link_node, rmtree, yprint

logger = make_logger(__name__)


register_app_builder_sig('GoApp', [('main', PT.Target)])


@register_manipulate_target_hook('GoApp')
def go_app_manipulate_target(build_context, target):
    logger.debug('Injecting {} to deps of {}',
                 target.props.base_image, target.name)
    target.deps.append(target.props.base_image)
    if target.props.main and target.props.main not in target.deps:
        logger.debug('Injecting {} to deps of {}',
                     target.props.main, target.name)
        target.deps.append(target.props.main)


@register_build_func('GoApp')
def go_app_builder(build_context, target):
    """Pack a Go binary as a Docker image with its runtime dependencies."""
    yprint(build_context.conf, 'Build GoApp', target)
    prog = build_context.targets[target.props.main]
    binary = list(prog.artifacts.get(AT.binary).keys())[0]
    entrypoint = ['/usr/src/bin/' + binary]
    build_app_docker_and_bin(
        build_context, target, entrypoint=entrypoint)


# Common Go builder signature terms
GO_SIG = [
    ('sources', PT.FileList),
    ('in_buildenv', PT.Target),
    ('protos', PT.TargetList, None),
    ('go_module', PT.str, None),
    ('cmd_env', None),
]

register_builder_sig('GoProg', GO_SIG)


@register_manipulate_target_hook('GoProg')
def go_prog_manipulate_target(build_context, target):
    target.buildenv = target.props.in_buildenv


@register_build_func('GoProg')
def go_prog_builder(build_context, target):
    """Build a Go binary executable"""
    go_builder_internal(build_context, target, command='build')


register_builder_sig('GoPackage', GO_SIG)


@register_manipulate_target_hook('GoPackage')
def go_package_manipulate_target(build_context, target):
    target.buildenv = target.props.in_buildenv
    target.deps.extend(target.props.protos)


@register_build_func('GoPackage')
def go_package_builder(build_context, target):
    """Build a Go package"""
    go_builder_internal(build_context, target, command='build', is_binary=False)


register_builder_sig('GoTest', GO_SIG)


@register_manipulate_target_hook('GoTest')
def go_test_manipulate_target(build_context, target):
    target.tags.add('testable')
    target.buildenv = target.props.in_buildenv


@register_build_func('GoTest')
def go_test_builder(build_context, target):
    """Test a Go test"""
    go_builder_internal(build_context, target, command='test')


def rm_all_but_go_mod(workspace_dir):
    for fname in listdir(workspace_dir):
        if fname == 'go.mod':
            continue
        filepath = join(workspace_dir, fname)
        if isfile(filepath):
            remove(filepath)
        else:
            rmtree(filepath)


def go_builder_internal(build_context, target, command, is_binary=True):
    """
    Build or test a Go package or Go binary executable.

    :param is_binary: True if binary artifact.
    :param build_context:
    :param target:
    :param command: Can be either 'build' or 'test'
    :return: Nothing

    Build of all Go targets is done under one module tree workspace.
    We link all Go source files to the module tree.
    We link all Go proto generated files to 'proto' sub-module under the root module tree.
    We create a go.mod file in module root, with "replace proto => ./proto", and another go.mod
    file in the 'proto' root with same module name.
    We set the first dir in GOPATH to be yabtwork/go so that all downloaded
    packages are managed in the user machine and not inside the ephemeral
    docker.
    Workspace is not cleaned, since it accumulates all the Go source files from all Go targets.

    TODOs:
      - Support multiple modules in the same repo. Currently only one module is supported. Better than
        to define go_module in YSettings and not in the different targets. Using different go_modules is untested, and
        will probably will not be able to import packages from one module in another one.

    """

    builder_name = target.builder_name
    yprint(build_context.conf, command, builder_name, target)
    go_module = target.props.get('go_module', None) or build_context.conf.get('go_module', None)
    if not go_module:
        raise KeyError("Must specify go_module in {} common_conf or on target".format(YSETTINGS_FILE))
    workspace_dir = join(build_context.conf.get_go_workspace_path(), go_module)

    buildenv_workspace = build_context.conf.host_to_buildenv_path(workspace_dir)
    buildenv_sources = [join(buildenv_workspace, src) for src in target.props.sources]

    has_protos = False
    for proto_dep_name in target.props.protos:
        proto_dep = build_context.targets[proto_dep_name]
        artifact_map = proto_dep.artifacts.get(AT.gen_go)
        if not artifact_map:
            continue
        has_protos = True
        for dst, src in artifact_map.items():
            target_file = join(workspace_dir, dst)
            link_node(join(build_context.conf.project_root, src), target_file)
            buildenv_sources.append(join(buildenv_workspace, dst))

    sources_to_link = list(target.props.sources)
    link_files(sources_to_link, workspace_dir, None, build_context.conf)

    download_cache_dir = build_context.conf.host_to_buildenv_path(
        build_context.conf.get_go_packages_path())

    gopaths = [download_cache_dir]
    user_gopath = (target.props.cmd_env or {}).get('GOPATH')
    # if user didn't provide GOPATH we assumes it is /go
    # TODO(eyal): A more correct behavior will be to check the GOPATH var
    # inside the docker then to assumes it is /go.
    # This code provides a way for the user to tell us what is the correct
    # GOPATH but if it come handy we should implement looking into the docker
    gopaths.append(user_gopath if user_gopath else '/go')
    build_cmd_env = {
        'XDG_CACHE_HOME': '/tmp/.cache',
    }
    build_cmd_env.update(target.props.cmd_env or {})
    build_cmd_env['GOPATH'] = ':'.join(gopaths)

    go_mod_path = join(workspace_dir, 'go.mod')
    if not isfile(go_mod_path):
        build_context.run_in_buildenv(
            target.props.in_buildenv,
            ['go', 'mod', 'init', go_module],
            build_cmd_env,
            work_dir=buildenv_workspace
        )
    if has_protos and not isfile(join(workspace_dir, 'proto', 'go.mod')):
        build_context.run_in_buildenv(
            target.props.in_buildenv,
            ['go', 'mod', 'edit', '-replace', 'proto=./proto'],
            build_cmd_env,
            work_dir=buildenv_workspace
        )
        build_context.run_in_buildenv(
            target.props.in_buildenv,
            ['go', 'mod', 'init', go_module],
            build_cmd_env,
            work_dir=join(buildenv_workspace, 'proto')
        )

    binary = join(*split(target.name)) if is_binary else None
    binary_args = []
    if binary:
        bin_file = join(buildenv_workspace, binary)
        binary_args.extend(['-o', bin_file])
    build_cmd = ['go', command] + binary_args + buildenv_sources
    run_params = extend_runtime_params(target.props.runtime_params,
                                       build_context.walk_target_deps_topological_order(target),
                                       build_context.conf.runtime_params, True)
    build_context.run_in_buildenv(target.props.in_buildenv, build_cmd, build_cmd_env,
                                  run_params=format_docker_run_params(run_params), work_dir=buildenv_workspace)
    if binary:
        target.artifacts.add(AT.binary, relpath(join(workspace_dir, binary), build_context.conf.project_root), binary)

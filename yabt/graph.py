# -*- coding: utf-8 -*-

# Copyright 2016 Resonai Ltd. All rights reserved
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

"""
yabt target graph
~~~~~~~~~~~~~~~~~

:author: Itamar Ostricher
"""

from os.path import relpath

import networkx
from networkx.algorithms import dag

from .buildfile_parser import process_build_file
from .compat import walk
from .config import Config
from .logging import make_logger
from .target_utils import norm_name, parse_target_selectors, split
from .utils import yprint


logger = make_logger(__name__)


def stable_reverse_topological_sort(graph):
    """Return a list of nodes in topological sort order.

    This topological sort is a **unique** permutation of the nodes
    such that an edge from u to v implies that u appears before v in the
    topological sort order.

    Parameters
    ----------
    graph : NetworkX digraph
            A directed graph

    Raises
    ------
    NetworkXError
        Topological sort is defined for directed graphs only. If the
        graph G is undirected, a NetworkXError is raised.
    NetworkXUnfeasible
        If G is not a directed acyclic graph (DAG) no topological sort
        exists and a NetworkXUnfeasible exception is raised.

    Notes
    -----
    - This algorithm is based on a description and proof in
      The Algorithm Design Manual [1]_ .
    - This implementation is modified from networkx 1.11 implementation [2]_
      to achieve stability, support only reverse (allows yielding instead of
      returning a list), and remove the `nbunch` argument (had no use for it).

    See also
    --------
    is_directed_acyclic_graph

    References
    ----------
    .. [1] Skiena, S. S. The Algorithm Design Manual  (Springer-Verlag, 1998).
        http://www.amazon.com/exec/obidos/ASIN/0387948600/ref=ase_thealgorithmrepo/
    .. [2] networkx on GitHub
        https://github.com/networkx/networkx/blob/8358afac209c00b7feb3e81c901098852a9413b3/networkx/algorithms/dag.py#L88-L168
    """
    if not graph.is_directed():
        raise networkx.NetworkXError(
            'Topological sort not defined on undirected graphs.')

    # nonrecursive version
    seen = set()
    explored = set()

    for v in sorted(graph.nodes()):
        if v in explored:
            continue
        fringe = [v]  # nodes yet to look at
        while fringe:
            w = fringe[-1]  # depth first search
            if w in explored:  # already looked down this branch
                fringe.pop()
                continue
            seen.add(w)     # mark as seen
            # Check successors for cycles and for new nodes
            new_nodes = []
            for n in sorted(graph[w]):
                if n not in explored:
                    if n in seen:  # CYCLE!! OH NOOOO!!
                        raise networkx.NetworkXUnfeasible(
                            'Graph contains a cycle.')
                    new_nodes.append(n)
            if new_nodes:   # Add new_nodes to fringe
                fringe.extend(new_nodes)
            else:           # No new nodes so w is fully explored
                explored.add(w)
                yield w
                fringe.pop()  # done considering this node


def build_target_dep_graph(build_context, unused_conf: Config):
    build_context.target_graph = networkx.DiGraph()
    for target_name, target in build_context.targets.items():
        build_context.target_graph.add_node(target_name)
        for dep in target.deps:
            build_context.target_graph.add_edge(target_name, dep)


def norm_rel_target(target_spec, build_module):
    if ':' not in target_spec:
        target_spec += ':*'
    return norm_name(build_module, target_spec)


def generate_all_targets(conf: Config):
    # TODO(itamar): add ignore marker files / flags
    for root, unused_dirs, files in walk(conf.project_root):
        if conf.build_file_name in files:
            yield norm_rel_target(relpath(root, conf.project_root), '//')


def populate_targets_graph(build_context, conf: Config):
    # Process project root build file
    process_build_file(conf.get_project_build_file(), build_context, conf)
    targets_to_prune = set(build_context.targets.keys())
    if conf.targets:
        logger.debug('targets: {}', conf.targets)
        # TODO(itamar): Figure out how to support a target selector that is a
        #   parent directory which isn't a build module, but contains build
        #   modules (e.g., `ybt tree yapi` from the `dag` test root).
        seeds = parse_target_selectors(conf.targets, conf)
        logger.debug('seeds: {}', seeds)
    else:
        default_target = ':{}'.format(conf.default_target_name)
        logger.info('searching for default target {}', default_target)
        if default_target not in build_context.targets:
            raise RuntimeError(
                'No default target found, and no target selector specified')
        seeds = [default_target]

    def extend_seeds(target_name):
        target = build_context.targets[target_name]
        seeds.extend(target.deps)
        if target.buildenv:
            seeds.append(target.buildenv)

    # Crawl rest of project from seeds
    seeds_used_for_extending = set()
    for seed in seeds:
        if seed in build_context.targets:
            #
            if seed in targets_to_prune:
                targets_to_prune.remove(seed)
            if seed not in seeds_used_for_extending:
                # Avoid infinite loop in case of cyclic dependencies
                extend_seeds(seed)
                seeds_used_for_extending.add(seed)
        else:
            if seed == '**:*':
                # Adding all build modules under current working directory as
                # seeds
                seeds.extend(generate_all_targets(conf))
                continue
            build_module, target_name = split(seed)
            process_build_file(conf.get_build_file_path(build_module),
                               build_context, conf)
            # Parsed build file with this seed target - add its dependencies as
            # seeds
            if target_name == '*':
                # It's a wildcard - add all targets from build module
                # (and skip adding to targets_to_prune altogether)
                for module_target in (
                        build_context.targets_by_module[build_module]):
                    extend_seeds(module_target)
            else:
                if seed not in build_context.targets:
                    raise RuntimeError(
                        'Don\'t know how to make `{}\''.format(seed))
                #
                for module_target in (
                        build_context.targets_by_module[build_module]):
                    targets_to_prune.add(module_target)
                targets_to_prune.remove(seed)
                extend_seeds(seed)
        # TODO(itamar): Write tests that pruning is *ALWAYS* correct!
        # e.g., not pruning things it shouldn't (like when targets are in prune
        # list when loaded initially, but should be removed later because a
        # target loaded later required them).

    # Pruning, after parsing is done
    # (first, adding targets that are tagged as "prune-me" to prune list)
    targets_to_prune.update(
        target_name
        for target_name, target in build_context.targets.items()
        if 'prune-me' in target.tags)
    for target_name in targets_to_prune:
        build_context.remove_target(target_name)

    build_target_dep_graph(build_context, conf)
    if not dag.is_directed_acyclic_graph(build_context.target_graph):
        cycles = '\n'.join(
            ' -> '.join(cycle)
            for cycle in networkx.simple_cycles(build_context.target_graph))
        raise RuntimeError('Detected cycles in build graph!\n' + cycles)
    logger.info('Finished parsing build graph with {} nodes and {} edges',
                build_context.target_graph.order(),
                build_context.target_graph.size())


def write_dot(build_context, conf: Config, out_f):
    """Write build graph in dot format to `out_f` file-like object."""
    out_f.write('strict digraph  {\n')
    out_f.writelines('  "{}";\n'.format(node)
                     for node in build_context.target_graph.nodes)
    out_f.writelines('  "{}" -> "{}";\n'.format(u, v)
                     for u, v in build_context.target_graph.edges)
    out_f.write('}\n\n')


def topological_sort(graph: networkx.DiGraph):
    yield from stable_reverse_topological_sort(graph)


def get_descendants(graph: networkx.DiGraph, source):
    """Return all nodes reachable from `source` in `graph`."""
    return dag.descendants(graph, source)

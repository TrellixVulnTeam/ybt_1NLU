# -*- coding: utf-8 -*-

# Copyright 2016 Yowza Ltd. All rights reserved
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
yabt Ruby Builders
~~~~~~~~~~~~~~~~~~

:author: Itamar Ostricher
"""


from ..extend import (
    PropType as PT, register_build_func, register_builder_sig,
    register_manipulate_target_hook)
from ..utils import yprint


register_builder_sig(
    'GemPackage',
    [('package', PT.str),
     ('version', PT.str, None),
     ])


def format_gem_specifier(target):
    # TODO(itamar): support pinned version
    # this should be the way to specify a version for a ruby gem:
    # http://stackoverflow.com/questions/17026441/how-to-install-a-specific-version-of-a-ruby-gem
    # but it doesn't work...
    # if target.props.version:
    #     return "{0.package}:'{0.version}'".format(target.props)
    return '{0.package}'.format(target.props)


@register_build_func('GemPackage')
def gem_package_builder(build_context, target):
    yprint(build_context.conf, 'Fetch and cache Gem package', target)


@register_manipulate_target_hook('GemPackage')
def gem_package_manipulate_target(build_context, target):
    target.tags.add('gem-installable')

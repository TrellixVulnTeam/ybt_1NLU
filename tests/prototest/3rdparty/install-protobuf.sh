#!/bin/sh

set -eo
cd protobuf-2.6.1/
./configure
make
make check
make install
ldconfig /usr/local/lib

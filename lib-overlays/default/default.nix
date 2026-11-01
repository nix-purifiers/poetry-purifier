# SPDX-License-Identifier: MIT
#
# The `poetry-purifier` library namespace. It holds nothing: the builder
# needs a package set, so it is the package `buildPoetryProject` that the
# default package overlay adds.
{ ... }:
{

  overlay = final: prev: {
    poetry-purifier = prev.poetry-purifier or { };
  };

}

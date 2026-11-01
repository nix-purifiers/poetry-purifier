"""Usage: hello [--name=<name>]"""

from importlib.metadata import version

import attrs
import charset_normalizer
import git_package_example
import requests
import six

from docopt import docopt
from greeting import greet


def main() -> None:
    arguments = docopt(__doc__)
    name = arguments["--name"] or "world"
    print(
        f"{greet(name)} (requests {version(requests.__name__)},"
        f" charset-normalizer {charset_normalizer.__version__},"
        f" six {six.__version__},"
        f" git-package-example {git_package_example.__name__},"
        f" attrs {attrs.__version__})"
    )

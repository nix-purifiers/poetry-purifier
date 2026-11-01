"""Usage: hello [--name=<name>]"""

import charset_normalizer
import requests

from docopt import docopt


def main() -> None:
    arguments = docopt(__doc__)
    name = arguments["--name"] or "world"
    print(
        f"hello, {name} (requests {requests.__version__},"
        f" charset-normalizer {charset_normalizer.__version__})"
    )

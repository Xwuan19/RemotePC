"""Test suite cua RemotePC (unittest, khong can cai them gi).

Chay:  python -m unittest discover -s tests -t .
"""

import logging

# Log cua agent rat on khi test (nhieu canh bao du kien) -> chi giu loi nghiem trong
logging.getLogger("remotepc").setLevel(logging.CRITICAL)
logging.getLogger("remotepc").addHandler(logging.NullHandler())

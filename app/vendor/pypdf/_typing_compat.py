"""Small local typing compatibility layer for vendored pypdf.

FulfillCore ships pypdf inside ``app/vendor`` so PDF work does not depend on
network/pip installs.  Some supported server hosts still run Python 3.10,
where ``typing.Self`` is unavailable.  pypdf normally imports that symbol from
``typing_extensions``.  This module keeps the vendored package self-contained
without placing a partial ``typing_extensions`` module on the global import
path.
"""
from typing import TypeVar

try:
    from typing import Self as Self
except ImportError:  # Python < 3.11
    Self = TypeVar("Self")

try:
    from typing import TypeAlias as TypeAlias
except ImportError:  # Python < 3.10
    TypeAlias = object

try:
    from typing import TypeGuard as TypeGuard
except ImportError:  # Python < 3.10
    class TypeGuard:
        def __class_getitem__(cls, item):
            return bool

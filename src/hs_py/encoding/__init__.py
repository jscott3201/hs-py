"""Haystack encoding formats.

Provides JSON, Zinc, Trio, and CSV encoding/decoding.

- **JSON**: ``from hs_py.encoding.json import ...`` (v3 and v4)
- **Zinc**: ``from hs_py.encoding.zinc import ...`` (grid text format)
- **Trio**: ``from hs_py.encoding.trio import ...`` (record text format)
- **CSV**: ``from hs_py.encoding.csv import ...`` (lossy grid export)

For convenience, the most common JSON functions are re-exported directly
from this package. Zinc, Trio, and CSV functions should be imported from
their respective modules to avoid name collisions.
"""

from hs_py.encoding.csv import encode_grid as encode_csv
from hs_py.encoding.json import (
    JsonVersion,
    decode_grid,
    decode_grid_dict,
    decode_val,
    encode_grid,
    encode_val,
)
from hs_py.encoding.trio import encode_trio, parse_trio, parse_zinc_val

__all__ = [
    "JsonVersion",
    "decode_grid",
    "decode_grid_dict",
    "decode_val",
    "encode_csv",
    "encode_grid",
    "encode_trio",
    "encode_val",
    "parse_trio",
    "parse_zinc_val",
]

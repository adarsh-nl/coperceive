from collections.abc import Sequence
from typing import Any

from pyarrow import Table

def read_table(source: Any, columns: Sequence[str] | None = None) -> Table: ...
def write_table(table: Table, where: Any) -> None: ...

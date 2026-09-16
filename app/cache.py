"""요청 내용만 키로 쓰는 프로세스 내 캐시.

대화 이력이나 사용자 식별자를 담지 않으므로 무상태 원칙(절대 원칙 3)과 충돌하지 않는다.
ponytail: 프로세스 안에서만 산다. 서버를 늘리면 적중률만 떨어지고 정확도는 그대로다.
"""

from collections import OrderedDict
from typing import Any


class Lru:
    def __init__(self, limit: int) -> None:
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._limit = limit

    def get(self, key: str) -> Any | None:
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: str, value: Any) -> None:
        self._items[key] = value
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

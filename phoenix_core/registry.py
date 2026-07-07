from __future__ import annotations

from typing import Any, Dict, List, Type

from .interfaces import Engine


class EngineNotRegisteredError(KeyError):
    pass


class EngineRegistry:
    _registry: Dict[str, Dict[str, Type[Engine]]] = {}

    @classmethod
    def register(cls, slot: str, name: str):
        def decorator(engine_cls: Type[Engine]) -> Type[Engine]:
            slot_map = cls._registry.setdefault(slot, {})
            if name in slot_map:
                raise ValueError(
                    f"이미 등록된 엔진입니다: slot='{slot}', name='{name}' "
                    f"(기존: {slot_map[name].__name__}, 신규: {engine_cls.__name__})"
                )
            slot_map[name] = engine_cls
            return engine_cls
        return decorator

    @classmethod
    def get(cls, slot: str, name: str, **configure_kwargs: Any) -> Engine:
        slot_map = cls._registry.get(slot)
        if slot_map is None or name not in slot_map:
            available = list(slot_map.keys()) if slot_map else []
            raise EngineNotRegisteredError(
                f"등록되지 않은 엔진입니다: slot='{slot}', name='{name}'. 사용 가능한 이름: {available}"
            )
        instance = slot_map[name]()
        instance.configure(**configure_kwargs)
        return instance

    @classmethod
    def list_slot(cls, slot: str) -> List[str]:
        return list(cls._registry.get(slot, {}).keys())

    @classmethod
    def list_all(cls) -> Dict[str, List[str]]:
        return {slot: list(names.keys()) for slot, names in cls._registry.items()}

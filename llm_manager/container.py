from __future__ import annotations

import asyncio
import inspect
import typing
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class CircularDependencyError(Exception):
    pass


class ServiceNotFoundError(Exception):
    pass


class Container:
    def __init__(self):
        self._factories: dict[type, Callable] = {}
        self._instances: dict[type, Any] = {}
        self._singletons: set[type] = set()
        self._started: set[type] = set()
        self._dependency_graph: dict[type, list[type]] = {}

    def register(self, interface: type[T], factory: Callable[..., T], singleton: bool = True) -> None:
        self._factories[interface] = factory
        if singleton:
            self._singletons.add(interface)
        self._dependency_graph[interface] = self._get_dependencies(factory)

    def register_instance(self, interface: type[T], instance: T) -> None:
        self._instances[interface] = instance
        self._singletons.add(interface)
        self._dependency_graph[interface] = []

    def resolve(self, interface: type[T]) -> T:
        if interface in self._instances:
            return self._instances[interface]

        if interface not in self._factories:
            raise ServiceNotFoundError(f"No service registered for {interface.__name__}")

        if interface in self._singletons:
            instance = self._instances.get(interface)
            if instance is not None:
                return instance

        factory = self._factories[interface]
        kwargs = self._resolve_kwargs(factory)
        instance = factory(**kwargs)

        if interface in self._singletons:
            self._instances[interface] = instance

        return instance

    async def start_all(self) -> None:
        self._check_circular_dependencies()
        order = self._topological_sort()
        for svc_type in order:
            if svc_type in self._started:
                continue
            instance = self.resolve(svc_type)
            if hasattr(instance, "on_start") and callable(instance.on_start):
                result = instance.on_start()
                if asyncio.iscoroutine(result):
                    await result
            self._started.add(svc_type)

    async def stop_all(self) -> None:
        order = list(reversed(self._topological_sort()))
        for svc_type in order:
            if svc_type not in self._started:
                continue
            instance = self._instances.get(svc_type)
            if instance and hasattr(instance, "on_stop") and callable(instance.on_stop):
                result = instance.on_stop()
                if asyncio.iscoroutine(result):
                    await result
            self._started.discard(svc_type)

    def _get_type_hints(self, factory: Callable) -> dict[str, type]:
        target = factory.__init__ if isinstance(factory, type) else factory
        try:
            hints = typing.get_type_hints(target)
            if hints:
                return hints
        except Exception:
            pass

        sig = inspect.signature(factory)
        result = {}
        for name, param in sig.parameters.items():
            ann = param.annotation
            if isinstance(ann, type) and ann is not inspect.Parameter.empty:
                result[name] = ann
        return result

    def _get_dependencies(self, factory: Callable) -> list[type]:
        hints = self._get_type_hints(factory)
        sig = inspect.signature(factory)
        deps = []
        for name, param in sig.parameters.items():
            if param.default is not inspect.Parameter.empty:
                continue
            ann = hints.get(name)
            if isinstance(ann, type) and ann is not inspect.Parameter.empty:
                deps.append(ann)
        return deps

    def _resolve_kwargs(self, factory: Callable) -> dict[str, Any]:
        hints = self._get_type_hints(factory)
        sig = inspect.signature(factory)
        kwargs = {}
        for name, param in sig.parameters.items():
            if param.default is not inspect.Parameter.empty:
                continue
            ann = hints.get(name)
            if isinstance(ann, type) and ann is not inspect.Parameter.empty and (ann in self._factories or ann in self._instances):
                kwargs[name] = self.resolve(ann)
        return kwargs

    def _check_circular_dependencies(self) -> None:
        visited: set[type] = set()
        path: set[type] = set()

        def visit(node: type):
            if node in path:
                chain = " -> ".join(t.__name__ for t in path) + f" -> {node.__name__}"
                raise CircularDependencyError(f"Circular dependency detected: {chain}")
            if node in visited:
                return
            path.add(node)
            for dep in self._dependency_graph.get(node, []):
                visit(dep)
            path.remove(node)
            visited.add(node)

        for node in self._dependency_graph:
            visit(node)

    def _topological_sort(self) -> list[type]:
        visited: set[type] = set()
        order: list[type] = []

        def visit(node: type):
            if node in visited:
                return
            visited.add(node)
            for dep in self._dependency_graph.get(node, []):
                visit(dep)
            order.append(node)

        for node in self._dependency_graph:
            visit(node)
        return order

from typing import Callable, Type

from fastapi import Depends, Request


def get_service(service_type: type) -> Callable:
    def dependency(request: Request):
        return request.app.state.container.resolve(service_type)
    return dependency

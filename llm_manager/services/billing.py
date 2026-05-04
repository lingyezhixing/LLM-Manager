from __future__ import annotations

import logging

from llm_manager.services.base import BaseService
from llm_manager.container import Container

logger = logging.getLogger(__name__)


class BillingService(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)

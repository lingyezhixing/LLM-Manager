from llm_manager.config.models import AppConfig
from llm_manager.events import Event


class ConfigChanged(Event):
    def __init__(self, old: AppConfig, new: AppConfig):
        super().__init__()
        self.old = old
        self.new = new

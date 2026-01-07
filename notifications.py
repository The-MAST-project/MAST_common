from pydantic import BaseModel
from common.config import Config
import socket
from typing import Literal

local_machine: str | None = None
if not local_machine:
    local_machine = socket.gethostname().split('.')[0]
    if '-spec' in local_machine:
        local_machine = 'spec'
    elif '-control' in local_machine:
        local_machine = 'controller'

class Notifier(BaseModel):
    notification_site: str = Config().local_site.name
    notification_machine: str = local_machine
    notification_keys: list[str] = []
    notification_value: list[str] | str | None = None
    notification_element: Literal['badge', 'text'] = 'text'

    def ui_notification(self, value: list[str] | str | None = None, element: Literal['badge', 'text'] = 'text'):
        self.notification_value = value
        self.notification_element = element
        return self

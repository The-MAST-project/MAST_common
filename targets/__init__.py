import tomlkit
from typing import List, Optional

from datetime import datetime, timezone

from .task import Task, Event
from .model import TaskModel
import os
from common.utils import path_maker
from ulid import ULID
from copy import deepcopy

top_folder = path_maker.make_tasks_folder()

folders = {}
for sub in ['pending', 'completed', 'in-progress']:
    if not sub in folders:
        folders[sub] = os.path.join(top_folder, sub)
        os.makedirs(folders[sub], exist_ok=True)
del sub


def loads(s, file_name: str | None = None) -> Task:
    toml = tomlkit.loads(s)
    return Task(TaskModel(**toml), toml, os.path.realpath(file_name))

def load(fp, _from_template: bool = False) -> Task:
    return loads(fp.read(), fp.name)

def load_folder(which: str) -> Optional[List[Task]]:
    """
    Gets all the targets in the subfolder
    :param which: folder containing 'TSK_' files
    :return: a list of Task objects, loaded from the files
    """
    if not which in folders:
        return None

    tasks: List[Task] = []
    with os.scandir(folders[which]) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.startswith('TSK_'):
                try:
                    with open(entry.path, 'rb') as f:
                        t = load(f)
                        tasks.append(t)
                except ValueError as e:
                    print(f"Invalid task in '{entry.path}', {e}, skipping ...")
                    continue
    return tasks

def new():
    def replace_none_strings_in_toml(data):
        if isinstance(data, dict):  # Handle dictionary-like structures
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    replace_none_strings_in_toml(value)  # Recursive call
                elif value == 'None':
                    data[key] = None
        elif isinstance(data, list):  # Handle list-like structures
            for i in range(len(data)):
                if isinstance(data[i], (dict, list)):
                    replace_none_strings_in_toml(data[i])  # Recursive call
                elif data[i] == 'None':
                    data[i] = None
        return data

    def toml_to_dict(data):
        if isinstance(data, dict):  # Recursively process dictionaries
            return {key: toml_to_dict(value) for key, value in data.items()}
        elif isinstance(data, list):  # Recursively process lists
            return [toml_to_dict(item) for item in data]
        else:  # Return non-dictionary/list items as-is
            return None if data == 'None' else data

    file = os.path.join(top_folder, 'template.toml')
    if not os.path.exists(file):
        raise Exception("TSK_template folder not found")

    with open(file, 'r') as f:
        toml = tomlkit.load(f)
    if not toml:
        raise Exception(f"Could not load targets template from '{file}'")
    ret = toml_to_dict(toml)
    ret['settings']['ulid'] = str(ULID())
    ret['file_name'] = os.path.join(top_folder, 'pending', f"TSK_{ret['settings']['ulid']}.toml")
    ret['events'] = [{'date': datetime.now(timezone.utc).isoformat(), 'desc': 'created from template'}]

    return ret
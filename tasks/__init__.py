# import tomlkit
# from typing import List, Optional
#
# from datetime import datetime, timezone
#
# from .target import Target, Event
# from .models import TargetModel
# import os
# from common.utils import path_maker
# from common.tasks.models import AssignedTaskModel
#
# top_folder = path_maker.make_tasks_folder()
#
# folders = {}
# for sub in ['pending', 'completed', 'in-progress', 'assigned']:
#     if not sub in folders:
#         folders[sub] = os.path.join(top_folder, sub)
#         os.makedirs(folders[sub], exist_ok=True)
# del sub
#
#
# def loads(s, file_name: Optional[str] = None) -> Target:
#     toml_doc = tomlkit.loads(s)
#     return Target(TargetModel(**toml_doc), toml_doc, os.path.realpath(file_name))
#
#
# def load(fp, _from_template: bool = False) -> Target:
#     return loads(fp.read(), fp.name)
#
#
# def load_folder(folder_name: str) -> Optional[List[Target]]:
#     """
#     Gets all the tasks in the subfolder
#     :param folder_name: folder containing 'TSK_' files
#     :return: a list of Target objects, loaded from the files
#     """
#     if not folder_name in folders:
#         return None
#
#     tasks: List[Target] = []
#     with os.scandir(folders[folder_name]) as entries:
#         for entry in entries:
#             if entry.is_file() and entry.name.startswith('TSK_'):
#                 try:
#                     with open(entry.path, 'rb') as f:
#                         t = load(f)
#                         tasks.append(t)
#                 except ValueError as e:
#                     print(f"Invalid task in '{entry.path}', {e}, skipping ...")
#                     continue
#     return tasks
#
#
# def new():
#     def replace_none_strings_in_toml(data):
#         if isinstance(data, dict):  # Handle dictionary-like structures
#             for key, value in data.items():
#                 if isinstance(value, (dict, list)):
#                     replace_none_strings_in_toml(value)  # Recursive call
#                 elif value == 'None':
#                     data[key] = None
#         elif isinstance(data, list):  # Handle list-like structures
#             for i in range(len(data)):
#                 if isinstance(data[i], (dict, list)):
#                     replace_none_strings_in_toml(data[i])  # Recursive call
#                 elif data[i] == 'None':
#                     data[i] = None
#         return data
#
#     def toml_to_dict(data):
#         if isinstance(data, dict):  # Recursively process dictionaries
#             return {key: toml_to_dict(value) for key, value in data.items()}
#         elif isinstance(data, list):  # Recursively process lists
#             return [toml_to_dict(item) for item in data]
#         else:  # Return non-dictionary/list items as-is
#             return None if data == 'None' else data
#
#     file = os.path.join(top_folder, 'template.toml')
#     if not os.path.exists(file):
#         raise Exception("TSK_template folder not found")
#
#     with open(file, 'r') as f:
#         toml = tomlkit.load(f)
#     if not toml:
#         raise Exception(f"Could not load tasks template from '{file}'")
#     ret = toml_to_dict(toml)
#     # ret['settings']['ulid'] = str(ULID.new())
#     ret['file_name'] = os.path.join(top_folder, 'pending', f"TSK_{ret['settings']['ulid']}.toml")
#     ret['events'] = [{'date': datetime.now(timezone.utc).isoformat(), 'desc': 'created from template'}]
#
#     return ret

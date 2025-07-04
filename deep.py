def deep_dict_update(original: dict, update: dict):
    """
    Recursively update a dictionary with nested dictionaries.
    :param original: The original dictionary to be updated, in place.
    :param update: The dictionary with updates.
    """
    for key, value in update.items():
        if isinstance(value, dict) and key in original:
            # If the value is a dict and the key exists in the original dict,
            # perform a deep update
            deep_dict_update(original[key], value)
        else:
            # Otherwise, update or add the key-value pair to the original dict
            original[key] = value


def deep_dict_difference(old: dict, new: dict):
    if isinstance(old, dict) and isinstance(new, dict):
        difference = {}
        all_keys = set(old.keys()).union(new.keys())
        for key in all_keys:
            if key in old and key in new:
                diff = deep_dict_difference(old[key], new[key])
                if diff is not None:
                    difference[key] = diff
            elif key in new:
                difference[key] = new[key]
            elif key in old:
                difference[key] = old[key]
        return difference if difference else None
    elif isinstance(old, list) and isinstance(new, list):
        length = max(len(old), len(new))
        difference = []
        for i in range(length):
            old_val = old[i] if i < len(old) else None
            new_val = new[i] if i < len(new) else old_val
            diff = deep_dict_difference(old_val, new_val)
            difference.append(diff)
        return difference if any(item is not None for item in difference) else None
    else:
        return new if old != new else None


def deep_dict_is_empty(d):
    if not isinstance(d, dict | list):
        return False  # Not a dictionary or list

    if not d:
        return True  # Dictionary or list is empty

    if isinstance(d, list):
        return all(deep_dict_is_empty(item) for item in d)

    for value in d.values():
        if isinstance(value, dict | list):
            if not deep_dict_is_empty(value):
                return False  # Nested dictionary or list is not empty
        elif value:
            return False  # Non-empty value found

    return True  # All nested dictionaries and lists are empty

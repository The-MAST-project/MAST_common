"""Stack-frame helpers with no `common` imports of their own.

`function_name` lives here, not in `common.utils`, because `common.config` needs it and
`common.utils` builds a `Filer` at import -- which reads the bootstrap config through
`common.config`. Imported from `common.utils`, that was a cycle: `utils` half-initialised
when `config` asked it for the name. `common.utils` re-exports it.
"""

import inspect


def function_name():
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        return "UnknownFunction"
    try:
        caller_frame = frame.f_back
        func_name = caller_frame.f_code.co_name

        module = inspect.getmodule(caller_frame)
        class_name = None
        if "self" in caller_frame.f_locals:
            class_name = caller_frame.f_locals["self"].__class__.__name__
        elif "cls" in caller_frame.f_locals:
            class_name = caller_frame.f_locals["cls"].__name__
        ret = ""
        if module:
            ret += f"[{module.__name__}]."
        if class_name:
            ret += f"{class_name}."
        ret += func_name
        return ret
    finally:
        del frame  # avoid reference cycles

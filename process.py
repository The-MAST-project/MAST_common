import psutil
import logging
import re
import subprocess
import time


def find_process(name: str | None = None, patt: str = None, pid: int | None = None) -> psutil.Process:
    """
    Searches for a running process either by a pattern in the command line or by pid

    Parameters
    ----------
    name
    patt
    pid

    Returns
    -------

    """
    ret = None
    for proc in psutil.process_iter(['name', 'status', 'pid', 'cmdline']):
        if name and proc.info['name'] == name:
            ret = proc
            break
        elif patt:
            patt = re.compile(patt, re.IGNORECASE)
            for arg in proc.info['cmdline']:
                if patt.search(arg):
                    ret = proc
                    break
        elif pid and proc.info['pid'] == pid:
            ret = proc
            break

    return ret


def ensure_process_is_running(name: str | None = None, pattern: str | None = None, cmd: str = None,
                              logger: logging.Logger | None = None, env: dict = None,
                              cwd: str = None, shell: bool = False) -> psutil.Process:
    """
    Makes sure a process containing 'pattern' in the command line exists.
    If it's not running, it starts one using 'cmd' and waits till it is running

    Parameters
    ----------
    name:
    pattern: str The pattern to lookup in the command line of processes
    cmd: str - The command to use to start a new process
    env: dict - An environment dictionary
    cwd: str - Current working directory
    shell: bool - Run the cmd in a shell
    logger

    Returns
    -------

    """
    p = find_process(name, pattern)
    if p is not None:
        logger.debug(f'A process with {name=} or {pattern=} in the commandline exists, pid={p.pid}')
        return p

    try:
        # It's not running, start it
        if shell:
            process = subprocess.Popen(args=cmd, env=env, shell=True, cwd=cwd, stderr=None, stdout=None)
        else:
            args = cmd.split()
            process = subprocess.Popen(args, env=env, executable=args[0], cwd=cwd, stderr=None, stdout=None)
        logger.info(f"started process (pid={process.pid}) with cmd: '{cmd}'")
    except Exception as ex:
        pass

    p = None
    while not p:
        p = find_process(name, pattern)
        if p:
            return p
        logger.info(f"waiting for proces with {name=} or {pattern=} to run")
        time.sleep(1)

import psutil
import logging
import re
import subprocess
import time


def find_process(patt: str = None, pid: int | None = None) -> psutil.Process:
    """
    Searches for a running process either by a pattern in the command line or by pid

    Parameters
    ----------
    patt
    pid

    Returns
    -------

    """
    ret = None
    if patt:
        patt = re.compile(patt, re.IGNORECASE)
        for proc in psutil.process_iter():
            try:
                argv = proc.cmdline()
                for arg in argv:
                    if patt.search(arg) and proc.status() == psutil.STATUS_RUNNING:
                        ret = proc
                        break
            except psutil.AccessDenied:
                continue
    elif pid:
        proc = [(x.pid == pid and x.status() == psutil.STATUS_RUNNING) for x in psutil.process_iter()]
        ret = proc[0]

    return ret


def ensure_process_is_running(pattern: str, cmd: str, logger: logging.Logger, env: dict = None,
                              cwd: str = None, shell: bool = False) -> psutil.Process:
    """
    Makes sure a process containing 'pattern' in the command line exists.
    If it's not running, it starts one using 'cmd' and waits till it is running

    Parameters
    ----------
    pattern: str The pattern to lookup in the command line of processes
    cmd: str - The command to use to start a new process
    env: dict - An environment dictionary
    cwd: str - Current working directory
    shell: bool - Run the cmd in a shell
    logger

    Returns
    -------

    """
    p = find_process(pattern)
    if p is not None:
        logger.debug(f'A process with pattern={pattern} in the commandline exists, pid={p.pid}')
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
        p = find_process(pattern)
        if p:
            return p
        logger.info(f"waiting for proces with pattern='{pattern}' to run")
        time.sleep(1)

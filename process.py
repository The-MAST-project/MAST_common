import threading
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


def log_stream(stream, logger, log_level):
    """
    Reads a stream line by line and logs it.
    """
    for line in iter(stream.readline, b''):
        logger.log(log_level, line.decode().strip())
    stream.close()


def ensure_process_is_running(name: str | None = None, pattern: str | None = None, cmd: str = None,
                              logger: logging.Logger | None = None, env: dict = None,
                              cwd: str = None, shell: bool = False,
                              log_stdout_and_stderr: bool = False) -> psutil.Process:
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
    log_stdout_and_stderr

    Returns
    -------

    """
    p = find_process(name, pattern)
    if p is not None:
        if name:
            logger.debug(f'A process with {name=}exists, pid={p.pid}')
        elif pattern:
            logger.debug(f'A process with {pattern=} in the commandline exists, pid={p.pid}')
        return p

    try:
        # It's not running, start it
        stdout = subprocess.PIPE if log_stdout_and_stderr else subprocess.DEVNULL
        stderr = subprocess.PIPE if log_stdout_and_stderr else subprocess.DEVNULL

        if shell:
            process = subprocess.Popen(args=cmd, env=env, shell=True, cwd=cwd, stderr=stderr, stdout=stdout)
        else:
            args = cmd.split()
            process = subprocess.Popen(args, env=env, executable=args[0], cwd=cwd, stderr=stderr, stdout=stdout)
        if log_stdout_and_stderr:
            threading.Thread(name='stdout-logger', target=log_stream,
                             args=(process.stdout, logger, logging.INFO)).start()
            threading.Thread(name='stderr-logger', target=log_stream,
                             args=(process.stderr, logger, logging.ERROR)).start()

        logger.info(f"started process (pid={process.pid}) with cmd: '{cmd}' in {cwd=}")
    except Exception as ex:
        pass

    p = None
    while not p:
        p = find_process(name, pattern)
        if p:
            return p
        if name:
            logger.info(f"waiting for process with {name=} to run")
        else:
            logger.info(f"waiting for process with {pattern=} to run")
        time.sleep(1)

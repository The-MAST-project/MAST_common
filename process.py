import contextlib
import logging
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from threading import Thread

import psutil


def find_process(
    name: str | None = None, patt: str | None = None, pid: int | None = None
) -> psutil.Process | None:
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
    for proc in psutil.process_iter(["name", "status", "pid", "cmdline"]):
        if name and proc.info["name"] == name:
            ret = proc
            break
        elif patt:
            _patt: re.Pattern = re.compile(patt, re.IGNORECASE) # type: ignore
            for arg in proc.info["cmdline"]:
                if _patt.search(arg):
                    ret = proc
                    break
        elif pid and proc.info["pid"] == pid:
            ret = proc
            break

    return ret


def log_stream(label: str, stream, logger, log_level):
    """
    Reads a stream line by line and logs it.
    """
    for line in iter(stream.readline, b""):
        logger.log(log_level, "[" + label + "]: " + line.decode().strip())
    stream.close()


def ensure_process_is_running(
    name: str | None = None,
    pattern: str | None = None,
    cmd: str | None = None,
    logger: logging.Logger | None = None,
    env: dict | None = None,
    cwd: str | None = None,
    shell: bool = False,
    log_stdout_and_stderr: bool = False,
) -> psutil.Process | None:
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
            if logger:
                logger.debug(f"A process with {name=}exists, pid={p.pid}")
        elif pattern and logger:
            logger.debug(
                f"A process with {pattern=} in the commandline exists, pid={p.pid}"
            )
        return p

    try:
        # It's not running, start it
        stdout = subprocess.PIPE if log_stdout_and_stderr else subprocess.DEVNULL
        stderr = subprocess.PIPE if log_stdout_and_stderr else subprocess.DEVNULL
        cmd = Path(cmd).as_posix() if cmd else None
        if not cmd:
            raise ValueError("ensure_process_is_running: cmd must be set")
        executable = cmd.split("/")[-1].replace('"', "") if "/" in cmd else cmd.split(" ")[0]

        if shell:
            process = subprocess.Popen(
                args=cmd,
                env=env,
                shell=True,
                cwd=cwd, stderr=stderr, stdout=stdout
            )
        else:
            args = cmd.split()
            process = subprocess.Popen(
                args, env=env, executable=args[0], cwd=cwd, stderr=stderr, stdout=stdout
            )
        if log_stdout_and_stderr:
            threading.Thread(
                name="stdout-logger",
                target=log_stream,
                args=(executable, process.stdout, logger, logging.INFO),
            ).start()
            threading.Thread(
                name="stderr-logger",
                target=log_stream,
                args=(executable, process.stderr, logger, logging.ERROR),
            ).start()

        if logger:
            logger.info(f"started process (pid={process.pid}) with cmd: '{cmd}' in {cwd=}")
    except Exception:
        pass

    p = None
    while not p:
        p = find_process(name, pattern)
        if p:
            return p
        if logger:
            if name:
                logger.info(f"waiting for process with {name=} to run")
            else:
                logger.info(f"waiting for process with {pattern=} to run")
        time.sleep(1)


class WatchedProcess:

    def __init__(
        self,
        command_pattern: str | None = None,
        command: str | None = None,
        logger: logging.Logger | None = None,
        env: dict | None = None,
        cwd: str | None = None,
        shell: bool = False,
    ):

        self.command_pattern: str | None = command_pattern
        self.command: str | None = command
        self.logger: logging.Logger | None = logger
        self.env: dict | None = env
        self.cwd: str | None = cwd
        self.shell: bool = shell
        self.process: subprocess.Popen | None = None
        self.logging: bool = False
        self._terminate: bool = False

    def start(self):
        if not self.command:
            if not self.command_pattern:
                raise ValueError("WatchedProcess: command or command_pattern must be set")
            self.command = self.command_pattern
        #
        # First kill previous instances, if existent.
        # We want to log and monitor a newly created process
        #
        parts = shlex.split(self.command, posix=False)
        executable = parts[0].split("/")[-1].replace('"', "")
        kill_process_by_name(executable)

        #
        # Now run the command in a process
        #
        stdout = subprocess.PIPE if self.logger else subprocess.DEVNULL
        stderr = subprocess.PIPE if self.logger else subprocess.DEVNULL
        args = self.command.split()
        executable = args[0]
        del args[0]

        try:
            if self.shell:
                self.process = subprocess.Popen(
                    args=self.command,
                    env=self.env,
                    shell=True,
                    cwd=self.cwd,
                    stderr=stderr,
                    stdout=stdout,
                )
            else:
                self.process = subprocess.Popen(
                    args=args,
                    env=self.env,
                    executable=executable,
                    cwd=self.cwd,
                    stderr=stderr,
                    stdout=stdout,
                )
        except FileNotFoundError as ex:
            print(f"WatchedProcess.start: exception: {ex}")
            return

        Thread(target=self.watcher).start()
        if self.logger:
            self.logging = True
            Thread(target=self.log_stream, args=[stdout]).start()
            Thread(target=self.log_stream, args=[stderr]).start()

    def log_stream(self, stream):
        """
        Reads lines from a stream and logs them
        """
        if stream == -1:
            # This is a special case for subprocess.DEVNULL
            return
        if not self.logger:
            return
        for line in iter(stream.read_line, b""):
            self.logger.info(line.decode().strip())
        stream.close()

    def terminate(self):
        self._terminate = True
        if self.process is not None:
            self.process.kill()

    def watcher(self):
        while not self._terminate:
            if self.process is not None:
                exit_code = self.process.wait()
                if self.logger:
                    self.logger.info(f"Process {self.process.pid} exited with {exit_code=}")
            self.logging = False  # stop logging threads
            if self._terminate:
                return

            if self.logger:
                self.logger.info(f"Starting {self.command} ...")
            self.start()


def kill_process_by_name(name):
    found = False
    # print('=======================================')
    for proc in psutil.process_iter(["name", "pid"]):
        # print(f"looking at proc {proc.info['name']}, pid: {proc.pid}")
        if name.lower() in proc.info["name"].lower():
            found = True
            print(f"Killing PID {proc.pid} ({proc.info['name']})")
            with contextlib.suppress(Exception):
                proc.kill()
            gone = []
            while proc not in gone:
                gone, alive = psutil.wait_procs([proc])
                time.sleep(0.5)

    if not found:
        # print(f"No process named '{name}' found.")
        return

    # Wait for processes to die
    while True:
        still_alive = [
            p
            for p in psutil.process_iter(["name"])
            if p.info["name"] and name.lower() in p.info["name"].lower()
        ]
        if not still_alive:
            # print(f"All '{name.lower()}' processes terminated.")
            return
        time.sleep(0.5)


if __name__ == "__main__":
    WatchedProcess(
        command='"C:/Program Files (x86)/PHDGuiding2/phd2.exe"', shell=True
    ).start()
    while True:
        pass

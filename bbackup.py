#!/usr/bin/env python3

# Copyright 2022 Pontus Lurcock (pont -at- talvi.net)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import os
import pathlib
import subprocess
import sys
from typing import AnyStr
import signal

import yaml


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    parser = argparse.ArgumentParser("Perform borg backups")
    parser.add_argument("--dry-run", "-d", action="store_true")
    parser.add_argument(
        "config_dir", type=str, help="Configuration and log directory"
    )
    args = parser.parse_args()
    config_path = pathlib.Path(args.config_dir)
    exit_code = do_backup(config_path, args.dry_run)
    sys.exit(exit_code)


def handle_signal(sig, stack_frame):
    log(
        f"\nBackup interrupted by signal {sig} "
        f"at line {stack_frame.f_code.co_filename}:{stack_frame.f_lineno}!\n"
        f"Exiting."
    )
    sys.exit(2)


def do_backup(config_dir: pathlib.Path, dry_run: bool) -> int:
    exclude_file = config_dir.parent.joinpath("exclude.txt")
    global_config = read_config(config_dir.parent.joinpath("config.yaml"))
    repo_config = read_config(config_dir.joinpath("config.yaml"))
    borg_repo = repo_config["repo-path"]
    borg_path = repo_config.get("borg-path", "borg")

    extra_params = (
        ["--remote-path", repo_config["remote-path"]]
        if "remote-path" in repo_config
        else []
    ) + (["--dry-run"] if dry_run else [])
    log_file = config_dir.joinpath("logs", "log")
    borg_env = dict(
        os.environ,
        BORG_PASSCOMMAND=(
            "secret-tool lookup borg-config %s" % config_dir.name
        ),
        BORG_RELOCATED_REPO_ACCESS_IS_OK="yes",
    )
    if "variable-setter-script-path" in global_config:
        for variable_name in "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS":
            borg_env[variable_name] = get_variable_from_shell_script(
                variable_name,
                os.path.expandvars(
                    os.path.expanduser(
                        global_config["variable-setter-script-path"]
                    )
                ),
            )
    source_dirs = repo_config.get(
        "source-directories", [pathlib.Path.home().as_posix()]
    )

    with open(log_file, "bw") as log_fh:
        # NB: create_args, prune_args, and logrotate_args below contain
        # arguments for the subprocess.Popen call, not just for the external
        # command.

        log("Starting backup to " + borg_repo)
        create_args = dict(
            args=[
                borg_path,
                "create",
                "--verbose",
                "--filter",
                "AMEx",
                "--list",
                "--stats",
                "--show-rc",
                "--compression",
                "auto,zstd",
                "--exclude-caches",
                "--exclude-from",
                exclude_file,
            ]
            + extra_params
            + [borg_repo + "::{hostname}-{now}"]
            + source_dirs,
            env=borg_env,
        )
        create_result = tee(create_args, log_fh)

        log("Pruning repository " + borg_repo)
        # Prune repository to 7 daily, 4 weekly and 6 monthly archives. NB: the
        # '{hostname}-' prefix limits pruning to this machine's archives.
        prune_args = dict(
            args=[
                borg_path,
                "prune",
                "--list",
                "--prefix",
                "{hostname}-",  # Important! (See above.)
                "--show-rc",
                "--keep-daily",
                "7",
                "--keep-weekly",
                "4",
                "--keep-monthly",
                "6",
            ]
            + extra_params
            + [borg_repo],
            env=borg_env,
        )
        prune_result = tee(prune_args, log_fh)

    log("Rotating logs")
    # logrotate, of course, is not run through tee, since it can hardly log
    # its output to the log that it's currently rotating.
    logrotate_extra_args = ["--debug"] if dry_run else []
    logrotate_args = dict(
        args=[
            global_config.get("logrotate-path", "/usr/sbin/logrotate"),
            "--verbose",
            "--state",
            "logrotate-state",
        ]
        + logrotate_extra_args
        + ["logrotate.conf"],
        cwd=config_dir,
    )
    logrotate_result = subprocess.run(**logrotate_args).returncode

    for step, returncode in [
        ("Backup", create_result),
        ("Prune", prune_result),
        ("Rotate logs", logrotate_result),
    ]:
        log("%s finished with return code %d." % (step, returncode))

    # use highest exit code as global exit code
    return max(create_result, prune_result, logrotate_result)


def read_config(config_dir: pathlib.Path) -> dict:
    config_file = config_dir.parent.joinpath("config.yaml")
    if os.path.isfile(config_file):
        with open(config_file, "r") as fh:
            config = yaml.safe_load(fh)
        return config
    else:
        return {}


def get_variable_from_shell_script(variable_name: str, script_path: str) -> str:
    """Get the value of SSH_AUTH_SOCK from a shell script that sets it

    :param variable_name: name of an environment variable set by a shell script
    :param script_path: path to shell script
    :return: value of variable as set by the shell script
    """

    # Per the subprocess documentation, "On POSIX with shell=True, the shell
    # defaults to /bin/sh". But even if this changes, any POSIX-compliant shell
    # should work here.

    result = subprocess.check_output(
        # "." is the POSIX-compliant version of bash's "source".
        # "echo -n" is not guaranteed by POSIX so we strip the newline instead.
        ". %s; echo $%s" % (script_path, variable_name),
        shell=True,
    )

    return result.strip().decode(sys.stdout.encoding)


def tee(subprocess_args: dict, fh) -> int:
    with subprocess.Popen(
        stderr=subprocess.STDOUT, stdout=subprocess.PIPE, **subprocess_args
    ) as popen:
        while True:
            # Keeping the type-checker happy is a little fiddly here. In
            # practice we expect that this will always be of type bytes.
            data: AnyStr = popen.stdout.read(1)
            if data == b"":
                break
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            fh.write(data)
        # The subprocess has finished writing, but we still want to wait
        # for it to complete and give a return code.
        popen.wait()
        return popen.returncode


def log(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    main()

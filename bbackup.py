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

import yaml


def main():
    # TODO trap SIGINT and SIGTERM like the bash script?
    # TODO run the set-ssh-auth-sock script to allow online backups
    #      with ssh-agent managing authentication. Though actually,
    #      might have to modify this system -- in the bash script
    #      we just source set-ssh-auth-sock, but we can't do that
    #      in python, at least not in the same way. Might be
    #      simplest just to write the value of the SSH_AUTH_SOCK
    #      to a file and read that. Ideally I'd like to
    #      write a file which can include a comment so I know what
    #      it is when I stumble across it. But actually I can
    #      just keep the one-line, one-value ssh-auth-sock in
    #      its own directory with a README explaining what it's for.
    #      Easier than e.g. using YAML for a one-value config :).

    parser = argparse.ArgumentParser("Perform borg backups")
    parser.add_argument("--dry-run", "-d", action="store_true")
    parser.add_argument(
        "config_dir", type=str, help="Configuration and log directory"
    )
    args = parser.parse_args()
    config_path = pathlib.Path(args.config_dir)
    exit_code = do_backup(config_path, args.dry_run)
    sys.exit(exit_code)


def do_backup(config_dir: pathlib.Path, dry_run: bool):
    exclude_file = config_dir.parent.joinpath("exclude.txt")
    config = read_global_config(config_dir)
    with open(config_dir.joinpath("repo-path.txt"), "r") as fh:
        borg_repo = fh.readline().strip()

    extra_params = ["--dry-run"] if dry_run else []
    log_file = config_dir.joinpath("logs", "log")
    borg_passcommand = "secret-tool lookup borg-config %s" % config_dir.name
    borg_env = dict(os.environ, BORG_PASSCOMMAND=borg_passcommand)
    if "ssh-auth-sock-script-path" in config:
        borg_env["SSH_AUTH_SOCK"] = get_ssh_auth_socket(
            os.path.expandvars(
                os.path.expanduser(config["ssh-auth-sock-script-path"])
            )
        )

    print("Starting backup to " + borg_repo)

    with open(log_file, "w") as log_fh:
        create_result = subprocess.run(
            args=[
                "borg",
                "create",
                "--verbose",
                "--filter",
                "AME-x",
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
            + [
                borg_repo + "::{hostname}-test-{now}",
                pathlib.Path.home().as_posix(),
            ],
            stderr=log_fh,
            env=borg_env,
        )

        print("Pruning repository " + borg_repo)

        # Prune repository to 7 daily, 4 weekly and 6 monthly archives. NB: the
        # '{hostname}-' prefix limits pruning to this machine's archives.

        prune_result = subprocess.run(
            args=[
                "borg",
                "prune",
                "--list",
                "--prefix",
                "{hostname}-" "--show-rc",  # Important! (See above.)
                "--keep-daily",
                "7",
                "--keep-weekly",
                "4",
                "--keep-monthly",
                "6",
            ]
            + extra_params
            + [borg_repo],
            stderr=log_fh,
            env=borg_env,
        )

    logrotate_extra_args = ["--debug"] if dry_run else []
    subprocess.run(
        args=["logrotate", "--verbose", "--state", "logrotate-state"]
        + logrotate_extra_args
        + ["logrotate.conf"],
        cwd=config_dir,
    )

    for r in create_result, prune_result:
        print(dict(create="Backup", prune="Prune")[r.args[1]], end="")
        print(" finished ", end="")
        if r.returncode == 0:
            print("successfully")
        elif r.returncode == 1:
            print("with warnings")
        else:
            print("with errors")

    # use highest exit code as global exit code
    return max(create_result.returncode, prune_result.returncode)


def read_global_config(config_dir):
    config_file = config_dir.parent.joinpath("config.yaml")
    if os.path.isfile(config_file):
        with open(config_file, "r") as fh:
            config = yaml.safe_load(fh)
        return config
    else:
        return {}


def get_ssh_auth_socket(script_path: str) -> str:
    """Get the value of SSH_AUTH_SOCK from a shell script that sets it

    :param script_path: path to shell script
    :return: value of SSH_AUTH_SOCK set by shell script
    """

    # Per the subprocess documentation, "On POSIX with shell=True, the shell
    # defaults to /bin/sh". But even if this changes, any POSIX-compliant shell
    # should work here.

    result = subprocess.check_output(
        # "." is the POSIX-compliant version of bash's "source".
        # "echo -n" is not guaranteed by POSIX so we strip the newline instead.
        ". %s; echo $SSH_AUTH_SOCK" % script_path,
        shell=True,
    )

    return result.strip().decode(sys.stdout.encoding)


if __name__ == "__main__":
    main()

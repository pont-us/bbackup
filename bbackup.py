#!/usr/bin/env python3

import subprocess
import pathlib
import argparse
import os
import sys

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
    
    parser = argparse.ArgumentParser('Perform borg backups')
    parser.add_argument('--dry-run', '-d', action='store_true')
    parser.add_argument('config_dir', type=str,
                        help='Configuration and log directory')
    args = parser.parse_args()
    config_path = pathlib.Path(args.config_dir)
    exit_code = do_backup(config_path, args.dry_run)
    sys.exit(exit_code)


def do_backup(config_dir: pathlib.Path, dry_run: bool):
    exclude_file = config_dir.parent.joinpath('exclude.txt')
    with open(config_dir.joinpath('repo-path.txt'), 'r') as fh:
        borg_repo = fh.readline().strip()
    
    extra_params = ['--dry-run'] if dry_run else []
    log_file = config_dir.joinpath('logs', 'log')
    borg_passcommand = f'secret-tool lookup borg-config {config_dir.name}'
    borg_env = dict(os.environ, BORG_PASSCOMMAND=borg_passcommand)

    print('Starting backup to ' + borg_repo)

    with open(log_file, 'w') as log_fh:
        create_result = subprocess.run(
            args=[
                'borg',
                'create',
                '--verbose',
                '--filter', 'AME-x',
                '--list',
                '--stats',
                '--show-rc',
                '--compression', 'auto,zstd',
                '--exclude-caches',
                '--exclude-from', exclude_file,
            ] + extra_params + [
                borg_repo + '::{hostname}-test-{now}',
                pathlib.Path.home().as_posix()
            ],
            stderr=log_fh,
            env=borg_env
        )
        
        print('Pruning repository ' + borg_repo)
    
        # Use the `prune` subcommand to maintain 7 daily, 4 weekly and 6
        # monthly archives of THIS machine. The '{hostname}-' prefix is very
        # important to limit prune's operation to this machine's archives and
        # not apply to other machines' archives also.
    
        prune_result = subprocess.run(
            args=[
                'borg',
                'prune',
                '--list',
                '--prefix', '{hostname}-'
                '--show-rc',
                '--keep-daily', '7',
                '--keep-weekly', '4',
                '--keep-monthly', '6',
            ] + extra_params + [borg_repo],
            stderr=log_fh,
            env=borg_env
        )
    
    logrotate_extra_args = ['--debug'] if dry_run else []
    subprocess.run(args=[
        'logrotate',
        '--verbose',
        '--state', 'logrotate-state'
        ] + logrotate_extra_args + [
        'logrotate.conf'
        ],
        cwd=config_dir
    )
    
    for r in create_result, prune_result:
        print(dict(create='Backup', prune='Prune')[r.args[1]], end='')
        print(' finished ', end='')
        if r.returncode == 0:
            print('successfully')
        elif r.returncode == 1:
            print('with warnings')
        else:
            print('with errors')
    
    # use highest exit code as global exit code
    return max(create_result.returncode, prune_result.returncode)


if __name__ == '__main__':
    main()

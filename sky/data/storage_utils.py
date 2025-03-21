"""Utility functions for the storage module."""
import glob
import os
import pathlib
import shlex
import subprocess
from typing import Any, Dict, List, Optional, TextIO, Union
import warnings
import zipfile

import colorama

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import log_utils

logger = sky_logging.init_logger(__name__)

_FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG = (
    f'{colorama.Fore.YELLOW}Warning: Files/dirs '
    'specified in .gitignore will be uploaded '
    'to the cloud storage for {path!r}'
    'due to the following error: {error_msg!r}')

_LAST_USE_TRUNC_LENGTH = 25


def format_storage_table(storages: List[Dict[str, Any]],
                         show_all: bool = False) -> str:
    """Format the storage table for display.

    Args:
        storage_table (dict): The storage table.

    Returns:
        str: The formatted storage table.
    """
    storage_table = log_utils.create_table([
        'NAME',
        'UPDATED',
        'STORE',
        'COMMAND',
        'STATUS',
    ])

    for row in storages:
        launched_at = row['launched_at']
        if show_all:
            command = row['last_use']
        else:
            command = common_utils.truncate_long_string(row['last_use'],
                                                        _LAST_USE_TRUNC_LENGTH)
        storage_table.add_row([
            # NAME
            row['name'],
            # LAUNCHED
            log_utils.readable_time_duration(launched_at),
            # CLOUDS
            ', '.join([s.value for s in row['store']]),
            # COMMAND,
            command,
            # STATUS
            row['status'].value,
        ])
    if storages:
        return str(storage_table)
    else:
        return 'No existing storage.'


def get_excluded_files_from_skyignore(src_dir_path: str) -> List[str]:
    """List files and patterns ignored by the .skyignore file
    in the given source directory.
    """
    excluded_list: List[str] = []
    expand_src_dir_path = os.path.expanduser(src_dir_path)
    skyignore_path = os.path.join(expand_src_dir_path,
                                  constants.SKY_IGNORE_FILE)

    try:
        with open(skyignore_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Make parsing consistent with rsync.
                    # Rsync uses '/' as current directory.
                    if line.startswith('/'):
                        line = '.' + line
                    else:
                        line = '**/' + line
                    # Find all files matching the pattern.
                    matching_files = glob.glob(os.path.join(
                        expand_src_dir_path, line),
                                               recursive=True)
                    # Process filenames to comply with cloud rsync format.
                    for i in range(len(matching_files)):
                        matching_files[i] = os.path.relpath(
                            matching_files[i], expand_src_dir_path)
                    excluded_list.extend(matching_files)
    except IOError as e:
        logger.warning(f'Error reading {skyignore_path}: '
                       f'{common_utils.format_exception(e, use_bracket=True)}')

    return excluded_list


def get_excluded_files_from_gitignore(src_dir_path: str) -> List[str]:
    """ Lists files and patterns ignored by git in the source directory

    Runs `git status --ignored` which returns a list of excluded files and
    patterns read from .gitignore and .git/info/exclude using git.
    `git init` is run if SRC_DIR_PATH is not a git repository and removed
    after obtaining excluded list.

    Returns:
        List[str] containing files and patterns to be ignored.  Some of the
        patterns include, **/mydir/*.txt, !myfile.log, or file-*/.
    """
    expand_src_dir_path = os.path.expanduser(src_dir_path)

    git_exclude_path = os.path.join(expand_src_dir_path, '.git/info/exclude')
    gitignore_path = os.path.join(expand_src_dir_path,
                                  constants.GIT_IGNORE_FILE)

    git_exclude_exists = os.path.isfile(git_exclude_path)
    gitignore_exists = os.path.isfile(gitignore_path)

    # This command outputs a list to be excluded according to .gitignore
    # and .git/info/exclude
    filter_cmd = (f'git -C {shlex.quote(expand_src_dir_path)} '
                  'status --ignored --porcelain=v1')
    excluded_list: List[str] = []

    if git_exclude_exists or gitignore_exists:
        try:
            output = subprocess.run(filter_cmd,
                                    shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    check=True,
                                    text=True)
        except subprocess.CalledProcessError as e:
            # when the SRC_DIR_PATH is not a git repo and .git
            # does not exist in it
            if e.returncode == exceptions.GIT_FATAL_EXIT_CODE:
                if 'not a git repository' in e.stderr:
                    # Check if the user has 'write' permission to
                    # SRC_DIR_PATH
                    if not os.access(expand_src_dir_path, os.W_OK):
                        error_msg = 'Write permission denial'
                        logger.warning(
                            _FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG.format(
                                path=src_dir_path, error_msg=error_msg))
                        return excluded_list
                    init_cmd = f'git -C {expand_src_dir_path} init'
                    try:
                        subprocess.run(init_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)
                        output = subprocess.run(filter_cmd,
                                                shell=True,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE,
                                                check=True,
                                                text=True)
                    except subprocess.CalledProcessError as init_e:
                        logger.warning(
                            _FILE_EXCLUSION_FROM_GITIGNORE_FAILURE_MSG.format(
                                path=src_dir_path, error_msg=init_e.stderr))
                        return excluded_list
                    if git_exclude_exists:
                        # removes all the files/dirs created with 'git init'
                        # under .git/ except .git/info/exclude
                        remove_files_cmd = (f'find {expand_src_dir_path}' \
                                            f'/.git -path {git_exclude_path}' \
                                            ' -prune -o -type f -exec rm -f ' \
                                            '{} +')
                        remove_dirs_cmd = (f'find {expand_src_dir_path}' \
                                        f'/.git -path {git_exclude_path}' \
                                        ' -o -type d -empty -delete')
                        subprocess.run(remove_files_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)
                        subprocess.run(remove_dirs_cmd,
                                       shell=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       check=True)

        output_list = output.stdout.split('\n')
        for line in output_list:
            # FILTER_CMD outputs items preceded by '!!'
            # to specify excluded files/dirs
            # e.g., '!! mydir/' or '!! mydir/myfile.txt'
            if line.startswith('!!'):
                to_be_excluded = line[3:]
                if line.endswith('/'):
                    # aws s3 sync and gsutil rsync require * to exclude
                    # files/dirs under the specified directory.
                    to_be_excluded += '*'
                excluded_list.append(to_be_excluded)
    return excluded_list


def get_excluded_files(src_dir_path: str) -> List[str]:
    # TODO: this could return a huge list of files,
    # should think of ways to optimize.
    """ List files and directories to be excluded."""
    expand_src_dir_path = os.path.expanduser(src_dir_path)
    skyignore_path = os.path.join(expand_src_dir_path,
                                  constants.SKY_IGNORE_FILE)
    if os.path.exists(skyignore_path):
        logger.debug(f'  {colorama.Style.DIM}'
                     f'Excluded files to sync to cluster based on '
                     f'{constants.SKY_IGNORE_FILE}.'
                     f'{colorama.Style.RESET_ALL}')
        return get_excluded_files_from_skyignore(src_dir_path)
    logger.debug(f'  {colorama.Style.DIM}'
                 f'Excluded files to sync to cluster based on '
                 f'{constants.GIT_IGNORE_FILE}.'
                 f'{colorama.Style.RESET_ALL}')
    return get_excluded_files_from_gitignore(src_dir_path)


def zip_files_and_folders(items: List[str],
                          output_file: Union[str, pathlib.Path],
                          log_file: Optional[TextIO] = None):

    def _store_symlink(zipf, path: str, is_dir: bool):
        # Get the target of the symlink
        target = os.readlink(path)
        # Use relative path as absolute path will not be able to resolve on
        # remote API server.
        if os.path.isabs(target):
            target = os.path.relpath(target, os.path.dirname(path))
        # Create a ZipInfo instance
        zi = zipfile.ZipInfo(path + '/') if is_dir else zipfile.ZipInfo(path)
        # Set external attributes to mark as symlink
        zi.external_attr = 0xA1ED0000
        # Write symlink target as content
        zipf.writestr(zi, target)

    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',
                                category=UserWarning,
                                message='Duplicate name:')
        with zipfile.ZipFile(output_file, 'w') as zipf:
            for item in items:
                item = os.path.expanduser(item)
                if not os.path.isfile(item) and not os.path.isdir(item):
                    raise ValueError(f'{item} does not exist.')
                excluded_files = set(
                    [os.path.join(item, f) for f in get_excluded_files(item)])
                if os.path.isfile(item) and item not in excluded_files:
                    zipf.write(item)
                elif os.path.isdir(item):
                    for root, dirs, files in os.walk(item, followlinks=False):
                        # Store directory entries (important for empty
                        # directories)
                        for dir_name in dirs:
                            dir_path = os.path.join(root, dir_name)
                            if dir_path in excluded_files:
                                continue
                            # If it's a symlink, store it as a symlink
                            if os.path.islink(dir_path):
                                _store_symlink(zipf, dir_path, is_dir=True)
                            else:
                                zipf.write(dir_path)

                        for file in files:
                            file_path = os.path.join(root, file)
                            if file_path in excluded_files:
                                continue
                            if os.path.islink(file_path):
                                _store_symlink(zipf, file_path, is_dir=False)
                            else:
                                zipf.write(file_path)
                if log_file is not None:
                    log_file.write(f'Zipped {item}\n')

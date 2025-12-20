# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/user_input.py

"""Utilities to get input interactively from the user"""

import glob
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class UserInputContext:
    """A container for user input callbacks"""

    get_input: callable
    get_pass: callable


def get_input(msg, completer=None):
    """
    Get user input, with optional readline tab completion,
    if readline is available.

    Note: readline is probably not available on Windows.
    """
    if completer is None:
        return input(msg)

    try:
        import readline
    except ImportError:
        # e.g., Windows has no readline
        return input(msg)

    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    try:
        readline.set_completer(completer)
        readline.set_completer_delims(FILENAME_COMPLETER_DELIMS)
        readline.parse_and_bind("tab: complete")
        ret = input(msg)
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)
    return ret


def _get_all_filename_matches(text, glob_suffix="*"):
    """
    Reusable helper to get a list of all possible filename matches.
    """
    text = os.path.expanduser(text)

    # Get all matches and add trailing slashes to dirs
    matches = [
        f + (os.path.sep if os.path.isdir(f) else "") for f in glob.glob(text + glob_suffix)
    ]
    logger.debug(glob.glob(text + glob_suffix))
    return matches


FILENAME_COMPLETER_DELIMS = " \t\n"


def filename_completer(text, state, glob_suffix="*"):
    """A simple filename completer for readline."""
    # Call the helper to get the full list
    matches = _get_all_filename_matches(text, glob_suffix)

    try:
        return matches[state]
    except IndexError:
        return None


def pdf_filename_completer(text, state):
    """A filename completer for pdf files"""
    # Call the helper with the specific glob
    matches = _get_all_filename_matches(text, "*.[Pp][Dd][Ff]")
    try:
        return matches[state]
    except IndexError:
        return None


def dirname_completer(text, state):
    """A directory name completer"""
    # Call the helper to get all matches
    all_matches = _get_all_filename_matches(text, "*")

    # *Then*, filter that list for directories
    dir_matches = [
        match
        for match in all_matches
        if match.endswith(os.path.sep)  # Check for the trailing slash
    ]

    try:
        return dir_matches[state]
    except IndexError:
        return None

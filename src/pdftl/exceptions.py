# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/exceptions.py

"""Exceptions for this project"""


class PackageError(Exception):
    """Exception raised by a feature with a missing required package"""


class UserCommandLineError(Exception):
    """Generic exception for a user passing an invalid command line"""


class MissingArgumentError(UserCommandLineError):
    """Exception for missing argument(s)"""


class InvalidArgumentError(UserCommandLineError):
    """Exception for invalid argument(s)"""


class InvalidCommandError(UserCommandLineError):
    """Exception for an invalid command"""

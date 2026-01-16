# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/exceptions.py

"""Exceptions for the pdftl project"""


class PdftlError(Exception):
    """Base exception for all pdftl errors"""


class PdftlConfigError(PdftlError):
    """Exception raised when required data or configuration is missing."""


class PackageError(PdftlError):
    """Exception raised by a feature with a missing required package"""


class UserCommandLineError(PdftlError):
    """Generic exception for a user passing an invalid command line"""


class MissingArgumentError(UserCommandLineError):
    """Exception for missing argument(s)"""


class InvalidArgumentError(UserCommandLineError):
    """Exception for invalid argument(s)"""


class DuplicateArgumentError(InvalidArgumentError):
    """Exception for duplicate argument(s)"""


class InvalidCommandError(UserCommandLineError):
    """Exception for an invalid command"""


class OperationError(PdftlError):
    """Raised when a PDF operation fails during execution"""


class SignatureError(PdftlError):
    """Raised when an operation returns an unexpected type (e.g. chaining on None)"""

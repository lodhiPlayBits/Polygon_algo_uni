from abc import ABC, abstractmethod


def problem_prefix(problem_id):
    """Folder that holds every stored file for one problem."""
    return f"test_cases/{problem_id}/"


def test_case_keys(problem_id, test_number):
    """
    Returns the (input_key, output_key) pair for one test case.

    This is the only place the storage layout is defined:
        test_cases/{problem_id}/{test_number}      input
        test_cases/{problem_id}/{test_number}.a    expected output
    """
    input_key = f"{problem_prefix(problem_id)}{test_number}"
    return input_key, f"{input_key}.a"


class StorageError(Exception):
    """Raised when a storage provider cannot complete an operation."""


class StorageBackend(ABC):
    """
    Provider-neutral interface for storing migrated files.

    The migration flow only talks to this interface, so switching providers
    means adding a subclass and a branch in factory.get_storage().
    """

    name = "storage"

    @abstractmethod
    def upload(self, key, data):
        """Writes bytes to key, replacing any existing object."""

    @abstractmethod
    def list_keys(self, prefix):
        """Returns every key that starts with prefix."""

    @abstractmethod
    def delete_prefix(self, prefix):
        """Deletes every key that starts with prefix and returns how many were deleted."""

"""Errors shared by model clients and services. No HTTP library here, so services may import it."""


class ModelUnavailable(Exception):
    """The model gave no usable reply. The message is a fixed string, safe to log."""

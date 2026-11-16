"""Errors the CLI turns into exit codes."""


class SatrcutError(Exception):
    """Expected failure with a message safe to print."""


class InfraError(SatrcutError):
    """ComfyUI or the host failed. The runner retries the same seed once."""


class SimulatedCrash(SatrcutError):
    """Stand-in for a killed process. partial.json stays, the manifest does not."""


class AssembleError(SatrcutError):
    """The frozen manifest does not match the files on disk."""


class LipSyncUnavailable(SatrcutError):
    """Close-up lip-sync is a phase-2 interface and does not run."""

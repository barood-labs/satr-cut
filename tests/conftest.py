import os

os.environ["SATRCUT_MIN_FREE_GB"] = "0"
os.environ["SATRCUT_RETRY_BACKOFF"] = "0"
os.environ["SATRCUT_X264_PRESET"] = "ultrafast"
os.environ.pop("SATRCUT_PROJECT", None)
os.environ.pop("SATRCUT_STOP_AT", None)
os.environ.pop("SATRCUT_MAINT_UTC", None)
os.environ.pop("SATRCUT_QC_RESPECT_SIDECAR", None)

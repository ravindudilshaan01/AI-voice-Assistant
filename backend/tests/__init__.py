
import os

# Unit tests mock synthesis and should not preload the 114 MB local graph.
os.environ.setdefault("KOKORO_PRELOAD", "0")

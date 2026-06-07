import os
import sys


bundle_dir = getattr(sys, "_MEIPASS", None)
if bundle_dir and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(bundle_dir)
    dynload_dir = os.path.join(bundle_dir, "python3.11", "lib-dynload")
    if os.path.isdir(dynload_dir):
        os.add_dll_directory(dynload_dir)

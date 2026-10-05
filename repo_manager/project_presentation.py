"""Shared compact Project labels; full paths remain authoritative."""
import os


def location_label(path, roots):
    """Show the useful part of a scanned path; the full path remains in details."""
    if not path:
        return "(no folder)"
    for root in roots:
        try:
            relative = os.path.relpath(path, root)
        except (OSError, ValueError):
            continue
        if relative == ".." or relative.startswith(".." + os.sep):
            continue
        root_name = os.path.basename(os.path.normpath(root))
        return os.path.join(root_name, relative) if relative != "." else root_name
    return path

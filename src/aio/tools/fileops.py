"""Filesystem operations: create dirs, move/rename, copy, delete, archive.

These give the agent first-class, platform-independent file management (the
same operations a desktop file manager offers) without shelling out to
``mv``/``rm``/``cp``/``zip`` — so they work identically on Windows, mac and
Linux, are confined to the working directory, ask for approval before mutating,
and (where reversible) record a rewindable checkpoint.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath


class MakeDirTool(Tool):
    name = "make_dir"
    description = "Create a directory (and any missing parents). No error if it already exists."
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory to create."}},
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        if p.is_file():
            raise ToolError(f"{_relpath(p, ctx)} already exists as a file.")
        existed = p.is_dir()
        p.mkdir(parents=True, exist_ok=True)
        return f"{'Directory already existed' if existed else 'Created directory'}: {_relpath(p, ctx)}"


class MovePathTool(Tool):
    name = "move_path"
    description = (
        "Move or rename a file or directory. Use this to rename or reorganise — "
        "it works on whole directories too. Parent dirs of the destination are "
        "created automatically."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing file or directory."},
            "dest": {"type": "string", "description": "New path/name."},
            "overwrite": {"type": "boolean", "description": "Replace an existing destination (default false)."},
        },
        "required": ["source", "dest"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        src = ctx.safe_path(args["source"])
        dst = ctx.safe_path(args["dest"])
        if not src.exists():
            raise ToolError(f"Source not found: {_relpath(src, ctx)}")
        if dst.exists():
            if not bool(args.get("overwrite", False)):
                raise ToolError(f"Destination exists: {_relpath(dst, ctx)}. Set overwrite=true to replace it.")
            _remove(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            ctx.snapshot(src, f"move_path {_relpath(src, ctx)} -> {_relpath(dst, ctx)}")
        shutil.move(str(src), str(dst))
        ctx.touch_file(_relpath(dst, ctx))
        return f"Moved {_relpath(src, ctx)} -> {_relpath(dst, ctx)}"


class CopyPathTool(Tool):
    name = "copy_path"
    description = "Copy a file or directory (recursively) to a new path."
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing file or directory."},
            "dest": {"type": "string", "description": "Destination path."},
            "overwrite": {"type": "boolean", "description": "Replace an existing destination (default false)."},
        },
        "required": ["source", "dest"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        src = ctx.safe_path(args["source"])
        dst = ctx.safe_path(args["dest"])
        if not src.exists():
            raise ToolError(f"Source not found: {_relpath(src, ctx)}")
        if dst.exists():
            if not bool(args.get("overwrite", False)):
                raise ToolError(f"Destination exists: {_relpath(dst, ctx)}. Set overwrite=true to replace it.")
            _remove(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
            kind = "directory"
        else:
            shutil.copy2(str(src), str(dst))
            kind = "file"
        return f"Copied {kind} {_relpath(src, ctx)} -> {_relpath(dst, ctx)}"


class DeletePathTool(Tool):
    name = "delete_path"
    description = (
        "Delete a file or directory (recursively). A single file is snapshotted "
        "so it can be restored with rewind; directory deletes are NOT reversible."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory to delete."},
            "recursive": {"type": "boolean", "description": "Required true to delete a non-empty directory."},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        if not p.exists():
            raise ToolError(f"Not found: {_relpath(p, ctx)}")
        if p == ctx.workdir.resolve() or p == ctx.workdir:
            raise ToolError("Refusing to delete the working directory itself.")
        if p.is_dir():
            non_empty = any(p.iterdir())
            if non_empty and not bool(args.get("recursive", False)):
                raise ToolError(f"{_relpath(p, ctx)} is a non-empty directory. Set recursive=true to delete it.")
            shutil.rmtree(p)
            return f"Deleted directory {_relpath(p, ctx)} (not reversible)."
        ctx.snapshot(p, f"delete_path {_relpath(p, ctx)}")   # restorable via rewind
        p.unlink()
        return f"Deleted file {_relpath(p, ctx)} (restorable with rewind)."


class ArchiveTool(Tool):
    name = "archive"
    description = (
        "Create or extract a zip archive. action='zip' compresses a file/dir into "
        "a .zip; action='unzip' extracts a .zip into a directory."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["zip", "unzip"], "description": "zip or unzip."},
            "source": {"type": "string", "description": "Path to compress (zip) or the .zip to extract (unzip)."},
            "dest": {"type": "string", "description": "Output .zip (zip) or target directory (unzip)."},
        },
        "required": ["action", "source", "dest"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        import zipfile

        action = args.get("action")
        src = ctx.safe_path(args["source"])
        dst = ctx.safe_path(args["dest"])
        if not src.exists():
            raise ToolError(f"Source not found: {_relpath(src, ctx)}")

        if action == "zip":
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
                if src.is_file():
                    zf.write(src, src.name)
                    n = 1
                else:
                    n = 0
                    for f in sorted(src.rglob("*")):
                        if f.is_file():
                            zf.write(f, str(f.relative_to(src.parent)))
                            n += 1
            return f"Zipped {n} file(s) from {_relpath(src, ctx)} -> {_relpath(dst, ctx)}"

        if action == "unzip":
            if not zipfile.is_zipfile(src):
                raise ToolError(f"{_relpath(src, ctx)} is not a valid zip archive.")
            dst.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(src) as zf:
                # guard against zip-slip: every member must stay under dst
                base = dst.resolve()
                for member in zf.namelist():
                    target = (dst / member).resolve()
                    if base not in target.parents and target != base:
                        raise ToolError(f"Refusing unsafe path in archive: {member}")
                zf.extractall(dst)
                n = len(zf.namelist())
            return f"Extracted {n} entr(ies) from {_relpath(src, ctx)} -> {_relpath(dst, ctx)}"

        raise ToolError("action must be 'zip' or 'unzip'.")


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()

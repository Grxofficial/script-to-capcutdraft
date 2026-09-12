"""本地素材索引数据库。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import ClipRecord, MediaInfo


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS media_files (
    id INTEGER PRIMARY KEY,
    library_root TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    duration REAL NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    fps REAL NOT NULL,
    rotation INTEGER NOT NULL DEFAULT 0,
    analyzer_version TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_media_library ON media_files(library_root);
CREATE INDEX IF NOT EXISTS idx_media_hash ON media_files(content_hash);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
    source_start REAL NOT NULL,
    source_end REAL NOT NULL,
    caption TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    UNIQUE(file_id, source_start, source_end)
);
CREATE INDEX IF NOT EXISTS idx_clips_file ON clips(file_id);
"""


class LibraryDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LibraryDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def file_state(self, path: Path) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM media_files WHERE path = ?", (str(path.resolve()),)
        ).fetchone()

    def file_by_hash(self, library_root: Path, content_hash: str, analyzer_version: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """SELECT * FROM media_files
            WHERE library_root=? AND content_hash=? AND analyzer_version=? AND error=''
            ORDER BY id LIMIT 1""",
            (str(library_root.resolve()), content_hash, analyzer_version),
        ).fetchone()

    def update_file_location(self, file_id: int, path: Path, size: int, mtime_ns: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE media_files SET path=?,size=?,mtime_ns=? WHERE id=?",
                (str(path.resolve()), size, mtime_ns, file_id),
            )

    def replace_file(
        self,
        library_root: Path,
        info: MediaInfo,
        content_hash: str,
        size: int,
        mtime_ns: int,
        analyzer_version: str,
        clips: Iterable[dict[str, Any]],
    ) -> int:
        path = str(info.path.resolve())
        with self.connection:
            existing = self.connection.execute(
                "SELECT id FROM media_files WHERE path = ?", (path,)
            ).fetchone()
            if existing:
                file_id = int(existing["id"])
                self.connection.execute("DELETE FROM clips WHERE file_id = ?", (file_id,))
                self.connection.execute(
                    """UPDATE media_files SET library_root=?, content_hash=?, size=?, mtime_ns=?,
                    duration=?, width=?, height=?, fps=?, rotation=?, analyzer_version=?, error=''
                    WHERE id=?""",
                    (str(library_root.resolve()), content_hash, size, mtime_ns, info.duration,
                     info.width, info.height, info.fps, info.rotation, analyzer_version, file_id),
                )
            else:
                cursor = self.connection.execute(
                    """INSERT INTO media_files
                    (library_root,path,content_hash,size,mtime_ns,duration,width,height,fps,rotation,analyzer_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(library_root.resolve()), path, content_hash, size, mtime_ns, info.duration,
                     info.width, info.height, info.fps, info.rotation, analyzer_version),
                )
                file_id = int(cursor.lastrowid)
            for clip in clips:
                self.connection.execute(
                    """INSERT INTO clips
                    (file_id,source_start,source_end,caption,metadata_json,embedding_json)
                    VALUES (?,?,?,?,?,?)""",
                    (file_id, clip["source_start"], clip["source_end"], clip["caption"],
                     json.dumps(clip["metadata"], ensure_ascii=False),
                     json.dumps(clip["embedding"])),
                )
        return file_id

    def record_error(
        self, library_root: Path, path: Path, size: int, mtime_ns: int,
        analyzer_version: str, error: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO media_files
                (library_root,path,content_hash,size,mtime_ns,duration,width,height,fps,rotation,analyzer_version,error)
                VALUES (?,?,?,?,?,0,0,0,0,0,?,?)
                ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
                analyzer_version=excluded.analyzer_version,error=excluded.error""",
                (str(library_root.resolve()), str(path.resolve()), "", size, mtime_ns, analyzer_version, error[:2000]),
            )

    def prune_missing(self, library_root: Path, existing_paths: set[str]) -> int:
        rows = self.connection.execute(
            "SELECT id,path FROM media_files WHERE library_root = ?", (str(library_root.resolve()),)
        ).fetchall()
        stale = [int(row["id"]) for row in rows if row["path"] not in existing_paths]
        with self.connection:
            self.connection.executemany("DELETE FROM media_files WHERE id = ?", [(item,) for item in stale])
        return len(stale)

    def clips_for_library(self, library_root: Path) -> list[ClipRecord]:
        return self.clips_for_libraries([library_root])

    def clips_for_libraries(self, library_roots: Iterable[Path]) -> list[ClipRecord]:
        roots = list(dict.fromkeys(str(root.resolve()) for root in library_roots))
        if not roots:
            return []
        placeholders = ",".join("?" for _ in roots)
        rows = self.connection.execute(
            f"""SELECT c.*,m.path,m.width,m.height,m.fps FROM clips c
            JOIN media_files m ON m.id=c.file_id
            WHERE m.library_root IN ({placeholders}) AND m.error='' ORDER BY c.id""",
            roots,
        ).fetchall()
        return [ClipRecord(
            id=int(row["id"]), file_id=int(row["file_id"]), path=row["path"],
            source_start=float(row["source_start"]), source_end=float(row["source_end"]),
            width=int(row["width"]), height=int(row["height"]), caption=row["caption"],
            metadata=json.loads(row["metadata_json"]), embedding=json.loads(row["embedding_json"]),
            fps=float(row["fps"] or 30.0),
        ) for row in rows]

    def stats(self, library_root: Path | None = None) -> dict[str, int]:
        suffix = " WHERE library_root=?" if library_root else ""
        params = (str(library_root.resolve()),) if library_root else ()
        files = self.connection.execute(f"SELECT COUNT(*) FROM media_files{suffix}", params).fetchone()[0]
        if library_root:
            clips = self.connection.execute(
                "SELECT COUNT(*) FROM clips c JOIN media_files m ON m.id=c.file_id WHERE m.library_root=?",
                params,
            ).fetchone()[0]
        else:
            clips = self.connection.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        return {"files": int(files), "clips": int(clips)}

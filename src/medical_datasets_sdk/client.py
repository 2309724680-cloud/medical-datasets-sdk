"""Small, dependency-free client for the Medical Datasets catalog API."""

from __future__ import annotations

import json
import hashlib
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator


ProgressCallback = Callable[[str, int, int], None]


class _ProgressReader:
    def __init__(self, handle, path: str, total: int, callback: ProgressCallback | None):
        self.handle = handle
        self.path = path
        self.total = total
        self.callback = callback
        self.done = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.handle.read(size)
        self.done += len(chunk)
        if self.callback and chunk:
            self.callback(self.path, self.done, self.total)
        return chunk


class _PartReader:
    def __init__(self, handle, length: int, path: str, base: int, total: int, callback: ProgressCallback | None):
        self.handle = handle
        self.remaining = length
        self.path = path
        self.base = base
        self.total = total
        self.callback = callback
        self.done = 0

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        requested = self.remaining if size is None or size < 0 else min(size, self.remaining)
        chunk = self.handle.read(requested)
        self.remaining -= len(chunk)
        self.done += len(chunk)
        if self.callback and chunk:
            self.callback(self.path, self.base + self.done, self.total)
        return chunk


class MedicalDatasetsError(RuntimeError):
    """Raised when the catalog API rejects a request."""


class MedicalDatasetsClient:
    """Browse and download catalog datasets over HTTP.

    Args:
        base_url: Catalog address, for example ``http://catalog.internal:4173``.
        token: Optional SDK key. When omitted, ``MEDICAL_DATASETS_SDKEY`` is used,
            falling back to the legacy ``MEDICAL_DATASETS_TOKEN``.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.getenv("MEDICAL_DATASETS_SDKEY", os.getenv("MEDICAL_DATASETS_TOKEN", ""))
        self.timeout = timeout

    def get_dataset(self, slug: str) -> dict:
        return self._json(f"/api/datasets/{self._quote_slug(slug)}")

    def whoami(self) -> dict:
        """Return the user represented by the configured SDK Key."""
        return self._json("/api/auth/me")["user"]

    def create_dataset(
        self,
        name: str,
        source: str,
        *,
        summary: str = "",
        description: str = "",
        category: str = "其他",
        modalities: list[str] | None = None,
        formats: list[str] | None = None,
        tags: list[str] | None = None,
        readme: str = "",
        defer_initial_revision: bool = False,
    ) -> dict:
        """Create a managed dataset record.

        Set ``defer_initial_revision`` when files will be uploaded immediately.
        The draft remains hidden until the first import is committed.
        """
        return self._json_request("POST", "/api/datasets", {
            "name": name,
            "source": source,
            "summary": summary,
            "description": description,
            "category": category,
            "modalities": modalities or [],
            "formats": formats or [],
            "tags": tags or [],
            "readme": readme,
            "recordType": "dataset",
            "deferInitialRevision": bool(defer_initial_revision),
        })

    def upload_dataset(
        self,
        source: str | os.PathLike[str],
        *,
        dataset_slug: str | None = None,
        name: str | None = None,
        source_name: str = "SDK 上传",
        summary: str = "",
        description: str = "",
        readme: str = "",
        category: str = "其他",
        modalities: list[str] | None = None,
        formats: list[str] | None = None,
        tags: list[str] | None = None,
        message: str = "SDK 上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        poll_interval: float = 0.8,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload, organize, and publish in one call.

        Pass ``dataset_slug`` to append to an existing dataset. If both
        ``dataset_slug`` and ``name`` are omitted, the local file or directory name
        is used for the new dataset. ``source`` may be one file, an archive, or a
        directory. The returned dictionary contains the committed revision and
        dataset metadata.
        """
        if dataset_slug and name:
            raise ValueError("dataset_slug 和 name 只能填写一个")
        is_new_dataset = not dataset_slug
        if is_new_dataset and not str(name or "").strip():
            name = Path(source).expanduser().name or "SDK 上传数据集"

        slug = dataset_slug
        draft_state_path = None
        if is_new_dataset:
            source_path = Path(source).expanduser().resolve()
            draft_state_path = self._state_path("draft", str(name).strip(), str(source_path))
            draft_state = self._load_state(draft_state_path)
            dataset = None
            if draft_state.get("slug"):
                try:
                    candidate = self.get_dataset(draft_state["slug"])
                    if candidate.get("contentRevision"):
                        self._remove_state(draft_state_path)
                        return {"dataset": candidate, "revision": {"id": candidate["contentRevision"]}, "resumed": True}
                    if candidate.get("migrationStatus") == "draft" and not candidate.get("contentRevision"):
                        dataset = candidate
                except MedicalDatasetsError:
                    pass
            if dataset is None:
                dataset = self.create_dataset(
                    str(name).strip(),
                    source_name,
                    summary=summary,
                    description=description,
                    category=category,
                    modalities=modalities,
                    formats=formats,
                    tags=tags,
                    readme=readme,
                    defer_initial_revision=True,
                )
                self._save_state(draft_state_path, {"datasetId": dataset["id"], "slug": dataset["slug"]})
            slug = dataset["slug"]

        try:
            result = self.import_directory(
                str(slug),
                source,
                message=message,
                progress=progress,
                chunk_size=chunk_size,
                keep_archives=keep_archives,
                confirm=True,
                new_dataset=is_new_dataset,
                poll_interval=poll_interval,
                exclude=exclude,
            )
            if draft_state_path:
                self._remove_state(draft_state_path)
            return result
        except Exception:
            raise

    def upload_directory(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传目录",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
    ) -> dict:
        """Backward-compatible alias for :meth:`import_directory`."""
        return self.import_directory(
            slug,
            source,
            message=message,
            progress=progress,
            chunk_size=chunk_size,
            keep_archives=keep_archives,
            confirm=True,
        )

    def analyze_upload(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        poll_interval: float = 0.8,
        new_dataset: bool = False,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload a file or directory and wait for an organization plan.

        ZIP, RAR, TAR, TAR.GZ and TGZ inputs are extracted in an isolated server
        workspace. Nothing is committed until :meth:`commit_import` is called.
        """
        dataset = self.get_dataset(slug)
        if new_dataset and (dataset.get("migrationStatus") != "draft" or dataset.get("contentRevision")):
            raise MedicalDatasetsError("new_dataset 只能用于尚未发布的草稿数据集")
        upload_id: str | None = None
        try:
            source_path = Path(source).expanduser().resolve()
            if not source_path.exists() or source_path.is_symlink():
                raise MedicalDatasetsError(f"上传来源不存在或不可用: {source_path}")
            if source_path.is_dir():
                excluded = {str(item).strip() for item in exclude if str(item).strip()}
                files = sorted(
                    path for path in source_path.rglob("*")
                    if path.is_file()
                    and not path.is_symlink()
                    and not any(part in excluded for part in path.relative_to(source_path).parts)
                )
                upload_entries = [(path, path.relative_to(source_path).as_posix()) for path in files]
            elif source_path.is_file():
                upload_entries = [(source_path, source_path.name)]
            else:
                raise MedicalDatasetsError(f"上传来源不是普通文件或目录: {source_path}")
            if not upload_entries:
                raise MedicalDatasetsError("上传目录中没有普通文件")
            source_fingerprint = hashlib.sha256("\n".join(
                f"{relative_path}\0{local_path.stat().st_size}\0{local_path.stat().st_mtime_ns}"
                for local_path, relative_path in upload_entries
            ).encode("utf-8")).hexdigest()
            state_path = self._state_path("upload", str(dataset["id"]), str(source_path))
            state = self._load_state(state_path)
            session = None
            if state.get("uploadId"):
                if state.get("sourceFingerprint") and state["sourceFingerprint"] != source_fingerprint:
                    try:
                        self._json_request("DELETE", f"/api/uploads/{urllib.parse.quote(state['uploadId'], safe='')}")
                    except MedicalDatasetsError:
                        pass
                    state = {}
                try:
                    candidate = self._json(f"/api/uploads/{urllib.parse.quote(state.get('uploadId', ''), safe='')}") if state.get("uploadId") else None
                    if candidate is None:
                        raise MedicalDatasetsError("没有可续传会话")
                    if candidate.get("status") == "completed":
                        return {"id": candidate["id"], "status": "completed", "dataset": dataset, "revision": {"id": dataset.get("contentRevision")}, "resumed": True}
                    if candidate.get("status") == "open" and int(candidate.get("datasetId", 0)) == int(dataset["id"]):
                        session = candidate
                except MedicalDatasetsError:
                    pass
            if session is None:
                session = self._json_request(
                    "POST", f"/api/datasets/{dataset['id']}/uploads", {"message": message}
                )
                state = {"uploadId": session["id"], "datasetId": dataset["id"], "source": str(source_path), "sourceFingerprint": source_fingerprint}
                self._save_state(state_path, state)
            upload_id = session["id"]
            server_limit = int(session.get("limits", {}).get("maxChunkBytes") or chunk_size)
            actual_chunk_size = max(1024 * 1024, min(int(chunk_size), server_limit))
            existing_files = {item["path"]: item for item in session.get("files", [])}
            for local_path, relative_path in upload_entries:
                size_bytes = local_path.stat().st_size
                remote = existing_files.get(relative_path)
                if remote and int(remote.get("sizeBytes", -1)) != size_bytes:
                    raise MedicalDatasetsError(f"本地文件大小在续传期间发生变化: {relative_path}")
                if remote is None:
                    remote = self._json_request(
                        "POST",
                        f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files",
                        {
                            "path": relative_path,
                            "sizeBytes": size_bytes,
                            "direct": True,
                            "contentType": "application/octet-stream",
                        },
                    )
                if remote.get("status") == "ready" and int(remote.get("uploadedBytes", 0)) == size_bytes:
                    if progress:
                        progress(relative_path, size_bytes, size_bytes)
                    continue
                if remote.get("multipart") or remote.get("multipartUploadId"):
                    self._upload_multipart(upload_id, remote, local_path, relative_path, size_bytes, progress)
                    continue
                if remote.get("direct"):
                    self._json_request(
                        "POST",
                        f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}/complete",
                        {},
                    )
                    if progress:
                        progress(relative_path, size_bytes, size_bytes)
                    continue
                if remote.get("uploadUrl"):
                    with local_path.open("rb") as handle:
                        upload_request = urllib.request.Request(
                            remote["uploadUrl"],
                            data=_ProgressReader(handle, relative_path, size_bytes, progress),
                            method="PUT",
                            headers={
                                "Content-Type": "application/octet-stream",
                                "Content-Length": str(size_bytes),
                            },
                        )
                        try:
                            with urllib.request.urlopen(upload_request, timeout=self.timeout) as response:
                                response.read()
                        except urllib.error.HTTPError as error:
                            raise MedicalDatasetsError(f"对象存储上传失败: HTTP {error.code}") from error
                        except urllib.error.URLError as error:
                            raise MedicalDatasetsError(f"无法连接对象存储: {error.reason}") from error
                    self._json_request(
                        "POST",
                        f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}/complete",
                        {},
                    )
                    if progress and size_bytes == 0:
                        progress(relative_path, 0, 0)
                    continue
                uploaded = int(remote.get("uploadedBytes", 0))
                with local_path.open("rb") as handle:
                    handle.seek(uploaded)
                    while uploaded < size_bytes:
                        chunk = handle.read(min(actual_chunk_size, size_bytes - uploaded))
                        if not chunk:
                            raise MedicalDatasetsError(f"读取上传文件失败: {relative_path}")
                        result = self._json_request(
                            "PATCH",
                            f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}",
                            data=chunk,
                            headers={
                                "Content-Type": "application/octet-stream",
                                "Upload-Offset": str(uploaded),
                            },
                        )
                        uploaded = int(result["uploadedBytes"])
                        if progress:
                            progress(relative_path, uploaded, size_bytes)
            job = self._json_request(
                "POST",
                f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/analyze",
                {"keepArchives": bool(keep_archives), "newDataset": bool(new_dataset)},
            )
            while job.get("status") in {"analyzing", "extracting"}:
                time.sleep(max(0.2, float(poll_interval)))
                job = self.get_import(upload_id)
            if job.get("status") != "ready":
                raise MedicalDatasetsError(job.get("error") or "平台未能生成整理方案")
            return job
        except Exception:
            raise

    def _upload_multipart(self, upload_id: str, remote: dict, local_path: Path, relative_path: str, size_bytes: int, progress: ProgressCallback | None) -> None:
        base = f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}/multipart"
        status = self._json(base)
        part_size = int(status["partSizeBytes"])
        completed = {int(part["partNumber"]): int(part["sizeBytes"]) for part in status.get("parts", [])}
        uploaded_bytes = sum(completed.values())
        if progress:
            progress(relative_path, uploaded_bytes, size_bytes)
        part_count = (size_bytes + part_size - 1) // part_size
        if part_count > int(status.get("maxParts", 10000)):
            raise MedicalDatasetsError(f"文件需要 {part_count} 个分片，超过对象存储限制")
        with local_path.open("rb") as handle:
            for part_number in range(1, part_count + 1):
                offset = (part_number - 1) * part_size
                length = min(part_size, size_bytes - offset)
                if completed.get(part_number) == length:
                    continue
                for attempt in range(1, 6):
                    handle.seek(offset)
                    signed = self._json_request("POST", f"{base}/parts/{part_number}", {})
                    request = urllib.request.Request(
                        signed["url"],
                        data=_PartReader(handle, length, relative_path, offset, size_bytes, progress),
                        method="PUT",
                        headers={"Content-Length": str(length)},
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=max(self.timeout, 3600)) as response:
                            response.read()
                        uploaded_bytes += length
                        if progress:
                            progress(relative_path, uploaded_bytes, size_bytes)
                        break
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
                        if attempt == 5:
                            raise MedicalDatasetsError(f"Multipart 分片 {part_number} 上传失败，重新执行命令可续传: {error}") from error
                        time.sleep(min(2 ** (attempt - 1), 8))
        self._json_request("POST", f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}/complete", {})

    def get_import(self, upload_id: str) -> dict:
        """Return the current analysis or commit state for an upload."""
        return self._json(f"/api/imports/{urllib.parse.quote(upload_id, safe='')}")

    def commit_import(self, upload_id: str) -> dict:
        """Atomically commit a prepared organization plan as a new revision."""
        return self._json_request(
            "POST",
            f"/api/imports/{urllib.parse.quote(upload_id, safe='')}/commit",
            {},
        )

    def cancel_import(self, upload_id: str, *, remove_dataset: bool = False) -> None:
        """Cancel analysis and remove its isolated upload workspace."""
        suffix = "?removeDataset=1" if remove_dataset else ""
        self._json_request("DELETE", f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}{suffix}")

    def import_directory(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        confirm: bool = True,
        new_dataset: bool = False,
        poll_interval: float = 0.8,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload, analyze and optionally commit a file or directory."""
        job = self.analyze_upload(
            slug,
            source,
            message=message,
            progress=progress,
            chunk_size=chunk_size,
            keep_archives=keep_archives,
            new_dataset=new_dataset,
            poll_interval=poll_interval,
            exclude=exclude,
        )
        if job.get("status") == "completed":
            self._remove_state(self._state_path("upload", str(job["dataset"]["id"]), str(Path(source).expanduser().resolve())))
            return job
        if not confirm:
            return job
        result = self.commit_import(job["id"])
        source_path = Path(source).expanduser().resolve()
        dataset = self.get_dataset(slug)
        self._remove_state(self._state_path("upload", str(dataset["id"]), str(source_path)))
        return result

    def iter_directory(self, slug: str, path: str = "") -> Iterator[dict]:
        """Yield every entry in a directory, transparently following API pages."""
        offset = 0
        while True:
            query = urllib.parse.urlencode({"path": path, "offset": offset, "limit": 500})
            page = self._json(f"/api/datasets/{self._quote_slug(slug)}/files?{query}")
            yield from page["entries"]
            if not page.get("hasMore"):
                return
            offset += len(page["entries"])

    def download_dataset(
        self,
        slug: str,
        destination: str | os.PathLike[str] = ".",
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Recursively download a dataset and return its local directory.

        Existing complete files are skipped. Interrupted files are stored with a
        ``.part`` suffix and resumed on the next call when the server supports it.
        """
        dataset = self.get_dataset(slug)
        dataset_dir = Path(destination).expanduser() / self._safe_folder_name(dataset["name"])
        dataset_dir.mkdir(parents=True, exist_ok=True)

        pending = [""]
        while pending:
            directory = pending.pop()
            for entry in self.iter_directory(slug, directory):
                relative_path = str(PurePosixPath(directory, entry["name"]))
                if entry["type"] == "directory":
                    (dataset_dir / self._local_relative_path(relative_path)).mkdir(parents=True, exist_ok=True)
                    pending.append(relative_path)
                elif entry["type"] == "file":
                    self.download_file(
                        slug,
                        relative_path,
                        dataset_dir / self._local_relative_path(relative_path),
                        expected_size=entry.get("sizeBytes"),
                        progress=progress,
                    )
        return dataset_dir

    def download_file(
        self,
        slug: str,
        path: str,
        destination: str | os.PathLike[str],
        expected_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Download one file atomically, resuming an existing partial download."""
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        if expected_size is not None and target.is_file() and target.stat().st_size == expected_size:
            if progress:
                progress(path, expected_size, expected_size)
            return target

        partial = target.with_name(f"{target.name}.part")
        downloaded = partial.stat().st_size if partial.is_file() else 0
        if expected_size is not None and downloaded > expected_size:
            partial.unlink()
            downloaded = 0
        if expected_size is not None and downloaded == expected_size:
            partial.replace(target)
            return target

        query = urllib.parse.urlencode({"path": path})
        last_error: Exception | None = None
        for attempt in range(1, 9):
            downloaded = partial.stat().st_size if partial.is_file() else 0
            if expected_size is not None and downloaded == expected_size:
                break
            try:
                request = self._fresh_download_request(slug, query)
                if downloaded:
                    request.add_header("Range", f"bytes={downloaded}-")
                response = urllib.request.urlopen(request, timeout=max(self.timeout, 3600))
                with response:
                    append = downloaded > 0 and response.status == 206
                    if not append:
                        downloaded = 0
                    content_length = int(response.headers.get("Content-Length", "0"))
                    total = expected_size or (downloaded + content_length)
                    with partial.open("ab" if append else "wb") as output:
                        while True:
                            chunk = response.read(4 * 1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                progress(path, downloaded, total)
                if expected_size is None or partial.stat().st_size == expected_size:
                    break
                last_error = MedicalDatasetsError(f"下载连接提前结束，已保留 {partial.stat().st_size} 字节")
            except (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, MedicalDatasetsError) as error:
                last_error = error
            if attempt < 8:
                time.sleep(min(2 ** (attempt - 1), 8))

        if expected_size is not None and partial.stat().st_size != expected_size:
            raise MedicalDatasetsError(
                f"下载大小不一致: {path}（预期 {expected_size}，实际 {partial.stat().st_size}）；重新执行命令可继续下载"
            ) from last_error
        partial.replace(target)
        return target

    def _fresh_download_request(self, slug: str, query: str) -> urllib.request.Request:
        request = self._request(f"/api/datasets/{self._quote_slug(slug)}/object-download?{query}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                object_download = json.load(response)
            return urllib.request.Request(object_download["url"], method="GET")
        except urllib.error.HTTPError as error:
            if error.code not in {404, 409}:
                raise self._api_error(error) from error
            return self._request(f"/api/datasets/{self._quote_slug(slug)}/download?{query}")
        except (KeyError, json.JSONDecodeError):
            return self._request(f"/api/datasets/{self._quote_slug(slug)}/download?{query}")

    def _json(self, path: str) -> dict:
        return self._json_request("GET", path)

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        if payload is not None and data is not None:
            raise ValueError("payload and data cannot both be provided")
        body = data
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = self._request(path, method=method, data=body, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return {}
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise self._api_error(error) from error
        except urllib.error.URLError as error:
            raise MedicalDatasetsError(f"无法连接数据集平台: {error.reason}") from error

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Accept", "application/json, application/octet-stream")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        return request

    @staticmethod
    def _api_error(error: urllib.error.HTTPError) -> MedicalDatasetsError:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error")
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = None
        return MedicalDatasetsError(message or f"数据集平台请求失败 ({error.code})")

    @staticmethod
    def _quote_slug(slug: str) -> str:
        return urllib.parse.quote(str(slug), safe="")

    @staticmethod
    def _safe_folder_name(name: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", str(name)).strip(". ")
        return cleaned or "dataset"

    @staticmethod
    def _local_relative_path(value: str) -> Path:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise MedicalDatasetsError(f"服务端返回了不安全的文件路径: {value}")
        return Path(*path.parts)

    def _state_path(self, kind: str, *values: str) -> Path:
        identity = hashlib.sha256("\0".join((self.base_url, kind, *values)).encode("utf-8")).hexdigest()
        return Path(os.getenv("MEDICAL_DATASETS_STATE_DIR", Path.home() / ".medical-datasets" / "transfers")) / f"{kind}-{identity}.json"

    @staticmethod
    def _load_state(path: Path) -> dict:
        try:
            value = json.loads(path.read_text("utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_state(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(path)

    @staticmethod
    def _remove_state(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

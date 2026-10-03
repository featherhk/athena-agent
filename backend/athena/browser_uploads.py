"""Bounded, account-owned file snapshots for a browser's file inputs.

The browser only receives host-created snapshot paths, never model-provided paths.
Snapshots outlive a dispatch because a file input may read its file on later submit.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
from contextlib import contextmanager

from .files import DIRECTORY_FLAGS, MAX_FILE_BYTES, _child_dir, _open_file, sanitize_filename

MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_STAGED_FILES = 100
MAX_STAGED_BYTES = 100 * 1024 * 1024
IDENTIFIER = re.compile(r'[a-zA-Z0-9_-]{1,64}')


def upload_ids(args):
    if ('file_id' in args) == ('file_ids' in args):
        raise ValueError('Provide file_id or file_ids, not both.')
    values = [args['file_id']] if 'file_id' in args else args['file_ids']
    if (not isinstance(values, list) or not 1 <= len(values) <= MAX_UPLOAD_FILES
            or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError('Upload 1–10 distinct owned file IDs; paths are not accepted.')
    return values


@contextmanager
def _area(config, owner, create=False):
    if not IDENTIFIER.fullmatch(owner):
        raise ValueError('Invalid browser owner.')
    descriptors = []
    try:
        descriptors.append(os.open(config.data_dir, DIRECTORY_FLAGS))
        for part in ('browser-profiles', owner, 'uploads'):
            descriptors.append(_child_dir(descriptors[-1], part, create))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _usage(directory, depth=0):
    size, count = 0, 0
    with os.scandir(directory) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and depth < 2:
                child = _child_dir(directory, entry.name)
                try:
                    child_size, child_count = _usage(child, depth + 1)
                finally:
                    os.close(child)
                size += child_size
                count += max(1, child_count)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                size += info.st_size
                count += 1
            else:
                raise ValueError('Browser upload storage contains an unsafe entry.')
            if size > MAX_STAGED_BYTES or count > MAX_STAGED_FILES:
                raise ValueError('Browser upload staging is full. Close completed browser tabs before uploading more files.')
    return size, count


def _remove(directory, name):
    """Remove only inside already-open no-follow directories; never chase a link."""
    child = _child_dir(directory, name)
    try:
        with os.scandir(child) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    _remove(child, entry.name)
                else:
                    os.unlink(entry.name, dir_fd=child)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=directory)


def discard_upload(config, owner, operation_id):
    if not IDENTIFIER.fullmatch(operation_id):
        raise ValueError('Invalid browser upload identity.')
    try:
        with _area(config, owner) as directory:
            _remove(directory, operation_id)
    except FileNotFoundError:
        pass


def staged_operations(config, owner):
    try:
        with _area(config, owner) as directory:
            # The staging quota bounds this inventory independently of action history.
            values = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(values) >= MAX_STAGED_FILES:
                        break
                    if IDENTIFIER.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                        values.append(entry.name)
            return values
    except FileNotFoundError:
        return []


def stage_upload(config, owner, operation_id, records):
    if not IDENTIFIER.fullmatch(operation_id) or not 1 <= len(records) <= MAX_UPLOAD_FILES:
        raise ValueError('Invalid browser upload batch.')
    created = False
    try:
        with _area(config, owner, create=True) as area:
            existing_size, existing_count = _usage(area)
            if existing_count + len(records) > MAX_STAGED_FILES:
                raise ValueError('Browser upload staging is full. Close completed browser tabs before uploading more files.')
            os.mkdir(operation_id, 0o700, dir_fd=area)  # Unique action; never replace an earlier snapshot.
            created = True
            batch = _child_dir(area, operation_id)
            try:
                files, paths, total = [], [], 0
                for record in records:
                    if record['user_id'] != owner or not IDENTIFIER.fullmatch(record['id']):
                        raise ValueError('Browser upload file belongs to another account.')
                    name = sanitize_filename(record['name'])
                    incoming, size = _open_file(config, record)
                    with incoming:
                        before = os.fstat(incoming.fileno())
                        if size > MAX_FILE_BYTES or total + size > MAX_UPLOAD_BYTES:
                            raise ValueError('Browser upload accepts at most 20 MiB per file and 40 MiB per batch.')
                        if existing_size + total + size > MAX_STAGED_BYTES:
                            raise ValueError('Browser upload staging is full. Close completed browser tabs before uploading more files.')
                        file_dir = _child_dir(batch, record['id'], create=True)
                        try:
                            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=file_dir)
                            copied, digest = 0, hashlib.sha256()
                            with os.fdopen(descriptor, 'wb') as outgoing:
                                while chunk := incoming.read(65536):
                                    copied += len(chunk)
                                    if copied > size:
                                        raise ValueError('The upload file changed while being copied; read it again before retrying.')
                                    digest.update(chunk)
                                    outgoing.write(chunk)
                                outgoing.flush()
                                os.fsync(outgoing.fileno())
                                os.fchmod(outgoing.fileno(), 0o400)
                        finally:
                            os.close(file_dir)
                        after = os.fstat(incoming.fileno())
                        if copied != size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                            raise ValueError('The upload file changed while being copied; read it again before retrying.')
                    total += copied
                    paths.append(str(Path(config.data_dir) / 'browser-profiles' / owner / 'uploads' / operation_id / record['id'] / name))
                    files.append({'file_id': record['id'], 'name': name, 'size': copied, 'sha256': digest.hexdigest()})
                return {'paths': paths, 'files': files, 'bytes': total}
            finally:
                os.close(batch)
    except BaseException:
        if created:
            try:
                discard_upload(config, owner, operation_id)
            except OSError:
                pass  # The quota still accounts for any unremovable partial copy.
        raise

"""
BSA archive scanner and targeted extractor for Skyrim SE scripts.

Two public functions:
  scan_bsa_scripts(bsa_path)
      Read the BSA file table and return {lowercase_filename: record} for
      every scripts/*.pex entry found.  No data bytes are read.

  extract_bsa_record(bsa_path, record, out_path)
      Extract one specific .pex record to out_path, handling LZ4 (v105)
      and zlib (v104) compression.

Records returned by scan_bsa_scripts are self-contained dicts that carry all
metadata needed by extract_bsa_record (version, offsets, compression flags).
"""

import os
import struct
import zlib

import lz4.frame

MAGIC            = b'BSA\x00'
FLAG_DIR_NAMES   = 0x001
FLAG_FILE_NAMES  = 0x002
FLAG_COMPRESSED  = 0x004
FLAG_EMBED_NAMES = 0x100


def _read_bzstring(f):
    n = struct.unpack('<B', f.read(1))[0]
    return f.read(n).rstrip(b'\x00').decode('utf-8', errors='replace')


def _read_bsa_table(bsa_path: str) -> list:
    """
    Parse BSA header and file table.  Returns a list of record dicts
    (no data bytes extracted).  Raises ValueError for non-BSA files.
    """
    with open(bsa_path, 'rb') as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise ValueError(f"Not a BSA: {os.path.basename(bsa_path)}")

        version         = struct.unpack('<I', f.read(4))[0]
        folder_offset   = struct.unpack('<I', f.read(4))[0]
        archive_flags   = struct.unpack('<I', f.read(4))[0]
        folder_count    = struct.unpack('<I', f.read(4))[0]
        _file_count     = struct.unpack('<I', f.read(4))[0]
        _tfnl           = struct.unpack('<I', f.read(4))[0]
        total_fname_len = struct.unpack('<I', f.read(4))[0]
        _content_type   = struct.unpack('<I', f.read(4))[0]

        has_dir_names   = bool(archive_flags & FLAG_DIR_NAMES)
        has_file_names  = bool(archive_flags & FLAG_FILE_NAMES)
        compressed_dflt = bool(archive_flags & FLAG_COMPRESSED)
        embed_names     = bool(archive_flags & FLAG_EMBED_NAMES)

        # ── Folder records ───────────────────────────────────────────────
        f.seek(folder_offset)
        folders = []
        for _ in range(folder_count):
            h   = struct.unpack('<Q', f.read(8))[0]
            cnt = struct.unpack('<I', f.read(4))[0]
            if version == 105:
                f.read(4)  # unknown padding present in SSE v105
                off = struct.unpack('<Q', f.read(8))[0]
            else:
                off = struct.unpack('<I', f.read(4))[0]
            folders.append({'hash': h, 'count': cnt, 'offset': off, 'name': ''})

        # ── File record blocks (one block per folder) ────────────────────
        all_records = []
        for folder in folders:
            f.seek(folder['offset'] - total_fname_len)
            if has_dir_names:
                folder['name'] = _read_bzstring(f)
            for _ in range(folder['count']):
                fh  = struct.unpack('<Q', f.read(8))[0]
                sz  = struct.unpack('<I', f.read(4))[0]
                off = struct.unpack('<I', f.read(4))[0]
                toggle        = bool(sz & 0x40000000)
                actual_sz     = sz & 0x3FFFFFFF
                is_compressed = compressed_dflt ^ toggle
                all_records.append({
                    'folder':      folder['name'],
                    'name':        '',          # filled below
                    'hash':        fh,
                    'size':        actual_sz,
                    'offset':      off,
                    'compressed':  is_compressed,
                    'bsa_version': version,
                    'embed_names': embed_names,
                })

        # ── File name block ──────────────────────────────────────────────
        if has_file_names:
            for rec in all_records:
                buf = b''
                while True:
                    c = f.read(1)
                    if not c or c == b'\x00':
                        break
                    buf += c
                rec['name'] = buf.decode('utf-8', errors='replace')

    return all_records


def scan_bsa_scripts(bsa_path: str) -> dict:
    """
    Return {lowercase_filename: record} for every scripts/*.pex entry in the BSA.
    Fast: reads only the file table, no data bytes.
    """
    try:
        records = _read_bsa_table(bsa_path)
    except (ValueError, struct.error, OSError):
        return {}

    result = {}
    for rec in records:
        folder = rec['folder'].lower().replace('\\', '/').strip('/')
        fname  = rec['name'].lower()
        if folder == 'scripts' and fname.endswith('.pex'):
            result[fname] = rec
    return result


def extract_bsa_record(bsa_path: str, record: dict, out_path: str):
    """
    Extract one BSA record to out_path.
    Handles SSE v105 (LZ4) and v104 (zlib) compression.
    """
    version     = record['bsa_version']
    embed_names = record['embed_names']

    with open(bsa_path, 'rb') as f:
        f.seek(record['offset'])
        data = f.read(record['size'])

    if embed_names and data:
        skip = data[0] + 1
        data = data[skip:]

    if record['compressed'] and data:
        raw = data[4:]  # first 4 bytes are uncompressed size, not needed
        data = lz4.frame.decompress(raw) if version == 105 else zlib.decompress(raw)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(data)

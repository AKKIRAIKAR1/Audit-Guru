import io
import socket
import struct
from pathlib import Path

import boto3
from app.config import settings


def s3():
    cfg = settings()
    return boto3.client('s3', endpoint_url=cfg.s3_endpoint_url,
                        aws_access_key_id=cfg.s3_access_key or None,
                        aws_secret_access_key=cfg.s3_secret_key or None,
                        region_name=cfg.s3_region)


def local_path(key):
    root = Path(settings().local_storage_path).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Invalid object key')
    return path


def put(key, content, media_type):
    cfg = settings()
    if cfg.storage_backend == 's3':
        extra = {'ServerSideEncryption': cfg.s3_server_side_encryption} if cfg.s3_server_side_encryption else {}
        s3().put_object(Bucket=cfg.s3_bucket, Key=key, Body=content, ContentType=media_type, **extra)
    elif cfg.storage_backend == 'local':
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    else:
        raise ValueError('Unknown storage backend')


def get(key):
    cfg = settings()
    if cfg.storage_backend == 's3':
        response = s3().get_object(Bucket=cfg.s3_bucket, Key=key)
        with response['Body'] as stream:
            content = stream.read(cfg.max_file_mb * 1024 * 1024 + 1)
        return content
    return local_path(key).read_bytes()


def delete(key):
    if settings().storage_backend == 's3':
        s3().delete_object(Bucket=settings().s3_bucket, Key=key)
    else:
        local_path(key).unlink(missing_ok=True)


def scan(content):
    cfg = settings()
    if not cfg.clamav_host:
        return
    with socket.create_connection((cfg.clamav_host, cfg.clamav_port), timeout=30) as conn:
        conn.sendall(b'zINSTREAM\0')
        stream = io.BytesIO(content)
        while chunk := stream.read(65536):
            conn.sendall(struct.pack('!I', len(chunk)) + chunk)
        conn.sendall(struct.pack('!I', 0))
        reply = b''
        while len(reply) < 4096:
            chunk = conn.recv(4096)
            if not chunk:
                break
            reply += chunk
            if b'\0' in reply:
                break
        if not reply.rstrip(b'\0\n').endswith(b': OK'):
            raise ValueError('Document rejected by malware scanner')

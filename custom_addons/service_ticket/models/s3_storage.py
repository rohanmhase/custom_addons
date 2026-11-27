# -*- coding: utf-8 -*-
"""S3 archive for ticket photos.

Every uploaded image/PDF is pushed to the bucket and removed from Odoo, so the
database stays small. Only the link is kept (``svcticket.cloud.photo``).

Key format
    <prefix>/<TICKET>/<TICKET>_<kind>_<n>.<ext>

    prefix   -> svcticket.s3_prefix, default "service-tickets"
    TICKET   -> TKT-2026-00001
    kind     -> intake | resolution | payment | invoice
    n        -> 1, 2, 3 ... (per ticket per kind)

    e.g. service-tickets/TKT-2026-00001/TKT-2026-00001_intake_1.jpg
         service-tickets/TKT-2026-00001/TKT-2026-00001_resolution_2.pdf

Nothing here runs until the settings have a key, secret and bucket, so the
module works with local attachments out of the box.
"""
import base64
import logging
import re

_logger = logging.getLogger(__name__)

try:
    import boto3
except ImportError:  # boto3 is optional
    boto3 = None


def _param(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(key, default)


def _safe(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("_") or "file"


def get_config(env):
    """Return the S3 config dict, or None when archiving is off/incomplete."""
    if _param(env, "svcticket.s3_enabled", "1") not in ("1", "True", "true"):
        return None
    key = _param(env, "aws_access_key_id")
    secret = _param(env, "aws_secret_access_key")
    bucket = _param(env, "aws_s3_bucket_name")
    if not (key and secret and bucket):
        return None
    return {
        "key": key, "secret": secret, "bucket": bucket,
        "region": _param(env, "aws_s3_region_name", "ap-south-1"),
        "prefix": (_param(env, "svcticket.s3_prefix", "service-tickets") or "").strip("/"),
    }


def _client(cfg):
    return boto3.client("s3", aws_access_key_id=cfg["key"],
                        aws_secret_access_key=cfg["secret"], region_name=cfg["region"])


def build_key(cfg, ticket_name, kind, sequence, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else "jpg"
    name = "%s_%s_%d.%s" % (_safe(ticket_name), kind, sequence, _safe(ext))
    folder = "/".join(p for p in (cfg["prefix"], _safe(ticket_name)) if p)
    return "%s/%s" % (folder, name)


def upload_attachments(env, ticket, attachments, kind):
    """Move the given attachments to S3. Returns (uploaded, failed).

    Safe to call with archiving off - it just returns (0, 0).
    """
    cfg = get_config(env)
    if not cfg or boto3 is None or not attachments:
        return 0, 0

    CloudPhoto = env["svcticket.cloud.photo"].sudo()
    client = _client(cfg)
    already = CloudPhoto.search_count([("ticket_id", "=", ticket.id), ("kind", "=", kind)])
    uploaded, failed = 0, 0

    for index, att in enumerate(attachments, start=already + 1):
        if not att.datas:
            continue
        key = build_key(cfg, ticket.name, kind, index, att.name)
        try:
            client.put_object(
                Bucket=cfg["bucket"], Key=key,
                Body=base64.b64decode(att.datas),
                ContentType=att.mimetype or "application/octet-stream")
        except Exception:
            _logger.exception("S3 upload failed for %s (%s)", att.name, key)
            failed += 1
            continue
        CloudPhoto.create({
            "ticket_id": ticket.id, "kind": kind, "filename": att.name or key,
            "url": "https://%s.s3.%s.amazonaws.com/%s" % (cfg["bucket"], cfg["region"], key),
        })
        att.unlink()
        uploaded += 1

    _logger.info("S3 archive %s: %s uploaded, %s failed", ticket.name, uploaded, failed)
    return uploaded, failed


def test_connection(env):
    """Used by the Test S3 button in settings."""
    if boto3 is None:
        return False, "boto3 is not installed on the server (pip install boto3)."
    cfg = get_config(env)
    if not cfg:
        return False, "Fill in Access Key, Secret and Bucket first (and tick Archive photos)."
    try:
        client = _client(cfg)
        key = "%s/_connection-test.txt" % (cfg["prefix"] or "service-tickets")
        client.put_object(Bucket=cfg["bucket"], Key=key, Body=b"ok",
                          ContentType="text/plain")
        client.delete_object(Bucket=cfg["bucket"], Key=key)
        return True, "S3 works. Files will be stored as %s/<TICKET>/<TICKET>_<kind>_<n>.<ext>" % cfg["prefix"]
    except Exception as exc:
        return False, "S3 failed: %s" % exc

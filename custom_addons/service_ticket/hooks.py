# -*- coding: utf-8 -*-
"""One-time cleanups that must not run on every upgrade."""
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """1. sync locations, 2. drop the legacy 'SERVICE TIKIT' group category."""
    env["svcticket.location"].sync_all()
    cleanup_legacy_groups(env)


def cleanup_legacy_groups(env):
    """Remove the mis-spelled category + groups left by the old module.

    Guarded: if anything is missing this is a no-op.
    """
    try:
        category = env["ir.module.category"].sudo().search([("name", "=", "SERVICE TIKIT")])
        if not category:
            return
        groups = env["res.groups"].sudo().search([("category_id", "in", category.ids)])
        if groups:
            env["ir.model.access"].sudo().search([("group_id", "in", groups.ids)]).unlink()
            groups.unlink()
        category.unlink()
        _logger.info("Service Ticket: legacy SERVICE TIKIT groups removed.")
    except Exception:
        _logger.exception("Service Ticket: legacy group cleanup skipped.")
